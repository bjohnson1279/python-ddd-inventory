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
    # Legacy protocol validation remains, but tenantless dispatch is disabled.
    return None

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
        
    raise HTTPException(501, 'Register a tenant-owned signed integration webhook')
