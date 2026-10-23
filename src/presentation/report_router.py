import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, Depends, Query, Body
from fastapi.responses import Response, JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.owned_feature_models import ReportDefinitionModel as Definition
from src.infrastructure.report_alert_models import ReportJob as Job, ReportSchedule as Schedule
from src.infrastructure.transfer_models import InventoryActionModel as Action
from src.infrastructure.financial_posting import database_now
from src.application.costing_service import locked_config
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.auth.rbac import requires_roles
from src.application.report_service import owned, enqueue, authorize_definition, process_job, scheduler_tick, next_run, job_dto, schedule_dto
from src.application.retail_reports import validate_definition, export_csv, required_permission

router = APIRouter(prefix='/reports', tags=['reports'])

class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenantId: str | None = None
    mutationId: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=1, max_length=100)
    reason: str = Field(default='Reviewed report command', min_length=1, max_length=500)

class Create(Command):
    name: str = Field(min_length=1, max_length=100, pattern=r'\S')
    description: str = Field(default='', max_length=2000)
    type: str = 'STOCK'
    filters: dict | str = Field(default_factory=dict)
    grouping: dict | str | None = None

class Execute(Command):
    format: Literal['json', 'csv'] = 'json'

class ScheduleCommand(Execute):
    cronExpression: str = Field(max_length=100)
    deliveryMethod: Literal['INTERNAL'] = 'INTERNAL'

class Versioned(Command):
    expectedVersion: int = Field(ge=0, strict=True)

class Toggle(Versioned):
    active: bool = Field(strict=True)

def definition_dto(row):
    return {**row.payload, 'id': row.id, 'tenant_id': row.tenant_id, 'tenantId': row.tenant_id, 'created_by': row.created_by}

async def command(request, session, data, operation, identifier=None):
    tenant, actor = current_tenant(request), request.state.user_id
    fingerprint = {'operation': operation, 'id': identifier, **data.model_dump(mode='json')}
    key = 'report:' + data.mutationId
    try:
        await locked_config(session, tenant)
        target = await owned(session, Job if operation == 'retry' else Schedule if operation == 'toggle' else Definition, tenant, identifier) if identifier else None
        if target:
            current_definition = target.definition if operation == 'retry' else validate_definition((await owned(session, Definition, tenant, target.report_id)).payload) if operation == 'toggle' else validate_definition(target.payload)
            await authorize_definition(session, tenant, actor, current_definition)
        prior = await session.scalar(select(Action).where(Action.tenant_id == tenant, Action.mutation_id == key))
        if prior:
            if prior.actor_id != actor or prior.request != fingerprint:
                raise HTTPException(409, 'Mutation ID belongs to another report command')
            result = prior.result
            await session.rollback()
            return result
        if operation == 'create':
            payload = validate_definition(data.model_dump(include={'name', 'description', 'type', 'filters', 'grouping'}))
            await authorize_definition(session, tenant, actor, payload)
            report = Definition(id=str(uuid.uuid4()), tenant_id=tenant, created_by=actor, payload=payload)
            session.add(report)
            await session.flush()
            result = {'success': True, 'report': definition_dto(report)}
        elif operation == 'execute':
            job = await enqueue(session, tenant, actor, target, data.format, key)
            result = {'success': True, 'executionId': job.id, 'execution': job_dto(job)}
        elif operation == 'schedule':
            definition = validate_definition(target.payload)
            await authorize_definition(session, tenant, actor, definition)
            now = await session.run_sync(lambda sync: database_now(sync.connection()))
            row = Schedule(id=str(uuid.uuid4()), tenant_id=tenant, report_id=target.id, created_by=actor,
                cron=data.cronExpression, format=data.format, next_run_at=next_run(data.cronExpression, now), active=True, version=0)
            session.add(row)
            result = {'success': True, 'schedule': schedule_dto(row)}
        else:
            definition = target.definition if operation == 'retry' else validate_definition((await owned(session, Definition, tenant, target.report_id)).payload)
            await authorize_definition(session, tenant, actor, definition)
            if target.version != data.expectedVersion:
                raise HTTPException(409, 'Report record version changed')
            if operation == 'retry':
                if target.status != 'FAILED':
                    raise HTTPException(409, 'Only failed report jobs can be retried')
                target.status, target.error = 'QUEUED', None
                target.version += 1
                result = job_dto(target)
            else:
                target.active, target.version, target.error = data.active, target.version + 1, None
                if data.active:
                    now = await session.run_sync(lambda sync: database_now(sync.connection()))
                    target.next_run_at = next_run(target.cron, now)
                result = schedule_dto(target)
        session.add(Action(tenant_id=tenant, mutation_id=key, actor_id=actor, request=fingerprint, result=result))
        await session.commit()
        return result
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        await session.rollback()
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, IntegrityError) as exc:
        await session.rollback()
        raise HTTPException(400 if isinstance(exc, ValueError) else 409, str(exc) if isinstance(exc, ValueError) else 'Concurrent report command; repeat the original mutation ID') from exc
    except Exception:
        await session.rollback()
        raise

@router.post('')
@router.post('/')
@requires_roles(['ADMIN', 'MANAGER', 'ACCOUNTANT'])
async def create_report(data: Create, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data, 'create')

@router.get('')
@router.get('/')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'VIEWER', 'ACCOUNTANT'])
async def list_reports(request: Request, session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(Definition).where(Definition.tenant_id == current_tenant(request)).order_by(Definition.id))
    return {'reports': [definition_dto(row) for row in rows if required_permission(row.payload) in request.state.permissions]}

@router.get('/executions')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'VIEWER', 'ACCOUNTANT'])
async def executions(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(Job).where(Job.tenant_id == current_tenant(request)).order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset))
    return [job_dto(row) for row in rows if required_permission(row.definition) in request.state.permissions]

async def readable_job(request, session, identifier):
    try:
        job = await owned(session, Job, current_tenant(request), identifier)
        await authorize_definition(session, current_tenant(request), request.state.user_id, job.definition)
        return job
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

@router.get('/executions/{identifier}')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'VIEWER', 'ACCOUNTANT'])
async def execution(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    return job_dto(await readable_job(request, session, identifier))

@router.get('/executions/{identifier}/content')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'VIEWER', 'ACCOUNTANT'])
async def content(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    job = await readable_job(request, session, identifier)
    if job.status != 'COMPLETED' or job.result is None:
        raise HTTPException(409, 'Report export is not completed')
    headers = {'Content-Disposition': 'attachment; filename="report.' + job.format + '"', 'Cache-Control': 'private, no-store'}
    return JSONResponse(job.result, headers=headers) if job.format == 'json' else Response(export_csv(job.result), media_type='text/csv; charset=utf-8', headers=headers)

@router.post('/executions/{identifier}/process')
@requires_roles(['ADMIN', 'MANAGER', 'ACCOUNTANT'])
async def process(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    await readable_job(request, session, identifier)
    return await process_job(session, current_tenant(request), identifier)

@router.post('/executions/{identifier}/retry')
@requires_roles(['ADMIN', 'MANAGER', 'ACCOUNTANT'])
async def retry(identifier: str, data: Versioned, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data, 'retry', identifier)

@router.get('/schedules')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'VIEWER', 'ACCOUNTANT'])
async def schedules(request: Request, session: AsyncSession = Depends(get_db_session)):
    rows = await session.execute(select(Schedule, Definition).join(Definition, Definition.id == Schedule.report_id).where(
        Schedule.tenant_id == current_tenant(request), Definition.tenant_id == current_tenant(request)).order_by(Schedule.next_run_at, Schedule.id))
    return [schedule_dto(row) for row, definition in rows if required_permission(definition.payload) in request.state.permissions]

@router.post('/schedules/{identifier}/active')
@requires_roles(['ADMIN', 'MANAGER', 'ACCOUNTANT'])
async def toggle_schedule(identifier: str, data: Toggle, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data, 'toggle', identifier)

@router.post('/scheduler/tick')
async def tick(request: Request, session: AsyncSession = Depends(get_db_session)):
    return await scheduler_tick(session, current_tenant(request))

@router.post('/{identifier}/execute', status_code=202)
@requires_roles(['ADMIN', 'MANAGER', 'ACCOUNTANT'])
async def execute_report(identifier: str, request: Request, data: Execute | None = Body(default=None), session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data or Execute(), 'execute', identifier)

@router.post('/{identifier}/schedule')
@requires_roles(['ADMIN', 'MANAGER', 'ACCOUNTANT'])
async def schedule_report(identifier: str, request: Request, data: ScheduleCommand | None = Body(default=None), session: AsyncSession = Depends(get_db_session)):
    if data is None:
        try:
            await owned(session, Definition, current_tenant(request), identifier)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        raise HTTPException(422, 'cronExpression is required')
    return await command(request, session, data, 'schedule', identifier)

@router.get('/shared/{token}')
async def shared(token: str, request: Request):
    raise HTTPException(404, 'Report share not found')
