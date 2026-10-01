from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from datetime import datetime

class AnalyticsRepository(ABC):
    @abstractmethod
    async def get_inventory_levels_by_location(self, location_id: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def fetch_dispatch_history(self, location_id: str, since_date: datetime) -> List[Dict[str, Any]]:
        """Returns [{'sku': str, 'quantity': int, 'dispatched_at': datetime}, ...]"""
        pass

    @abstractmethod
    async def get_reorder_policies(self, location_id: str) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_active_forecasts(self, location_id: str) -> List[Dict[str, Any]]:
        pass
