from abc import ABC, abstractmethod
from typing import List, Dict, Any

class SalesChannelAdapter(ABC):
    @abstractmethod
    def sync_inventory(self, sku: str, quantity: int) -> bool:
        pass

    @abstractmethod
    def ingest_orders(self) -> List[Dict[str, Any]]:
        pass

class ShopifyAdapter(SalesChannelAdapter):
    def sync_inventory(self, sku: str, quantity: int) -> bool:
        # Mock API call to Shopify
        raise NotImplementedError('Direct provider adapter is not implemented; use a configured event destination')

    def ingest_orders(self) -> List[Dict[str, Any]]:
        raise NotImplementedError('Provider polling is not implemented; use signed order webhooks')

class AmazonSPAdapter(SalesChannelAdapter):
    def sync_inventory(self, sku: str, quantity: int) -> bool:
        # Mock API call to Amazon Selling Partner API
        raise NotImplementedError('Direct provider adapter is not implemented; use a configured event destination')

    def ingest_orders(self) -> List[Dict[str, Any]]:
        raise NotImplementedError('Provider polling is not implemented; use signed order webhooks')

class OmnichannelFramework:
    def __init__(self):
        self.adapters: Dict[str, SalesChannelAdapter] = {
            "shopify": ShopifyAdapter(),
            "amazon": AmazonSPAdapter()
        }

    def sync_all_channels(self, sku: str, quantity: int):
        for name, adapter in self.adapters.items():
            adapter.sync_inventory(sku, quantity)
