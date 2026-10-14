from sqlalchemy import Column, String, Integer, BigInteger, Boolean, DateTime, JSON, UniqueConstraint
from src.infrastructure.database import Base
from datetime import datetime
import uuid

class CycleCountPlanModel(Base):
    __tablename__ = 'cycle_count_plans'

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False)
    name = Column(String, nullable=False)
    abc_classification = Column(String, nullable=False)
    frequency_days = Column(Integer, nullable=False)
    zone = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CycleCountRecordModel(Base):
    __tablename__ = 'cycle_count_records'

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False)
    plan_id = Column(String, nullable=True)
    name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    abc_classification = Column(String, nullable=False)
    zone = Column(String, nullable=True)
    assigned_operator_id = Column(String, nullable=True)
    location_id = Column(String, nullable=True)
    revision = Column(Integer, nullable=False, default=0, server_default='0')
    approved_by = Column(String, nullable=True)
    approval_result = Column(JSON, nullable=True)
    is_blind_count = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class CycleCountLineItemModel(Base):
    __tablename__ = 'cycle_count_line_items'

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    record_id = Column(String, nullable=False)
    sku = Column(String, nullable=False)
    expected_quantity = Column(Integer, nullable=False)
    stock_version = Column(Integer, nullable=True)
    counted_quantity = Column(Integer, nullable=True)
    variance_quantity = Column(Integer, nullable=True)
    variance_value = Column(BigInteger, nullable=True)
    status = Column(String, nullable=False, default='PENDING')


class CountRequestModel(Base):
    __tablename__ = 'python_count_requests'
    __table_args__ = (UniqueConstraint('tenant_id', 'mutation_id'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False)
    mutation_id = Column(String, nullable=False)
    request = Column(JSON, nullable=False)
    result = Column(JSON, nullable=False)
