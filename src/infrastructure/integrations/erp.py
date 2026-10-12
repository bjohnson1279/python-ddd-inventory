from abc import ABC, abstractmethod
from typing import Dict, Any

class ERPAdapter(ABC):
    @abstractmethod
    def post_journal_entry(self, entry: Dict[str, Any]) -> bool:
        pass

class NetSuiteAdapter(ERPAdapter):
    def post_journal_entry(self, entry: Dict[str, Any]) -> bool:
        raise NotImplementedError('Direct provider adapter is not implemented; use a configured event destination')

class QuickBooksAdapter(ERPAdapter):
    def post_journal_entry(self, entry: Dict[str, Any]) -> bool:
        raise NotImplementedError('Direct provider adapter is not implemented; use a configured event destination')

class LogisticsAdapter(ABC):
    @abstractmethod
    def generate_shipping_label(self, order_id: str) -> str:
        pass

class FedExAdapter(LogisticsAdapter):
    def generate_shipping_label(self, order_id: str) -> str:
        raise NotImplementedError('Shipping label purchase is not implemented')
