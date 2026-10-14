import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, JSON, DateTime, UniqueConstraint
from src.infrastructure.database import Base


class SupplierModel(Base):
    __tablename__ = 'python_suppliers'
    __table_args__ = (UniqueConstraint('tenant_id', 'name'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    lead_time_days = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=0)


class PurchaseOrderModel(Base):
    __tablename__ = 'python_purchase_orders'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    supplier_id = Column(String, nullable=False)
    supplier_name = Column(String, nullable=False)
    location_id = Column(String, nullable=False)
    status = Column(String, nullable=False, default='DRAFT')
    version = Column(Integer, nullable=False, default=0)
    items = Column(JSON, nullable=False)
    created_by = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expected_delivery_date = Column(DateTime(timezone=True), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=True)


class ProcurementActionModel(Base):
    __tablename__ = 'python_procurement_actions'
    __table_args__ = (UniqueConstraint('tenant_id', 'mutation_id'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False)
    mutation_id = Column(String, nullable=False)
    actor_id = Column(String, nullable=False)
    request = Column(JSON, nullable=False)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class ReorderPolicyModel(Base):
    __tablename__ = 'python_reorder_policies'
    __table_args__ = (UniqueConstraint('tenant_id', 'sku', 'location_id'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    sku = Column(String, nullable=False)
    location_id = Column(String, nullable=False)
    supplier_id = Column(String, nullable=False)
    reorder_point = Column(Integer, nullable=False)
    target_stock = Column(Integer, nullable=False)
    safety_stock = Column(Integer, nullable=False, default=0)
    lead_time_days = Column(Integer, nullable=False)
    case_pack = Column(Integer, nullable=False, default=1)
    minimum_order = Column(Integer, nullable=False, default=1)
    unit_cost_cents = Column(Integer, nullable=True)
    version = Column(Integer, nullable=False, default=0)


class ASNModel(Base):
    __tablename__ = 'python_supplier_asns'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False)
    supplier_id = Column(String, nullable=False)
    po_id = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
