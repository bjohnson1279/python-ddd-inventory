"""Persisted workforce records; user and source ownership enforced by RLS."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, JSON, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, event
from src.infrastructure.database import Base

class OperatorProfile(Base):
    __tablename__ = 'python_operator_profiles'
    __table_args__ = (UniqueConstraint('tenant_id', 'user_id'), CheckConstraint('target_picks_per_hour > 0 AND max_hours > 0 AND max_hours <= 16 AND version >= 0'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, ForeignKey('python_tenants.id'), nullable=False, index=True)
    user_id = Column(String, ForeignKey('python_users.id'), nullable=False)
    target_picks_per_hour = Column(Float, nullable=False)
    max_hours = Column(Integer, nullable=False)
    certifications = Column(JSON, nullable=False)
    version = Column(Integer, nullable=False, default=0)

class LaborShift(Base):
    __tablename__ = 'python_labor_shifts'
    __table_args__ = (CheckConstraint("status IN ('PLANNED','ACTIVE','COMPLETED','CANCELLED')"), CheckConstraint('planned_end > planned_start AND version >= 0'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, ForeignKey('python_tenants.id'), nullable=False, index=True)
    operator_id = Column(String, ForeignKey('python_operator_profiles.id'), nullable=False)
    planned_start = Column(DateTime(timezone=True), nullable=False)
    planned_end = Column(DateTime(timezone=True), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    location_id = Column(String, nullable=False)
    required_certifications = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default='PLANNED')
    version = Column(Integer, nullable=False, default=0)

class TaskPerformance(Base):
    __tablename__ = 'python_task_performance'
    __table_args__ = (UniqueConstraint('tenant_id', 'kind', 'source_id'), CheckConstraint("kind IN ('PICK','COUNT')"), CheckConstraint("(kind='PICK' AND movement_id=source_id AND movement_id IS NOT NULL AND count_id IS NULL) OR (kind='COUNT' AND count_id=source_id AND count_id IS NOT NULL AND movement_id IS NULL)"), CheckConstraint('duration_seconds > 0 AND duration_seconds <= 57600'), CheckConstraint('distance_meters IS NULL OR distance_meters >= 0'), CheckConstraint('accurate_lines >= 0 AND total_lines >= accurate_lines'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, ForeignKey('python_tenants.id'), nullable=False, index=True)
    shift_id = Column(String, ForeignKey('python_labor_shifts.id'), nullable=False)
    kind = Column(String, nullable=False)
    source_id = Column(String, nullable=False)
    movement_id = Column(String, ForeignKey('python_stock_movements.id'), nullable=True)
    count_id = Column(String, ForeignKey('cycle_count_records.id'), nullable=True)
    duration_seconds = Column(Integer, nullable=False)
    distance_meters = Column(Float, nullable=True)
    accurate_lines = Column(Integer, nullable=False)
    total_lines = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

class StaffingPlan(Base):
    __tablename__ = 'python_staffing_plans'
    __table_args__ = (CheckConstraint("status IN ('DRAFT','PUBLISHED')"), CheckConstraint('version >= 0'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, ForeignKey('python_tenants.id'), nullable=False, index=True)
    target_date = Column(String, nullable=False)
    inputs = Column(JSON, nullable=False)
    recommendation = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default='DRAFT')
    version = Column(Integer, nullable=False, default=0)

@event.listens_for(TaskPerformance.__table__, 'after_create')
def immutable_activity(table, connection, **kwargs):
    if connection.dialect.name == 'sqlite':
        for operation in ('UPDATE', 'DELETE'):
            connection.exec_driver_sql(f"CREATE TRIGGER python_task_{operation.lower()} BEFORE {operation} ON python_task_performance BEGIN SELECT RAISE(ABORT,'Workforce activity is immutable'); END")
    elif connection.dialect.name == 'postgresql':
        connection.exec_driver_sql("""CREATE OR REPLACE FUNCTION python_guard_workforce_activity() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'Workforce activity is immutable' USING ERRCODE='55000'; END $$""")
        connection.exec_driver_sql('CREATE TRIGGER python_task_immutable BEFORE UPDATE OR DELETE ON python_task_performance FOR EACH ROW EXECUTE FUNCTION python_guard_workforce_activity()')
