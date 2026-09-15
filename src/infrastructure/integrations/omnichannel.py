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
        return True

    def ingest_orders(self) -> List[Dict[str, Any]]:
        return []

class AmazonSPAdapter(SalesChannelAdapter):
    def sync_inventory(self, sku: str, quantity: int) -> bool:
        # Mock API call to Amazon Selling Partner API
        return True

    def ingest_orders(self) -> List[Dict[str, Any]]:
        return []

class OmnichannelFramework:
    def __init__(self):
        self.adapters: Dict[str, SalesChannelAdapter] = {
            "shopify": ShopifyAdapter(),
            "amazon": AmazonSPAdapter()
        }

    def sync_all_channels(self, sku: str, quantity: int):
        for name, adapter in self.adapters.items():
            adapter.sync_inventory(sku, quantity)
