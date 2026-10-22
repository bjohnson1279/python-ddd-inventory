"""Tenant-scoped workforce lifecycle and source-backed activity metrics."""
from datetime import datetime, date, timedelta
from typing import Literal
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import UserModel, TenantModel, current_tenant
from src.infrastructure.auth.security import ROLE_PERMISSIONS
from src.infrastructure.workforce_models import OperatorProfile as Profile, LaborShift as Shift, TaskPerformance as Task, StaffingPlan as Plan
from src.infrastructure.transfer_models import InventoryActionModel as Action
from src.infrastructure.stock_models import StockMovementModel as Movement
from src.infrastructure.cycle_count.models import CycleCountRecordModel as Count, CycleCountLineItemModel as Line
from src.application.costing_service import locked_config
from src.infrastructure.financial_posting import database_now
from src.domain.product.layer_costing import utc
from src.domain.labor.services import PredictiveSchedulingEngine
from src.infrastructure.retail_alerts import notify

router = APIRouter(prefix='/workforce', tags=['workforce'])

class Command(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    mutationId: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    tenantId: str | None = None

class Versioned(Command):
    expectedVersion: int = Field(ge=0, strict=True)

class ProfileCommand(Versioned):
    targetPicksPerHour: float = Field(gt=0, le=10000, strict=True)
    maxConsecutiveHours: int = Field(ge=1, le=16, strict=True)
    certifications: list[str] = Field(default_factory=list, max_length=30)

    @field_validator('certifications')
    @classmethod
    def certifications_valid(cls, values):
        if any(not value.strip() or len(value) > 100 for value in values):
            raise ValueError('Certifications must be nonempty and at most 100 characters')
        return sorted(set(value.strip() for value in values))

class ShiftCommand(Command):
    operatorId: str = Field(min_length=1, max_length=100)
    startTime: datetime
    endTime: datetime
    locationId: str = Field(min_length=1, max_length=100)
    requiredCertifications: list[str] = Field(default_factory=list, max_length=30)

    @field_validator('startTime', 'endTime')
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError('Shift times require a UTC offset')
        return utc(value)

    @field_validator('requiredCertifications')
    @classmethod
    def certs(cls, values):
        return ProfileCommand.certifications_valid(values)

class TaskCommand(Command):
    kind: Literal['PICK', 'COUNT']
    sourceId: str = Field(min_length=1, max_length=100)
    durationSeconds: int = Field(gt=0, le=57600, strict=True)
    traversalDistanceMeters: float | None = Field(default=None, ge=0, le=100000, strict=True)

class PlanCommand(Command):
    targetDate: date
    projectedInboundVolume: int = Field(ge=0, le=10000000, strict=True)
    projectedOutboundVolume: int = Field(ge=0, le=10000000, strict=True)
    averageOperatorTargetPicks: float = Field(gt=0, le=10000, strict=True)
    shiftDurationHours: float = Field(gt=0, le=16, strict=True)
    requiredCertifications: list[str] = Field(default_factory=list, max_length=30)

    @field_validator('requiredCertifications')
    @classmethod
    def certs(cls, values):
        return ProfileCommand.certifications_valid(values)

def dto(row):
    if isinstance(row, Profile):
        return {'id': row.id, 'userId': row.user_id, 'version': row.version, 'targetPicksPerHour': row.target_picks_per_hour,
            'maxConsecutiveHours': row.max_hours, 'certifications': row.certifications}
    if isinstance(row, Shift):
        return {'id': row.id, 'operatorId': row.operator_id, 'version': row.version, 'status': row.status,
            'startTime': utc(row.planned_start).isoformat(), 'endTime': utc(row.planned_end).isoformat(),
            'startedAt': utc(row.started_at).isoformat() if row.started_at else None,
            'endedAt': utc(row.ended_at).isoformat() if row.ended_at else None, 'locationId': row.location_id,
            'requiredCertifications': row.required_certifications}
    if isinstance(row, Plan):
        return {'id': row.id, 'targetDate': row.target_date, 'version': row.version, 'status': row.status,
            'inputs': row.inputs, **row.recommendation}
    return {'id': row.id, 'shiftId': row.shift_id, 'kind': row.kind, 'sourceId': row.source_id,
        'durationSeconds': row.duration_seconds, 'traversalDistanceMeters': row.distance_meters,
        'accurateCountLines': row.accurate_lines, 'totalCountLines': row.total_lines}

def manager(request):
    return 'workforce:manage' in ROLE_PERMISSIONS.get(request.state.roles[0], set())

async def owned(session, model, tenant, identifier):
    row = await session.scalar(select(model).where(model.tenant_id == tenant, model.id == identifier).execution_options(populate_existing=True))
    if not row:
        raise HTTPException(404, 'Workforce record not found')
    return row

async def eligible(session, tenant, profile):
    user = await owned(session, UserModel, tenant, profile.user_id)
    if not user.is_active or user.role != 'WAREHOUSE_OPERATOR':
        raise HTTPException(409, 'Assignment requires an active warehouse operator')
    return user

async def scope(session, request, shift, actor_role=None):
    profile = await owned(session, Profile, shift.tenant_id, shift.operator_id)
    managing = actor_role in ('ADMIN', 'MANAGER') if actor_role else manager(request)
    if not managing and profile.user_id != request.state.user_id:
        raise HTTPException(404, 'Workforce record not found')
    return profile

async def capacity(session, tenant, data):
    profiles = list(await session.scalars(select(Profile).join(UserModel, UserModel.id == Profile.user_id).where(
        Profile.tenant_id == tenant, UserModel.tenant_id == tenant, UserModel.is_active.is_(True), UserModel.role == 'WAREHOUSE_OPERATOR')))
    return sum(1 for p in profiles if p.max_hours >= data['shiftDurationHours'] and set(data['requiredCertifications']) <= set(p.certifications))

def consecutive_limit(intervals, hours, candidate=None):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    relevant = [(start, end) for start, end in merged if candidate is None or start <= candidate[0] and end >= candidate[1]]
    if any((end-start).total_seconds() > hours*3600 for start, end in relevant):
        raise HTTPException(409, 'Consecutive planned shifts exceed the operator limit')

async def mutate(session, request, data, operation, identifier=None):
    tenant, actor = current_tenant(request), request.state.user_id
    fingerprint = {'operation': operation, 'id': identifier, **data.model_dump(mode='json')}
    key = 'workforce:' + data.mutationId
    try:
        # Match user administration's lock order so disable/role edits cannot
        # race eligibility checks and assignment commits.
        await session.execute(update(TenantModel).where(TenantModel.id == tenant).values(name=TenantModel.name))
        await locked_config(session, tenant)
        # Reload current identity under the lock, not the supplied JWT role.
        user = await owned(session, UserModel, tenant, actor)
        if not user.is_active or user.role not in ('ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR') or (operation in ('profile', 'shift', 'plan', 'publish', 'cancel') and user.role not in ('ADMIN', 'MANAGER')):
            raise HTTPException(403, 'Workforce permission changed')
        target = await owned(session, UserModel if operation == 'profile' else Plan if operation == 'publish' else Shift, tenant, identifier) if identifier else None
        profile = await scope(session, request, target, user.role) if isinstance(target, Shift) else None
        if profile and operation in ('start', 'task'):
            await eligible(session, tenant, profile)
        prior = await session.scalar(select(Action).where(Action.tenant_id == tenant, Action.mutation_id == key))
        if prior:
            if prior.actor_id != actor or prior.request != fingerprint:
                raise HTTPException(409, 'Mutation ID belongs to another workforce command')
            result = prior.result
            await session.rollback()
            return result
        now = await session.run_sync(lambda sync: database_now(sync.connection()))
        if operation == 'profile':
            if not target.is_active or target.role != 'WAREHOUSE_OPERATOR':
                raise HTTPException(409, 'Profiles require an active warehouse operator')
            row = await session.scalar(select(Profile).where(Profile.tenant_id == tenant, Profile.user_id == identifier).execution_options(populate_existing=True))
            if (row.version if row else 0) != data.expectedVersion:
                raise HTTPException(409, 'Operator profile version changed')
            if not row:
                row = Profile(tenant_id=tenant, user_id=identifier, version=0)
                session.add(row)
            # Existing nonterminal assignments must remain valid.
            shifts = list(await session.scalars(select(Shift).where(Shift.tenant_id == tenant, Shift.operator_id == row.id, Shift.status.in_(['PLANNED', 'ACTIVE'])))) if row.id else []
            if any((utc(s.planned_end)-utc(s.planned_start)).total_seconds() > data.maxConsecutiveHours*3600 or not set(s.required_certifications) <= set(data.certifications) for s in shifts):
                raise HTTPException(409, 'Profile changes invalidate an open shift')
            history = list(await session.scalars(select(Shift).where(Shift.tenant_id == tenant, Shift.operator_id == row.id, Shift.status != 'CANCELLED'))) if row.id else []
            for shift in shifts:
                consecutive_limit([(utc(s.planned_start), utc(s.planned_end)) for s in history], data.maxConsecutiveHours,
                    (utc(shift.planned_start), utc(shift.planned_end)))
            row.target_picks_per_hour, row.max_hours, row.certifications = data.targetPicksPerHour, data.maxConsecutiveHours, data.certifications
            row.version += 1
        elif operation == 'shift':
            profile = await owned(session, Profile, tenant, data.operatorId)
            await eligible(session, tenant, profile)
            if data.endTime <= data.startTime or data.endTime <= now or (data.endTime-data.startTime).total_seconds() > profile.max_hours*3600:
                raise HTTPException(400, 'Shift interval is invalid or exceeds the operator limit')
            if not set(data.requiredCertifications) <= set(profile.certifications):
                raise HTTPException(409, 'Operator lacks required certifications')
            overlap = await session.scalar(select(Shift.id).where(Shift.tenant_id == tenant, Shift.operator_id == profile.id,
                Shift.status != 'CANCELLED', Shift.planned_start < data.endTime, Shift.planned_end > data.startTime))
            if overlap:
                raise HTTPException(409, 'Operator already has an overlapping shift')
            existing = await session.scalars(select(Shift).where(Shift.tenant_id == tenant, Shift.operator_id == profile.id, Shift.status != 'CANCELLED'))
            consecutive_limit([(utc(s.planned_start), utc(s.planned_end)) for s in existing] + [(data.startTime, data.endTime)], profile.max_hours, (data.startTime, data.endTime))
            row = Shift(tenant_id=tenant, operator_id=profile.id, planned_start=data.startTime, planned_end=data.endTime,
                location_id=data.locationId, required_certifications=data.requiredCertifications, status='PLANNED', version=0)
            session.add(row)
        elif operation in ('start', 'complete', 'cancel'):
            row = target
            if row.version != data.expectedVersion:
                raise HTTPException(409, 'Shift version changed')
            required = 'PLANNED' if operation in ('start', 'cancel') else 'ACTIVE'
            if row.status != required:
                raise HTTPException(409, 'Invalid shift transition')
            if operation == 'start':
                if not utc(row.planned_start) <= now < utc(row.planned_end):
                    raise HTTPException(409, 'Shift can only start within its planned interval')
                if not set(row.required_certifications) <= set(profile.certifications):
                    raise HTTPException(409, 'Operator lacks required certifications')
                if await session.scalar(select(Shift.id).where(Shift.tenant_id == tenant, Shift.operator_id == profile.id, Shift.status == 'ACTIVE')):
                    raise HTTPException(409, 'Operator already has an active shift')
                row.status, row.started_at = 'ACTIVE', now
            elif operation == 'complete':
                row.status, row.ended_at = 'COMPLETED', now
            else:
                row.status = 'CANCELLED'
            row.version += 1
        elif operation == 'task':
            if target.status != 'ACTIVE':
                raise HTTPException(409, 'Activities require an active shift')
            if data.durationSeconds > profile.max_hours*3600:
                raise HTTPException(400, 'Reported activity duration exceeds the operator limit')
            recorded_seconds = await session.scalar(select(func.coalesce(func.sum(Task.duration_seconds), 0)).where(Task.tenant_id == tenant, Task.shift_id == target.id))
            if recorded_seconds + data.durationSeconds > (utc(target.planned_end)-utc(target.planned_start)).total_seconds():
                raise HTTPException(409, 'Reported activities exceed the planned shift duration')
            accurate, total = 0, 0
            if data.kind == 'PICK':
                source = await owned(session, Movement, tenant, data.sourceId)
                if source.actor_id != profile.user_id or source.location_id != target.location_id or source.operation not in ('dispatch', 'fulfill-allocation', 'serial-sell', 'kit-sale-component') or utc(source.occurred_at) < utc(target.started_at):
                    raise HTTPException(409, 'Pick source does not belong to this operator shift')
            else:
                source = await owned(session, Count, tenant, data.sourceId)
                if source.assigned_operator_id != profile.user_id or source.location_id != target.location_id or source.status != 'COMPLETED':
                    raise HTTPException(409, 'Count source must be completed and assigned to this operator')
                lines = list(await session.scalars(select(Line).where(Line.record_id == source.id)))
                if not lines or any(l.counted_quantity is None for l in lines):
                    raise HTTPException(409, 'Count has no complete observations')
                accurate, total = sum(l.counted_quantity == l.expected_quantity for l in lines), len(lines)
            if await session.scalar(select(Task.id).where(Task.tenant_id == tenant, Task.kind == data.kind, Task.source_id == data.sourceId)):
                raise HTTPException(409, 'Activity source has already been recorded')
            row = Task(tenant_id=tenant, shift_id=target.id, kind=data.kind, source_id=source.id,
                movement_id=source.id if data.kind == 'PICK' else None, count_id=source.id if data.kind == 'COUNT' else None,
                duration_seconds=data.durationSeconds, distance_meters=data.traversalDistanceMeters,
                accurate_lines=accurate, total_lines=total, created_at=now)
            session.add(row)
        elif operation == 'plan':
            if data.targetDate < now.date():
                raise HTTPException(400, 'Staffing plans require a current or future date')
            recommendation = PredictiveSchedulingEngine().generate_staffing_recommendation(data.targetDate,
                data.projectedInboundVolume, data.projectedOutboundVolume, data.averageOperatorTargetPicks, data.shiftDurationHours)
            inputs = data.model_dump(mode='json', exclude={'mutationId', 'reason', 'tenantId', 'targetDate'})
            available = await capacity(session, tenant, inputs)
            row = Plan(tenant_id=tenant, target_date=data.targetDate.isoformat(), inputs=inputs,
                recommendation={'recommendedHeadcount': recommendation.recommended_headcount, 'eligibleOperatorCount': available,
                    'shortfall': max(0, recommendation.recommended_headcount-available), 'method': 'EXPLICIT_VOLUME_HEURISTIC'}, status='DRAFT', version=0)
            session.add(row)
        else:
            row = target
            if row.version != data.expectedVersion or row.status != 'DRAFT':
                raise HTTPException(409, 'Staffing plan is no longer this draft version')
            if date.fromisoformat(row.target_date) < now.date():
                raise HTTPException(409, 'Staffing date has passed')
            available = await capacity(session, tenant, row.inputs)
            row.recommendation = {**row.recommendation, 'eligibleOperatorCount': available,
                'shortfall': max(0, row.recommendation['recommendedHeadcount']-available)}
            row.status, row.version = 'PUBLISHED', row.version + 1
        await session.flush()
        result = dto(row)
        if operation in ('shift', 'cancel'):
            notify_target = profile or await owned(session, Profile, tenant, row.operator_id)
            await session.run_sync(lambda sync: notify(sync.connection(), tenant, notify_target.user_id,
                f"Shift {'assigned' if operation == 'shift' else 'cancelled'} at {row.location_id}: {utc(row.planned_start).isoformat()}", now))
        session.add(Action(tenant_id=tenant, mutation_id=key, actor_id=actor, request=fingerprint, result=result))
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, 'Workforce record changed or activity already exists') from exc
    except HTTPException:
        await session.rollback()
        raise
    except Exception:
        await session.rollback()
        raise

async def summary(session, tenant, profile, start, end):
    tasks = list(await session.scalars(select(Task).join(Shift, Shift.id == Task.shift_id).where(Task.tenant_id == tenant,
        Shift.tenant_id == tenant, Shift.operator_id == profile.id, Task.created_at >= start, Task.created_at < end)))

    pick_count = 0
    seconds = 0
    total = 0
    accurate = 0
    distance_meters = 0.0
    distance_complete = bool(tasks)

    for t in tasks:
        total += t.total_lines
        accurate += t.accurate_lines
        if t.kind == 'PICK':
            pick_count += 1
            seconds += t.duration_seconds

        if distance_complete:
            if t.distance_meters is not None:
                distance_meters += t.distance_meters
            else:
                distance_complete = False

    return {'recordedPickTasks': pick_count, 'reportedPickSeconds': seconds,
        'picksPerReportedHour': pick_count*3600/seconds if seconds else None,
        'countAccuracyPercent': accurate*100/total if total else None, 'countLines': total,
        'traversalDistanceMeters': distance_meters if distance_complete else None,
        'distanceComplete': distance_complete,
        'timingSource': 'OPERATOR_REPORTED', 'from': start.isoformat(), 'toExclusive': end.isoformat()}

@router.get('/me')
async def me(request: Request, days: int = Query(ge=1, le=31, default=1), session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    profile = await session.scalar(select(Profile).where(Profile.tenant_id == tenant, Profile.user_id == request.state.user_id))
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)-timedelta(days=days-1)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1)
    shifts = list(await session.scalars(select(Shift).where(Shift.tenant_id == tenant, Shift.operator_id == profile.id).order_by(Shift.planned_start.desc()).limit(100))) if profile else []
    return {'profile': dto(profile) if profile else None, 'shifts': [dto(s) for s in shifts],
        'metrics': await summary(session, tenant, profile, start, end) if profile else None}

@router.get('/operators')
async def operators(request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    rows = await session.execute(select(UserModel, Profile).outerjoin(Profile, (Profile.user_id == UserModel.id) & (Profile.tenant_id == tenant)).where(
        UserModel.tenant_id == tenant, UserModel.role == 'WAREHOUSE_OPERATOR', UserModel.is_active.is_(True)).order_by(UserModel.name).limit(1000))
    return [{'userId': user.id, 'name': user.name, 'profile': dto(p) if p else None} for user, p in rows]

@router.get('/operators/{identifier}/metrics')
async def operator_metrics(identifier: str, request: Request, days: int = Query(default=1, ge=1, le=31), session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    profile = await owned(session, Profile, tenant, identifier)
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)-timedelta(days=days-1)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)+timedelta(days=1)
    return await summary(session, tenant, profile, start, end)

@router.get('/shifts')
async def shifts(request: Request, session: AsyncSession = Depends(get_db_session)):
    query = select(Shift).where(Shift.tenant_id == current_tenant(request))
    if not manager(request):
        query = query.join(Profile, Profile.id == Shift.operator_id).where(Profile.user_id == request.state.user_id, Profile.tenant_id == current_tenant(request))
    return [dto(s) for s in await session.scalars(query.order_by(Shift.planned_start.desc()).limit(1000))]

@router.get('/shifts/{identifier}/activities')
async def activities(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    shift = await owned(session, Shift, current_tenant(request), identifier)
    await scope(session, request, shift)
    return [dto(t) for t in await session.scalars(select(Task).where(Task.tenant_id == shift.tenant_id, Task.shift_id == shift.id).order_by(Task.created_at).limit(1000))]

@router.get('/shifts/{identifier}/sources')
async def activity_sources(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    shift = await owned(session, Shift, tenant, identifier)
    profile = await scope(session, request, shift)
    if shift.status != 'ACTIVE':
        return []
    recorded = select(Task.source_id).where(Task.tenant_id == tenant)
    picks = await session.scalars(select(Movement).where(Movement.tenant_id == tenant, Movement.actor_id == profile.user_id,
        Movement.location_id == shift.location_id, Movement.occurred_at >= shift.started_at,
        Movement.operation.in_(['dispatch', 'fulfill-allocation', 'serial-sell', 'kit-sale-component']), ~Movement.id.in_(recorded)).order_by(Movement.occurred_at.desc()).limit(1000))
    counts = await session.scalars(select(Count).where(Count.tenant_id == tenant, Count.assigned_operator_id == profile.user_id,
        Count.location_id == shift.location_id, Count.status == 'COMPLETED', ~Count.id.in_(recorded)).order_by(Count.created_at.desc()).limit(1000))
    return [{'id': p.id, 'kind': 'PICK', 'label': p.sku + ' · ' + p.operation} for p in picks] + [
        {'id': c.id, 'kind': 'COUNT', 'label': c.name} for c in counts]

@router.get('/plans')
async def plans(request: Request, session: AsyncSession = Depends(get_db_session)):
    return [dto(p) for p in await session.scalars(select(Plan).where(Plan.tenant_id == current_tenant(request)).order_by(Plan.target_date.desc()).limit(1000))]

@router.put('/profiles/{identifier}')
async def profile(identifier: str, data: ProfileCommand, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await mutate(session, request, data, 'profile', identifier)

@router.post('/shifts')
async def create_shift(data: ShiftCommand, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await mutate(session, request, data, 'shift')

@router.post('/shifts/{identifier}/start')
async def start_shift(identifier: str, data: Versioned, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await mutate(session, request, data, 'start', identifier)

@router.post('/shifts/{identifier}/complete')
async def complete_shift(identifier: str, data: Versioned, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await mutate(session, request, data, 'complete', identifier)

@router.post('/shifts/{identifier}/cancel')
async def cancel_shift(identifier: str, data: Versioned, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await mutate(session, request, data, 'cancel', identifier)

@router.post('/shifts/{identifier}/activities')
async def log_activity(identifier: str, data: TaskCommand, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await mutate(session, request, data, 'task', identifier)

@router.post('/plans')
async def create_plan(data: PlanCommand, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await mutate(session, request, data, 'plan')

@router.post('/plans/{identifier}/publish')
async def publish_plan(identifier: str, data: Versioned, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await mutate(session, request, data, 'publish', identifier)
