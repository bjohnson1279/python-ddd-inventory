from typing import Optional, List
from src.domain.product.entity import Product
from src.domain.product.repository import ProductRepository
from src.domain.product.exceptions import ProductNotFoundError

class ProductService:
    def __init__(self, repository: ProductRepository):
        self.repository = repository

    async def create_product(self, sku: str, name: str, description: Optional[str], price_amount: str) -> Product:
        existing = await self.repository.get_by_sku(sku)
        if existing:
            raise ValueError(f'Product with SKU {sku} already exists')
        
        product = Product.create(sku=sku, name=name, description=description, price_amount=price_amount)
        await self.repository.save(product)
        return product

    async def get_product(self, product_id: str) -> Product:
        product = await self.repository.get_by_id(product_id)
        if not product:
            raise ProductNotFoundError(f'Product with id {product_id} not found')
        return product

    async def list_products(self, limit: int = 100, offset: int = 0) -> List[Product]:
        return await self.repository.list_all(limit, offset)

