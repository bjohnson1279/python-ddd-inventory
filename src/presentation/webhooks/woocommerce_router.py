from fastapi import APIRouter, Request, HTTPException, Header, Depends
from typing import Optional
import logging
import os

from src.infrastructure.woocommerce.woocommerce_webhook_security import WooCommerceWebhookSecurity
from src.application.integrations.channel_ingestion_service import ChannelIngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/woocommerce", tags=["webhooks", "woocommerce"])

# Dependencies
def get_woocommerce_security():
    api_secret = os.getenv("WOOCOMMERCE_API_SECRET", "")
    return WooCommerceWebhookSecurity(api_secret=api_secret)

def get_channel_ingestion_service():
    from src.application.integrations.channel_ingestion_service import ChannelIngestionService
    class MockDispatchUseCase:
        async def execute(self, sku, quantity, tenant_id, skip_publish_to_channel):
            pass
    return ChannelIngestionService(inventory_repository=None, dispatch_use_case=MockDispatchUseCase())

@router.post("/orders/create")
async def handle_order_created(
    request: Request,
    x_wc_webhook_signature: Optional[str] = Header(None),
    x_wc_webhook_topic: Optional[str] = Header(None),
    x_wc_webhook_source: Optional[str] = Header(None),
    security: WooCommerceWebhookSecurity = Depends(get_woocommerce_security),
    ingestion_service: ChannelIngestionService = Depends(get_channel_ingestion_service)
):
    if not x_wc_webhook_signature:
        raise HTTPException(status_code=401, detail="Missing Signature header")

    raw_body = await request.body()
    body_str = raw_body.decode('utf-8')
    
    if not body_str or not security.validate_hmac(body_str, x_wc_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")
        
    if x_wc_webhook_topic != "order.created":
        raise HTTPException(status_code=400, detail="Unsupported topic")
        
    order_payload = await request.json()
    line_items = order_payload.get("line_items", [])
    
    # Map woocommerce line_items to our internal representation if needed
    # WooCommerce line items usually have 'sku' and 'quantity'
    
    tenant_id = "default_tenant" 
    external_order_id = str(order_payload.get("id"))

    if not external_order_id or external_order_id == "None":
        raise HTTPException(status_code=400, detail="Missing order ID")

    try:
        await ingestion_service.ingest_order(
            channel_id="woocommerce",
            external_order_id=external_order_id,
            line_items=line_items,
            tenant_id=tenant_id
        )
        return {"status": "Webhook processed"}
    except Exception as e:
        logger.error(f"Error processing WooCommerce webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
