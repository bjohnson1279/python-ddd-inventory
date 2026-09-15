from abc import ABC, abstractmethod
from typing import Optional, List
from .entity import Product

class ProductRepository(ABC):
    @abstractmethod
    async def get_by_id(self, product_id: str) -> Optional[Product]:
        pass

    @abstractmethod
    async def get_by_sku(self, sku: str) -> Optional[Product]:
        pass

    @abstractmethod
    async def save(self, product: Product) -> None:
        pass

    @abstractmethod
    async def delete(self, product_id: str) -> None:
        pass

    @abstractmethod
    async def list_all(self, limit: int = 100, offset: int = 0) -> List[Product]:
        pass

