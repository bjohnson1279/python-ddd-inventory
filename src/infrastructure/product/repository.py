from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from decimal import Decimal
from src.domain.product.entity import Product
from src.domain.product.value_objects import SKU, Money
from src.domain.product.repository import ProductRepository
from .models import ProductModel

class SQLAlchemyProductRepository(ProductRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    def _to_domain(self, model: ProductModel) -> Product:
        return Product(
            id=model.id,
            sku=SKU(model.sku),
            name=model.name,
            description=model.description,
            price=Money(Decimal(model.price_amount)),
            created_at=model.created_at,
            updated_at=model.updated_at
        )

    def _to_model(self, entity: Product) -> ProductModel:
        return ProductModel(
            id=entity.id,
            sku=entity.sku.value,
            name=entity.name,
            description=entity.description,
            price_amount=entity.price.amount,
            created_at=entity.created_at,
            updated_at=entity.updated_at
        )

    async def get_by_id(self, product_id: str) -> Optional[Product]:
        result = await self.session.execute(select(ProductModel).where(ProductModel.id == product_id))
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def get_by_sku(self, sku: str) -> Optional[Product]:
        result = await self.session.execute(select(ProductModel).where(ProductModel.sku == sku))
        model = result.scalar_one_or_none()
        return self._to_domain(model) if model else None

    async def save(self, product: Product) -> None:
        model = self._to_model(product)
        await self.session.merge(model)
        await self.session.commit()

    async def delete(self, product_id: str) -> None:
        result = await self.session.execute(select(ProductModel).where(ProductModel.id == product_id))
        model = result.scalar_one_or_none()
        if model:
            await self.session.delete(model)
            await self.session.commit()

    async def list_all(self, limit: int = 100, offset: int = 0) -> List[Product]:
        result = await self.session.execute(select(ProductModel).offset(offset).limit(limit))
        models = result.scalars().all()
        return [self._to_domain(m) for m in models]

