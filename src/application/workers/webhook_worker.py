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
        import socket
        import ipaddress

        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise ValueError("Webhook URL must use HTTPS to prevent unencrypted sensitive data transmission")

        if not parsed.hostname:
            raise ValueError("Invalid Webhook URL: missing hostname")

        # Robust SSRF prevention
        try:
            addr_infos = socket.getaddrinfo(parsed.hostname, None)
            for addr in addr_infos:
                ip_str = addr[4][0]
                ip_obj = ipaddress.ip_address(ip_str)
                if (ip_obj.is_private or ip_obj.is_loopback or
                    ip_obj.is_link_local or ip_obj.is_unspecified or ip_obj.is_multicast):
                    raise ValueError("SSRF blocked: local or internal IP addresses are not allowed")
        except socket.gaierror:
            raise ValueError("Invalid Webhook URL: unable to resolve hostname")

        self._queue.append({
            "url": url,
            "payload": payload,
            "retries": 0,
            "max_retries": max_retries,
            "next_attempt_at": datetime.now(timezone.utc)
        })

    def _sign_payload(self, payload: str) -> str:
        return hmac.new(self.secret_key, payload.encode('utf-8'), hashlib.sha256).hexdigest()


    async def _deliver_webhook(self, client: httpx.AsyncClient, item: Dict[str, Any]):
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

    async def start(self):
        self._running = True
        logger.info("Webhook worker started.")
        async with httpx.AsyncClient() as client:
            while self._running:
                now = datetime.now(timezone.utc)

                # Bolt Optimization: O(N) queue reconstruction avoids O(N^2) list.remove() inside the loop
                to_process = []
                remaining_queue = []
                for item in self._queue:
                    if item["next_attempt_at"] <= now:
                        to_process.append(item)
                    else:
                        remaining_queue.append(item)
                self._queue = remaining_queue
                
                # Bolt Optimization: Use asyncio.gather to concurrently deliver webhooks
                # avoiding O(N) blocking network I/O loop.
                if to_process:
                    await asyncio.gather(*(self._deliver_webhook(client, item) for item in to_process))
                            
                await asyncio.sleep(2)

    async def stop(self):
        self._running = False
        logger.info("Webhook worker stopped.")
