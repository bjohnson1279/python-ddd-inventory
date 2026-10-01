from abc import ABC, abstractmethod
from typing import Any

class BaseChannelAdapter(ABC):
    @abstractmethod
    async def sync_inventory(self) -> None:
        pass

    @abstractmethod
    async def ingest_order(self, payload: Any) -> None:
        pass

    @abstractmethod
    async def push_fulfillment_status(self, status: Any) -> None:
        pass
