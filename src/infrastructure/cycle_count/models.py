from sqlalchemy import Column, String, Integer, Boolean, DateTime
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
    is_blind_count = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
