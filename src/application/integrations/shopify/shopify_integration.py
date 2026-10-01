from typing import Any
from src.application.integrations.types import BaseChannelAdapter
from src.infrastructure.shopify.shopify_client import ShopifyClient
from src.infrastructure.shopify.shopify_inventory_publisher import ShopifyInventoryPublisher

class ShopifyIntegration(BaseChannelAdapter):
    def __init__(self, shop_url: str, access_token: str, location_id: str):
        self.client = ShopifyClient(shop_url, access_token)
        self.publisher = ShopifyInventoryPublisher(self.client, location_id)

    async def sync_inventory(self) -> None:
        # To be implemented
        pass

    async def ingest_order(self, payload: Any) -> None:
        # Map payload and push to ChannelIngestionService
        pass

    async def push_fulfillment_status(self, status: Any) -> None:
        # Push tracking back to Shopify
        pass
