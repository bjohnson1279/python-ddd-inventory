from sqlalchemy import Column, String, Numeric, DateTime
from src.infrastructure.database import Base

class ProductModel(Base):
    __tablename__ = 'products'

    id = Column(String, primary_key=True)
    sku = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    price_amount = Column(Numeric(10, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

