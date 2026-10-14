from sqlalchemy import Column, String, Numeric, DateTime, UniqueConstraint
from src.infrastructure.database import Base

class ProductModel(Base):
    __tablename__ = 'products'
    __table_args__ = (UniqueConstraint('tenant_id', 'sku', name='products_tenant_sku_key'),)

    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price_amount = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

