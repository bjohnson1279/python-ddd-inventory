import asyncio
import logging
import httpx
import hmac
import hashlib
from typing import Dict, Any, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class WebhookDeliveryEngine:
    """Outbound Webhook Delivery Engine with signing and retries."""
    
    def __init__(self, secret_key: str):
        self.secret_key = secret_key.encode('utf-8')
        # In a real app, queue would be backed by Postgres or Redis
        self._queue: List[Dict[str, Any]] = []
        self._running = False

    def enqueue_webhook(self, url: str, payload: Dict[str, Any], max_retries: int = 3):
        import urllib.parse
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("Webhook URL must use HTTPS to prevent unencrypted sensitive data transmission")

        # Basic SSRF prevention
        if parsed.hostname in ("localhost", "127.0.0.1", "0.0.0.0") or (parsed.hostname and parsed.hostname.startswith("169.254.")):
            raise ValueError("SSRF blocked: local or internal IP addresses are not allowed")

        self._queue.append({
            "url": url,
            "payload": payload,
            "retries": 0,
            "max_retries": max_retries,
            "next_attempt_at": datetime.now(timezone.utc)
        })

    def _sign_payload(self, payload: str) -> str:
        return hmac.new(self.secret_key, payload.encode('utf-8'), hashlib.sha256).hexdigest()

    async def start(self):
        self._running = True
        logger.info("Webhook worker started.")
        async with httpx.AsyncClient() as client:
            async def process_item(item: Dict[str, Any]):
                payload_str = str(item["payload"])
                signature = self._sign_payload(payload_str)

                try:
                    response = await client.post(
                        item["url"],
                        json=item["payload"],
                        headers={"X-Webhook-Signature": signature}
                    )
                    response.raise_for_status()
                    logger.info(f"Webhook delivered successfully to {item['url']}")
                except Exception as e:
                    logger.error(f"Webhook delivery failed for {item['url']}: {e}")
                    item["retries"] += 1
                    if item["retries"] <= item["max_retries"]:
                        # Exponential backoff
                        delay_seconds = 2 ** item["retries"]
                        # For simplicity we just requeue at the back of the line without exact timestamp handling here
                        item["next_attempt_at"] = datetime.now(timezone.utc) # + timedelta in real app
                        self._queue.append(item)
                    else:
                        logger.error(f"Webhook exhausted retries for {item['url']}")

            while self._running:
                now = datetime.now(timezone.utc)
                to_process = [item for item in self._queue if item["next_attempt_at"] <= now]
                
                tasks = []
                for item in to_process:
                    self._queue.remove(item)
                    tasks.append(process_item(item))

                if tasks:
                    await asyncio.gather(*tasks)
                            
                await asyncio.sleep(2)

    async def stop(self):
        self._running = False
        logger.info("Webhook worker stopped.")
