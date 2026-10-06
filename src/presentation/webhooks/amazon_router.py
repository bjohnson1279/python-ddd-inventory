from fastapi import APIRouter, Request, HTTPException, Header, Depends
from typing import Optional, Tuple
import logging
import json

from src.infrastructure.amazon.amazon_notification_security import AmazonNotificationSecurity
from src.application.integrations.channel_ingestion_service import ChannelIngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/amazon", tags=["webhooks", "amazon"])

# Dependencies
def get_amazon_security():
    return AmazonNotificationSecurity(expected_topic_arn="arn:aws:sns:us-east-1:123456789012:amazon-sp-api-orders")

def get_channel_ingestion_service():
    from src.application.integrations.channel_ingestion_service import ChannelIngestionService
    class MockDispatchUseCase:
        async def execute(self, sku, quantity, tenant_id, skip_publish_to_channel):
            pass
    return ChannelIngestionService(inventory_repository=None, dispatch_use_case=MockDispatchUseCase())

def _parse_json_payload(body_str: str) -> dict:
    try:
        return json.loads(body_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

def _verify_sns_signature(payload: dict, body_str: str, security: AmazonNotificationSecurity) -> None:
    signature = payload.get("Signature")
    if not signature or not security.validate_sns_signature(body_str, signature):
        raise HTTPException(status_code=401, detail="Invalid SNS signature")

def _parse_order_change_notification(payload: dict) -> Optional[Tuple[str, list]]:
    message_str = payload.get("Message", "{}")
    try:
        message = json.loads(message_str)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid Message payload")

    notification_type = message.get("NotificationType")
    if notification_type != "ORDER_CHANGE":
        return None

    order_details = message.get("Payload", {}).get("OrderChangeNotification", {})
    external_order_id = order_details.get("AmazonOrderId")

    if not external_order_id:
        raise HTTPException(status_code=400, detail="Missing AmazonOrderId")

    line_items = order_details.get("OrderItems", [])
    return external_order_id, line_items

@router.post("/sns/notifications")
async def handle_amazon_notification(
    request: Request,
    x_amz_sns_message_type: Optional[str] = Header(None),
    security: AmazonNotificationSecurity = Depends(get_amazon_security),
    ingestion_service: ChannelIngestionService = Depends(get_channel_ingestion_service)
):
    if not x_amz_sns_message_type:
        raise HTTPException(status_code=400, detail="Missing SNS Message Type header")

    raw_body = await request.body()
    body_str = raw_body.decode('utf-8')
    payload = _parse_json_payload(body_str)

    if x_amz_sns_message_type == "SubscriptionConfirmation":
        # Handle SNS Subscription Confirmation
        # Usually requires HTTP GET to SubscribeURL
        logger.info(f"Received Subscription Confirmation: {payload.get('SubscribeURL')}")
        return {"status": "Subscription confirmed manually"}

    if x_amz_sns_message_type == "Notification":
        _verify_sns_signature(payload, body_str, security)

        parsed_order = _parse_order_change_notification(payload)
        if parsed_order is None:
            return {"status": "Ignored non-order notification"}

        external_order_id, line_items = parsed_order
        tenant_id = "default_tenant"

        try:
            await ingestion_service.ingest_order(
                channel_id="amazon",
                external_order_id=external_order_id,
                line_items=line_items,
                tenant_id=tenant_id
            )
            return {"status": "Webhook processed"}
        except Exception as e:
            logger.error(f"Error processing Amazon webhook: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    return {"status": "Unhandled message type"}
