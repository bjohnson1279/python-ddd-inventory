import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, JSON, DateTime, UniqueConstraint, CheckConstraint
from src.infrastructure.database import Base


class StockModel(Base):
    __tablename__ = 'python_stock'
    __table_args__ = (
        UniqueConstraint('tenant_id', 'sku', 'location_id'),
        CheckConstraint('quantity >= 0 AND allocated >= 0 AND allocated <= quantity'),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)
    location_id = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    allocated = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=0)


class StockMovementModel(Base):
    __tablename__ = 'python_stock_movements'
    __table_args__ = (UniqueConstraint('tenant_id', 'mutation_id'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    mutation_id = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    sku = Column(String, nullable=False)
    location_id = Column(String, nullable=False)
    operation = Column(String, nullable=False)
    amount = Column(Integer, nullable=False)
    request = Column(JSON, nullable=False)
    result = Column(JSON, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
