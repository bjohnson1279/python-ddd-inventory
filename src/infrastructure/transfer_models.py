import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, JSON, DateTime, UniqueConstraint, CheckConstraint
from src.infrastructure.database import Base


class TransferModel(Base):
    __tablename__ = 'python_stock_transfers'
    __table_args__ = (CheckConstraint('source_location_id <> destination_location_id'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    source_location_id = Column(String, nullable=False)
    destination_location_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default='DRAFT')
    version = Column(Integer, nullable=False, default=0)
    items = Column(JSON, nullable=False)
    reference = Column(String, nullable=True)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)


class InventoryActionModel(Base):
    __tablename__ = 'python_inventory_actions'
    __table_args__ = (UniqueConstraint('tenant_id', 'mutation_id'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    mutation_id = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    request = Column(JSON, nullable=False)
    result = Column(JSON, nullable=False)


class UomConfigModel(Base):
    __tablename__ = 'python_uom_configs'
    __table_args__ = (UniqueConstraint('tenant_id', 'sku'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)
    base_unit = Column(String, nullable=False)
    category = Column(String, nullable=False)
    purchase_unit = Column(String, nullable=False)
    sale_unit = Column(String, nullable=False)
    rules = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=0)
