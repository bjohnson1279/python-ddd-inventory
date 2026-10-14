import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, JSON, DateTime, UniqueConstraint, CheckConstraint
from src.infrastructure.database import Base


class ReturnModel(Base):
    __tablename__ = 'python_returns'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    source_movement_id = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    supplier_id = Column(String, nullable=True)
    status = Column(String, nullable=False, default='REQUESTED')
    version = Column(Integer, nullable=False, default=0)
    reason = Column(String, nullable=False)
    refund_reference = Column(String, nullable=True)
    items = Column(JSON, nullable=False)
    created_by = Column(String, nullable=False)
    authorized_by = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ReturnActionModel(Base):
    __tablename__ = 'python_return_actions'
    __table_args__ = (UniqueConstraint('tenant_id', 'mutation_id'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False)
    mutation_id = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    request = Column(JSON, nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class QuarantineModel(Base):
    __tablename__ = 'python_return_quarantine'
    __table_args__ = (CheckConstraint('quantity >= 0'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False)
    return_id = Column(String, nullable=False)
    stock_id = Column(String, nullable=False)
    sku = Column(String, nullable=False)
    location_id = Column(String, nullable=False)
    quantity = Column(Integer, nullable=False)
    reason = Column(String, nullable=False)
    layer_ids = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default='QUARANTINED')
    version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
