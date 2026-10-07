import os
from fastapi import APIRouter, Request, HTTPException, Header, Depends
from typing import Optional
import logging

from src.infrastructure.shopify.shopify_webhook_security import ShopifyWebhookSecurity
from src.application.integrations.channel_ingestion_service import ChannelIngestionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/shopify", tags=["webhooks", "shopify"])

# In a real app, these would be injected via dependencies
def get_webhook_security():
    api_secret = os.getenv("SHOPIFY_API_SECRET", "")
    return ShopifyWebhookSecurity(api_secret=api_secret)

def get_channel_ingestion_service():
    # Example mock dependency injection
    class MockDispatchUseCase:
        async def execute(self, sku, quantity, tenant_id, skip_publish_to_channel):
            pass
    return ChannelIngestionService(inventory_repository=None, dispatch_use_case=MockDispatchUseCase())

@router.post("/orders/create")
async def handle_order_created(
    request: Request,
    x_shopify_hmac_sha256: Optional[str] = Header(None),
    x_shopify_topic: Optional[str] = Header(None),
    x_shopify_webhook_id: Optional[str] = Header(None),
    security: ShopifyWebhookSecurity = Depends(get_webhook_security),
    ingestion_service: ChannelIngestionService = Depends(get_channel_ingestion_service)
):
    if not x_shopify_hmac_sha256:
        raise HTTPException(status_code=401, detail="Missing HMAC header")
        
    if not x_shopify_webhook_id:
        raise HTTPException(status_code=400, detail="Missing Webhook ID header")

    raw_body = await request.body()
    body_str = raw_body.decode('utf-8')
    
    if not body_str or not security.validate_hmac(body_str, x_shopify_hmac_sha256):
        raise HTTPException(status_code=401, detail="Invalid HMAC signature")
        
    if x_shopify_topic != "orders/create":
        raise HTTPException(status_code=400, detail="Unsupported topic")
        
    # We should normally check if this webhook was already processed to be idempotent
    # if await processed_webhook_repo.exists(x_shopify_webhook_id):
    #     return {"status": "already processed"}

    order_payload = await request.json()
    line_items = order_payload.get("line_items", [])
    
    # Normally tenant_id would be resolved from the shop domain or webhook headers
    tenant_id = "default_tenant" 
    external_order_id = str(order_payload.get("id", x_shopify_webhook_id))

    try:
        await ingestion_service.ingest_order(
            channel_id="shopify",
            external_order_id=external_order_id,
            line_items=line_items,
            tenant_id=tenant_id
        )
        return {"status": "Webhook processed"}
    except Exception as e:
        logger.error(f"Error processing Shopify webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")
