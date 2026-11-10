import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import pytest
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from src.infrastructure.database import Base
from src.infrastructure.auth.identity import TenantModel, UserModel
from src.infrastructure.stock_models import StockMovementModel
from src.infrastructure.cycle_count.models import CycleCountRecordModel
from src.infrastructure.workforce_models import (
    OperatorProfile,
    LaborShift,
    TaskPerformance,
    StaffingPlan,
    immutable_activity,
)


def test_immutable_activity_mock_sqlite():
    table = MagicMock()
    connection = MagicMock()
    connection.dialect.name = 'sqlite'

    immutable_activity(table, connection)

    assert connection.exec_driver_sql.call_count == 2
    calls = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert any('CREATE TRIGGER python_task_update BEFORE UPDATE ON python_task_performance' in c for c in calls)
    assert any('CREATE TRIGGER python_task_delete BEFORE DELETE ON python_task_performance' in c for c in calls)


def test_immutable_activity_mock_postgresql():
    table = MagicMock()
    connection = MagicMock()
    connection.dialect.name = 'postgresql'

    immutable_activity(table, connection)

    assert connection.exec_driver_sql.call_count == 2
    calls = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert any('CREATE OR REPLACE FUNCTION python_guard_workforce_activity()' in c for c in calls)
    assert any('CREATE TRIGGER python_task_immutable BEFORE UPDATE OR DELETE' in c for c in calls)


def test_immutable_activity_mock_unsupported_dialect():
    table = MagicMock()
    connection = MagicMock()
    connection.dialect.name = 'mysql'

    immutable_activity(table, connection)

    connection.exec_driver_sql.assert_not_called()


@pytest.fixture
async def async_session_factory():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:', echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    yield session_maker
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def seeded_entities(async_session_factory):
    async with async_session_factory() as session:
        tenant = TenantModel(id='t-test-1', name='Test Store')
        user = UserModel(
            id='u-test-1',
            tenant_id='t-test-1',
            email='operator@example.com',
            name='Operator 1',
            role='WAREHOUSE_OPERATOR',
            password_hash='hash123',
            is_active=True,
        )
        movement = StockMovementModel(
            id='mov-test-1',
            tenant_id='t-test-1',
            mutation_id='mut-1',
            actor_id='u-test-1',
            sku='SKU-1',
            location_id='A1',
            operation='DISPATCH',
            amount=10,
            request={},
            result={},
        )
        count_rec = CycleCountRecordModel(
            id='cnt-test-1',
            tenant_id='t-test-1',
            name='Count 1',
            status='COMPLETED',
            abc_classification='A',
        )
        session.add_all([tenant, user, movement, count_rec])
        await session.commit()
    return {
        'tenant_id': 't-test-1',
        'user_id': 'u-test-1',
        'movement_id': 'mov-test-1',
        'count_id': 'cnt-test-1',
    }


@pytest.mark.asyncio
async def test_operator_profile_defaults_and_persistence(async_session_factory, seeded_entities):
    async with async_session_factory() as session:
        profile = OperatorProfile(
            tenant_id=seeded_entities['tenant_id'],
            user_id=seeded_entities['user_id'],
            target_picks_per_hour=45.5,
            max_hours=8,
            certifications=['forklift', 'reach_truck'],
        )
        session.add(profile)
        await session.commit()

        assert profile.id is not None
        assert isinstance(uuid.UUID(profile.id), uuid.UUID)
        assert profile.version == 0
        assert profile.target_picks_per_hour == 45.5
        assert profile.max_hours == 8
        assert profile.certifications == ['forklift', 'reach_truck']

        fetched = await session.get(OperatorProfile, profile.id)
        assert fetched is not None
        assert fetched.tenant_id == seeded_entities['tenant_id']
        assert fetched.user_id == seeded_entities['user_id']


@pytest.mark.asyncio
async def test_operator_profile_unique_tenant_user_constraint(async_session_factory, seeded_entities):
    async with async_session_factory() as session:
        p1 = OperatorProfile(
            tenant_id=seeded_entities['tenant_id'],
            user_id=seeded_entities['user_id'],
            target_picks_per_hour=30.0,
            max_hours=8,
            certifications=[],
        )
        session.add(p1)
        await session.commit()

        p2 = OperatorProfile(
            tenant_id=seeded_entities['tenant_id'],
            user_id=seeded_entities['user_id'],
            target_picks_per_hour=40.0,
            max_hours=8,
            certifications=[],
        )
        session.add(p2)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('target_picks,max_hours,version', [
    (0.0, 8, 0),       # target_picks <= 0
    (-5.0, 8, 0),      # target_picks < 0
    (50.0, 0, 0),      # max_hours <= 0
    (50.0, 17, 0),     # max_hours > 16
    (50.0, -1, 0),     # max_hours < 0
    (50.0, 8, -1),     # version < 0
])
async def test_operator_profile_check_constraints(async_session_factory, seeded_entities, target_picks, max_hours, version):
    async with async_session_factory() as session:
        profile = OperatorProfile(
            tenant_id=seeded_entities['tenant_id'],
            user_id=seeded_entities['user_id'],
            target_picks_per_hour=target_picks,
            max_hours=max_hours,
            certifications=[],
            version=version,
        )
        session.add(profile)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_labor_shift_defaults_and_persistence(async_session_factory, seeded_entities):
    async with async_session_factory() as session:
        profile = OperatorProfile(
            tenant_id=seeded_entities['tenant_id'],
            user_id=seeded_entities['user_id'],
            target_picks_per_hour=50.0,
            max_hours=8,
            certifications=['forklift'],
        )
        session.add(profile)
        await session.commit()

        now = datetime.now(timezone.utc)
        shift = LaborShift(
            tenant_id=seeded_entities['tenant_id'],
            operator_id=profile.id,
            planned_start=now,
            planned_end=now + timedelta(hours=8),
            location_id='WH-ZONE-A',
            required_certifications=['forklift'],
        )
        session.add(shift)
        await session.commit()

        assert shift.id is not None
        assert isinstance(uuid.UUID(shift.id), uuid.UUID)
        assert shift.status == 'PLANNED'
        assert shift.version == 0
        assert shift.started_at is None
        assert shift.ended_at is None

        fetched = await session.get(LaborShift, shift.id)
        assert fetched is not None
        assert fetched.location_id == 'WH-ZONE-A'
        assert fetched.operator_id == profile.id


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['PLANNED', 'ACTIVE', 'COMPLETED', 'CANCELLED'])
async def test_labor_shift_valid_statuses(async_session_factory, seeded_entities, status):
    async with async_session_factory() as session:
        profile = OperatorProfile(
            tenant_id=seeded_entities['tenant_id'],
            user_id=seeded_entities['user_id'],
            target_picks_per_hour=50.0,
            max_hours=8,
            certifications=[],
        )
        session.add(profile)
        await session.commit()

        now = datetime.now(timezone.utc)
        shift = LaborShift(
            tenant_id=seeded_entities['tenant_id'],
            operator_id=profile.id,
            planned_start=now,
            planned_end=now + timedelta(hours=4),
            location_id='DEFAULT',
            required_certifications=[],
            status=status,
        )
        session.add(shift)
        await session.commit()
        assert shift.status == status


@pytest.mark.asyncio
@pytest.mark.parametrize('status,start_delta,end_delta,version', [
    ('INVALID_STATUS', 0, 4, 0),   # Invalid status
    ('PLANNED', 4, 4, 0),          # planned_end == planned_start
    ('PLANNED', 4, 2, 0),          # planned_end < planned_start
    ('PLANNED', 0, 4, -1),         # version < 0
])
async def test_labor_shift_check_constraints(async_session_factory, seeded_entities, status, start_delta, end_delta, version):
    async with async_session_factory() as session:
        profile = OperatorProfile(
            tenant_id=seeded_entities['tenant_id'],
            user_id=seeded_entities['user_id'],
            target_picks_per_hour=50.0,
            max_hours=8,
            certifications=[],
        )
        session.add(profile)
        await session.commit()

        now = datetime.now(timezone.utc)
        shift = LaborShift(
            tenant_id=seeded_entities['tenant_id'],
            operator_id=profile.id,
            planned_start=now + timedelta(hours=start_delta),
            planned_end=now + timedelta(hours=end_delta),
            location_id='DEFAULT',
            required_certifications=[],
            status=status,
            version=version,
        )
        session.add(shift)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.fixture
async def seeded_shift(async_session_factory, seeded_entities):
    async with async_session_factory() as session:
        profile = OperatorProfile(
            tenant_id=seeded_entities['tenant_id'],
            user_id=seeded_entities['user_id'],
            target_picks_per_hour=60.0,
            max_hours=8,
            certifications=[],
        )
        session.add(profile)
        await session.commit()

        now = datetime.now(timezone.utc)
        shift = LaborShift(
            tenant_id=seeded_entities['tenant_id'],
            operator_id=profile.id,
            planned_start=now,
            planned_end=now + timedelta(hours=8),
            location_id='DEFAULT',
            required_certifications=[],
            status='ACTIVE',
        )
        session.add(shift)
        await session.commit()
        shift_id = shift.id
    return shift_id


@pytest.mark.asyncio
async def test_task_performance_pick_success(async_session_factory, seeded_entities, seeded_shift):
    async with async_session_factory() as session:
        task = TaskPerformance(
            tenant_id=seeded_entities['tenant_id'],
            shift_id=seeded_shift,
            kind='PICK',
            source_id=seeded_entities['movement_id'],
            movement_id=seeded_entities['movement_id'],
            count_id=None,
            duration_seconds=120,
            distance_meters=15.5,
            accurate_lines=5,
            total_lines=5,
        )
        session.add(task)
        await session.commit()

        assert task.id is not None
        assert isinstance(uuid.UUID(task.id), uuid.UUID)
        assert task.created_at is not None
        assert task.duration_seconds == 120
        assert task.distance_meters == 15.5


@pytest.mark.asyncio
async def test_task_performance_count_success(async_session_factory, seeded_entities, seeded_shift):
    async with async_session_factory() as session:
        task = TaskPerformance(
            tenant_id=seeded_entities['tenant_id'],
            shift_id=seeded_shift,
            kind='COUNT',
            source_id=seeded_entities['count_id'],
            movement_id=None,
            count_id=seeded_entities['count_id'],
            duration_seconds=300,
            distance_meters=None,
            accurate_lines=8,
            total_lines=10,
        )
        session.add(task)
        await session.commit()

        assert task.id is not None
        assert task.kind == 'COUNT'
        assert task.distance_meters is None
        assert task.accurate_lines == 8
        assert task.total_lines == 10


@pytest.mark.asyncio
async def test_task_performance_unique_constraint(async_session_factory, seeded_entities, seeded_shift):
    async with async_session_factory() as session:
        t1 = TaskPerformance(
            tenant_id=seeded_entities['tenant_id'],
            shift_id=seeded_shift,
            kind='PICK',
            source_id=seeded_entities['movement_id'],
            movement_id=seeded_entities['movement_id'],
            count_id=None,
            duration_seconds=60,
            accurate_lines=1,
            total_lines=1,
        )
        session.add(t1)
        await session.commit()

        t2 = TaskPerformance(
            tenant_id=seeded_entities['tenant_id'],
            shift_id=seeded_shift,
            kind='PICK',
            source_id=seeded_entities['movement_id'],
            movement_id=seeded_entities['movement_id'],
            count_id=None,
            duration_seconds=90,
            accurate_lines=1,
            total_lines=1,
        )
        session.add(t2)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,source_id,movement_id,count_id,duration,distance,accurate,total', [
    # Invalid kind
    ('PACK', 'mov-1', 'mov-1', None, 60, 5.0, 1, 1),
    # PICK with mismatching movement_id != source_id
    ('PICK', 'mov-test-1', 'other-mov', None, 60, 5.0, 1, 1),
    # PICK with movement_id None
    ('PICK', 'mov-test-1', None, None, 60, 5.0, 1, 1),
    # PICK with count_id not None
    ('PICK', 'mov-test-1', 'mov-test-1', 'cnt-test-1', 60, 5.0, 1, 1),
    # COUNT with mismatching count_id != source_id
    ('COUNT', 'cnt-test-1', None, 'other-cnt', 60, 5.0, 1, 1),
    # COUNT with count_id None
    ('COUNT', 'cnt-test-1', None, None, 60, 5.0, 1, 1),
    # COUNT with movement_id not None
    ('COUNT', 'cnt-test-1', 'mov-test-1', 'cnt-test-1', 60, 5.0, 1, 1),
    # duration_seconds <= 0
    ('PICK', 'mov-test-1', 'mov-test-1', None, 0, 5.0, 1, 1),
    ('PICK', 'mov-test-1', 'mov-test-1', None, -10, 5.0, 1, 1),
    # duration_seconds > 57600 (16 hours)
    ('PICK', 'mov-test-1', 'mov-test-1', None, 57601, 5.0, 1, 1),
    # distance_meters < 0
    ('PICK', 'mov-test-1', 'mov-test-1', None, 60, -0.1, 1, 1),
    # accurate_lines < 0
    ('PICK', 'mov-test-1', 'mov-test-1', None, 60, 5.0, -1, 1),
    # total_lines < accurate_lines
    ('PICK', 'mov-test-1', 'mov-test-1', None, 60, 5.0, 5, 4),
])
async def test_task_performance_check_constraints(
    async_session_factory, seeded_entities, seeded_shift,
    kind, source_id, movement_id, count_id, duration, distance, accurate, total
):
    async with async_session_factory() as session:
        task = TaskPerformance(
            tenant_id=seeded_entities['tenant_id'],
            shift_id=seeded_shift,
            kind=kind,
            source_id=source_id,
            movement_id=movement_id,
            count_id=count_id,
            duration_seconds=duration,
            distance_meters=distance,
            accurate_lines=accurate,
            total_lines=total,
        )
        session.add(task)
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_task_performance_sqlite_immutability_trigger(async_session_factory, seeded_entities, seeded_shift):
    async with async_session_factory() as session:
        task = TaskPerformance(
            tenant_id=seeded_entities['tenant_id'],
            shift_id=seeded_shift,
            kind='PICK',
            source_id=seeded_entities['movement_id'],
            movement_id=seeded_entities['movement_id'],
            count_id=None,
            duration_seconds=120,
            accurate_lines=3,
            total_lines=3,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        # Attempt to UPDATE should trigger SQLite abort
        with pytest.raises(IntegrityError, match='Workforce activity is immutable'):
            await session.execute(
                update(TaskPerformance)
                .where(TaskPerformance.id == task_id)
                .values(duration_seconds=180)
            )
        await session.rollback()

        # Attempt to DELETE should trigger SQLite abort
        with pytest.raises(IntegrityError, match='Workforce activity is immutable'):
            await session.execute(
                delete(TaskPerformance)
                .where(TaskPerformance.id == task_id)
            )
        await session.rollback()


@pytest.mark.asyncio
async def test_staffing_plan_defaults_and_persistence(async_session_factory, seeded_entities):
    async with async_session_factory() as session:
        plan = StaffingPlan(
            tenant_id=seeded_entities['tenant_id'],
            target_date='2026-11-10',
            inputs={'projectedVolume': 500, 'shiftDurationHours': 8},
            recommendation={'recommendedHeadcount': 4},
        )
        session.add(plan)
        await session.commit()

        assert plan.id is not None
        assert isinstance(uuid.UUID(plan.id), uuid.UUID)
        assert plan.status == 'DRAFT'
        assert plan.version == 0
        assert plan.target_date == '2026-11-10'
        assert plan.inputs['projectedVolume'] == 500
        assert plan.recommendation['recommendedHeadcount'] == 4

        fetched = await session.get(StaffingPlan, plan.id)
        assert fetched is not None
        assert fetched.status == 'DRAFT'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['DRAFT', 'PUBLISHED'])
async def test_staffing_plan_valid_statuses(async_session_factory, seeded_entities, status):
    async with async_session_factory() as session:
        plan = StaffingPlan(
            tenant_id=seeded_entities['tenant_id'],
            target_date='2026-11-10',
            inputs={},
            recommendation={},
            status=status,
        )
        session.add(plan)
        await session.commit()
        assert plan.status == status


@pytest.mark.asyncio
@pytest.mark.parametrize('status,version', [
    ('ARCHIVED', 0),   # Invalid status
    ('CANCELLED', 0),  # Invalid status
    ('DRAFT', -1),     # Invalid version
])
async def test_staffing_plan_check_constraints(async_session_factory, seeded_entities, status, version):
    async with async_session_factory() as session:
        plan = StaffingPlan(
            tenant_id=seeded_entities['tenant_id'],
            target_date='2026-11-10',
            inputs={},
            recommendation={},
            status=status,
            version=version,
        )
        session.add(plan)
        with pytest.raises(IntegrityError):
            await session.commit()
