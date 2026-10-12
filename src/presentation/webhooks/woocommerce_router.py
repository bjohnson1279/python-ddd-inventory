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
    # Legacy protocol validation remains, but tenantless dispatch is disabled.
    return None

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
        
    raise HTTPException(501, 'Register a tenant-owned signed integration webhook')
