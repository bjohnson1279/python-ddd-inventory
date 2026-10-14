import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Boolean, DateTime, JSON, ForeignKey, UniqueConstraint, CheckConstraint, event
from src.infrastructure.database import Base


class ReportJob(Base):
    __tablename__ = 'python_report_jobs'
    __table_args__ = (UniqueConstraint('tenant_id', 'request_key'), CheckConstraint("status IN ('QUEUED','COMPLETED','FAILED')"),
        CheckConstraint("format IN ('json','csv')"), CheckConstraint('version >= 0 AND attempts >= 0'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    report_id = Column(String, ForeignKey('python_report_definitions.id'), nullable=False)
    requested_by = Column(String, ForeignKey('python_users.id'), nullable=False)
    request_key = Column(String, nullable=False)
    definition = Column(JSON, nullable=False)
    format = Column(String, nullable=False)
    status = Column(String, nullable=False, default='QUEUED')
    version = Column(Integer, nullable=False, default=0)
    attempts = Column(Integer, nullable=False, default=0)
    result = Column(JSON, nullable=True)
    error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime(timezone=True), nullable=True)


class ReportSchedule(Base):
    __tablename__ = 'python_report_schedules'
    __table_args__ = (CheckConstraint('version >= 0'), CheckConstraint("format IN ('json','csv')"))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    report_id = Column(String, ForeignKey('python_report_definitions.id'), nullable=False)
    created_by = Column(String, ForeignKey('python_users.id'), nullable=False)
    cron = Column(String, nullable=False)
    format = Column(String, nullable=False)
    next_run_at = Column(DateTime(timezone=True), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=0)
    error = Column(String, nullable=True)


class AlertConfig(Base):
    __tablename__ = 'python_alert_config'
    __table_args__ = (CheckConstraint('expiry_days >= 0 AND expiry_days <= 90 AND version >= 0'),)
    tenant_id = Column(String, ForeignKey('python_tenants.id'), primary_key=True)
    low_stock = Column(Boolean, nullable=False, default=False)
    expiry = Column(Boolean, nullable=False, default=False)
    expiry_days = Column(Integer, nullable=False, default=7)
    version = Column(Integer, nullable=False, default=0)


class AlertCondition(Base):
    __tablename__ = 'python_alert_conditions'
    __table_args__ = (UniqueConstraint('tenant_id', 'key'), CheckConstraint('episode > 0'))
    id = Column(String, primary_key=True)
    tenant_id = Column(String, ForeignKey('python_tenants.id'), nullable=False, index=True)
    key = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    active = Column(Boolean, nullable=False)
    episode = Column(Integer, nullable=False)
    facts = Column(JSON, nullable=False)
    evaluated_at = Column(DateTime(timezone=True), nullable=False)


class AlertNotice(Base):
    __tablename__ = 'python_alert_notices'
    __table_args__ = (UniqueConstraint('condition_id', 'episode', 'notification_id'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    condition_id = Column(String, ForeignKey('python_alert_conditions.id'), nullable=False)
    episode = Column(Integer, nullable=False)
    notification_id = Column(String, ForeignKey('python_notifications.id'), nullable=False, unique=True)
    resolved = Column(Boolean, nullable=False, default=False)


@event.listens_for(ReportJob.__table__, 'after_create')
def protect_job_snapshot(table, connection, **kwargs):
    if connection.dialect.name == 'sqlite':
        columns = ['id', 'tenant_id', 'report_id', 'requested_by', 'request_key', 'definition', 'format', 'created_at']
        changed = ' OR '.join('NEW.' + col + ' IS NOT OLD.' + col for col in columns)
        connection.exec_driver_sql("CREATE TRIGGER python_report_snapshot_update BEFORE UPDATE ON python_report_jobs WHEN OLD.status='COMPLETED' OR " + changed + " BEGIN SELECT RAISE(ABORT,'Report snapshot is immutable'); END")
        connection.exec_driver_sql("CREATE TRIGGER python_report_snapshot_delete BEFORE DELETE ON python_report_jobs BEGIN SELECT RAISE(ABORT,'Report history is immutable'); END")
    elif connection.dialect.name == 'postgresql':
        connection.exec_driver_sql("""CREATE OR REPLACE FUNCTION python_guard_report_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
            IF TG_OP='DELETE' OR OLD.status='COMPLETED' THEN RAISE EXCEPTION 'Report snapshot is immutable' USING ERRCODE='55000'; END IF;
            IF (to_jsonb(NEW)-ARRAY['version','attempts','result','status','error','completed_at']) IS DISTINCT FROM
               (to_jsonb(OLD)-ARRAY['version','attempts','result','status','error','completed_at']) THEN
               RAISE EXCEPTION 'Report request is immutable' USING ERRCODE='55000'; END IF;
            RETURN NEW; END $$""")
        connection.exec_driver_sql('CREATE TRIGGER python_report_snapshot BEFORE UPDATE OR DELETE ON python_report_jobs FOR EACH ROW EXECUTE FUNCTION python_guard_report_snapshot()')
