import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, BigInteger, DateTime, Boolean, CheckConstraint, ForeignKey
from src.infrastructure.database import Base


class AccountingConfigModel(Base):
    __tablename__ = 'python_accounting_config'
    tenant_id = Column(String, primary_key=True)
    costing_method = Column(String, nullable=False, default='FIFO')
    accounting_method = Column(String, nullable=False, default='ACCRUAL')
    currency = Column(String, nullable=False, default='USD')
    version = Column(Integer, nullable=False, default=0)


class CostLayerModel(Base):
    __tablename__ = 'python_cost_layers'
    __table_args__ = (
        CheckConstraint('initial_quantity >= 0 AND remaining_quantity >= 0 AND remaining_quantity <= initial_quantity'),
        CheckConstraint('unit_cost_cents IS NULL OR unit_cost_cents >= 0'),
        CheckConstraint('remaining_value_cents IS NULL OR remaining_value_cents >= 0'),
    )
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    stock_id = Column(String, ForeignKey('python_stock.id'), nullable=False, index=True)
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)
    location_id = Column(String, nullable=False)
    initial_quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False)
    unit_cost_cents = Column(BigInteger, nullable=True)
    remaining_value_cents = Column(BigInteger, nullable=True)
    lot_number = Column(String, nullable=True)
    expiration_date = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    quarantined = Column(Boolean, nullable=False, default=False)
