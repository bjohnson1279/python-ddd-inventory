import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.infrastructure.database import Base
# Import dependent identity and owned feature models to satisfy foreign key relationships
import src.infrastructure.auth.identity
import src.infrastructure.owned_feature_models
from src.infrastructure.report_alert_models import (
    ReportJob,
    ReportSchedule,
    AlertConfig,
    AlertCondition,
    AlertNotice,
    protect_job_snapshot,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_report_job_creation_and_defaults(db_session):
    job = ReportJob(
        tenant_id="tenant-1",
        report_id="rep-1",
        requested_by="user-1",
        request_key="req-key-1",
        definition={"report_type": "STOCK"},
        format="json",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    assert job.id is not None
    assert isinstance(job.id, str)
    assert job.status == "QUEUED"
    assert job.version == 0
    assert job.attempts == 0
    assert job.result is None
    assert job.error is None
    assert job.created_at is not None
    assert job.completed_at is None


def test_report_job_check_constraint_status(db_session):
    job = ReportJob(
        tenant_id="tenant-1",
        report_id="rep-1",
        requested_by="user-1",
        request_key="req-key-invalid-status",
        definition={},
        format="json",
        status="INVALID_STATUS",
    )
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_report_job_check_constraint_format(db_session):
    job = ReportJob(
        tenant_id="tenant-1",
        report_id="rep-1",
        requested_by="user-1",
        request_key="req-key-invalid-format",
        definition={},
        format="xml",
    )
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_report_job_check_constraint_negative_version(db_session):
    job = ReportJob(
        tenant_id="tenant-1",
        report_id="rep-1",
        requested_by="user-1",
        request_key="req-key-neg-version",
        definition={},
        format="json",
        version=-1,
    )
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_report_job_check_constraint_negative_attempts(db_session):
    job = ReportJob(
        tenant_id="tenant-1",
        report_id="rep-1",
        requested_by="user-1",
        request_key="req-key-neg-attempts",
        definition={},
        format="json",
        attempts=-1,
    )
    db_session.add(job)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_report_job_unique_constraint_tenant_and_request_key(db_session):
    job1 = ReportJob(
        tenant_id="tenant-1",
        report_id="rep-1",
        requested_by="user-1",
        request_key="unique-key",
        definition={},
        format="json",
    )
    db_session.add(job1)
    db_session.commit()

    job2 = ReportJob(
        tenant_id="tenant-1",
        report_id="rep-2",
        requested_by="user-2",
        request_key="unique-key",
        definition={},
        format="csv",
    )
    db_session.add(job2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_report_job_triggers_snapshot_immutability(db_session):
    job = ReportJob(
        tenant_id="tenant-1",
        report_id="rep-1",
        requested_by="user-1",
        request_key="immutable-job",
        definition={"metrics": ["stock"]},
        format="json",
    )
    db_session.add(job)
    db_session.commit()

    # Modifying snapshot definition when QUEUED must be prevented by the SQLite trigger
    job.definition = {"metrics": ["sales"]}
    with pytest.raises(DBAPIError, match="Report snapshot is immutable"):
        db_session.commit()
    db_session.rollback()

    # Updating mutable fields (attempts, version, status, result, error, completed_at) should succeed
    job = db_session.get(ReportJob, job.id)
    job.attempts = 1
    job.version = 1
    job.status = "COMPLETED"
    job.result = {"data": [1, 2, 3]}
    job.completed_at = datetime.now(timezone.utc)
    db_session.commit()

    # Once status is COMPLETED, ANY update must be prevented by the trigger
    job.attempts = 2
    with pytest.raises(DBAPIError, match="Report snapshot is immutable"):
        db_session.commit()
    db_session.rollback()

    # Deleting a job must also be prevented by the trigger
    job = db_session.get(ReportJob, job.id)
    db_session.delete(job)
    with pytest.raises(DBAPIError, match="Report history is immutable"):
        db_session.commit()
    db_session.rollback()


def test_report_schedule_creation_and_defaults(db_session):
    schedule = ReportSchedule(
        tenant_id="tenant-1",
        report_id="rep-1",
        created_by="user-1",
        cron="0 0 * * *",
        format="json",
        next_run_at=datetime.now(timezone.utc),
    )
    db_session.add(schedule)
    db_session.commit()
    db_session.refresh(schedule)

    assert schedule.id is not None
    assert isinstance(schedule.id, str)
    assert schedule.active is True
    assert schedule.version == 0
    assert schedule.error is None


def test_report_schedule_check_constraints(db_session):
    # Invalid format constraint
    bad_format = ReportSchedule(
        tenant_id="tenant-1",
        report_id="rep-1",
        created_by="user-1",
        cron="0 0 * * *",
        format="yaml",
        next_run_at=datetime.now(timezone.utc),
    )
    db_session.add(bad_format)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Negative version constraint
    bad_version = ReportSchedule(
        tenant_id="tenant-1",
        report_id="rep-1",
        created_by="user-1",
        cron="0 0 * * *",
        format="csv",
        next_run_at=datetime.now(timezone.utc),
        version=-1,
    )
    db_session.add(bad_version)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_alert_config_creation_and_defaults(db_session):
    config = AlertConfig(tenant_id="tenant-alert-1")
    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)

    assert config.tenant_id == "tenant-alert-1"
    assert config.low_stock is False
    assert config.expiry is False
    assert config.expiry_days == 7
    assert config.version == 0


def test_alert_config_check_constraints(db_session):
    # expiry_days < 0
    bad_days_low = AlertConfig(tenant_id="t-low", expiry_days=-1)
    db_session.add(bad_days_low)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # expiry_days > 90
    bad_days_high = AlertConfig(tenant_id="t-high", expiry_days=91)
    db_session.add(bad_days_high)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # version < 0
    bad_version = AlertConfig(tenant_id="t-ver", version=-1)
    db_session.add(bad_version)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_alert_condition_creation_and_constraints(db_session):
    condition = AlertCondition(
        id="cond-1",
        tenant_id="tenant-1",
        key="sku-low-stock-ABC",
        kind="LOW_STOCK",
        active=True,
        episode=1,
        facts={"sku": "ABC", "threshold": 5},
        evaluated_at=datetime.now(timezone.utc),
    )
    db_session.add(condition)
    db_session.commit()
    db_session.refresh(condition)

    assert condition.id == "cond-1"
    assert condition.episode == 1
    assert condition.facts["sku"] == "ABC"

    # episode <= 0 check constraint
    bad_episode = AlertCondition(
        id="cond-2",
        tenant_id="tenant-1",
        key="sku-low-stock-DEF",
        kind="LOW_STOCK",
        active=True,
        episode=0,
        facts={},
        evaluated_at=datetime.now(timezone.utc),
    )
    db_session.add(bad_episode)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # unique constraint on (tenant_id, key)
    dup_key = AlertCondition(
        id="cond-3",
        tenant_id="tenant-1",
        key="sku-low-stock-ABC",
        kind="LOW_STOCK",
        active=False,
        episode=2,
        facts={},
        evaluated_at=datetime.now(timezone.utc),
    )
    db_session.add(dup_key)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_alert_notice_creation_and_constraints(db_session):
    notice = AlertNotice(
        tenant_id="tenant-1",
        condition_id="cond-1",
        episode=1,
        notification_id="notif-1",
        resolved=False,
    )
    db_session.add(notice)
    db_session.commit()
    db_session.refresh(notice)

    assert notice.id is not None
    assert isinstance(notice.id, str)
    assert notice.resolved is False

    # Unique constraint on notification_id
    dup_notif = AlertNotice(
        tenant_id="tenant-1",
        condition_id="cond-2",
        episode=1,
        notification_id="notif-1",
    )
    db_session.add(dup_notif)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_protect_job_snapshot_dialects():
    # SQLite dialect test
    sqlite_conn = MagicMock()
    sqlite_conn.dialect.name = "sqlite"
    protect_job_snapshot(ReportJob.__table__, sqlite_conn)
    assert sqlite_conn.exec_driver_sql.call_count == 2
    executed_sqls = [call.args[0] for call in sqlite_conn.exec_driver_sql.call_args_list]
    assert any("CREATE TRIGGER python_report_snapshot_update" in sql for sql in executed_sqls)
    assert any("CREATE TRIGGER python_report_snapshot_delete" in sql for sql in executed_sqls)

    # PostgreSQL dialect test
    pg_conn = MagicMock()
    pg_conn.dialect.name = "postgresql"
    protect_job_snapshot(ReportJob.__table__, pg_conn)
    assert pg_conn.exec_driver_sql.call_count == 2
    executed_pg_sqls = [call.args[0] for call in pg_conn.exec_driver_sql.call_args_list]
    assert any("CREATE OR REPLACE FUNCTION python_guard_report_snapshot()" in sql for sql in executed_pg_sqls)
    assert any("CREATE TRIGGER python_report_snapshot" in sql for sql in executed_pg_sqls)

    # Other dialects should not execute DDL
    mysql_conn = MagicMock()
    mysql_conn.dialect.name = "mysql"
    protect_job_snapshot(ReportJob.__table__, mysql_conn)
    assert mysql_conn.exec_driver_sql.call_count == 0
