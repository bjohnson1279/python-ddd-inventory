import uuid
import re
from datetime import timedelta
from sqlalchemy import select
from src.infrastructure.report_alert_models import ReportJob as Job, ReportSchedule as Schedule
from src.infrastructure.owned_feature_models import ReportDefinitionModel as Definition
from src.infrastructure.auth.identity import UserModel
from src.infrastructure.auth.security import ROLE_PERMISSIONS
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.financial_posting import database_now
from src.infrastructure.retail_alerts import notify, evaluate
from src.application.costing_service import locked_config
from src.application.retail_reports import validate_definition, required_permission, snapshot
from src.domain.product.layer_costing import utc


async def tenant_context(session, tenant):
    if session.bind.dialect.name == 'postgresql':
        from src.application.workers.outbox_worker import set_worker_tenant
        await set_worker_tenant(session, tenant)


async def owned(session, model, tenant, identifier):
    row = await session.scalar(select(model).where(model.id == identifier, model.tenant_id == tenant).execution_options(populate_existing=True))
    if not row:
        raise LookupError('Report record not found')
    return row


async def authorize_definition(session, tenant, actor, definition):
    user = await session.scalar(select(UserModel).where(UserModel.id == actor, UserModel.tenant_id == tenant, UserModel.is_active.is_(True)))
    permissions = ROLE_PERMISSIONS.get(user.role, set()) if user else set()
    if 'reports:read' not in permissions or required_permission(definition) not in permissions:
        raise PermissionError('Report permission is missing or changed')


def job_dto(job):
    return {'id': job.id, 'executionId': job.id, 'reportDefinitionId': job.report_id, 'name': job.definition['name'], 'type': job.definition['type'], 'status': job.status,
        'format': job.format, 'version': job.version, 'attempts': job.attempts, 'error': job.error,
        'createdAt': utc(job.created_at).isoformat(), 'completedAt': utc(job.completed_at).isoformat() if job.completed_at else None,
        'fileUrl': f'/reports/executions/{job.id}/content' if job.status == 'COMPLETED' else None,
        'complete': job.result.get('complete') if job.result else None, 'rowCount': len(job.result['rows']) if job.result else None}


def schedule_dto(row):
    return {'id': row.id, 'reportDefinitionId': row.report_id, 'cronExpression': row.cron, 'timezone': 'UTC',
        'format': row.format, 'deliveryMethod': 'INTERNAL', 'active': row.active, 'version': row.version,
        'nextRunAt': utc(row.next_run_at).isoformat(), 'error': row.error}


def next_run(expression, now):
    match = re.fullmatch(r'(\d{1,2}) (\d{1,2}) \* \* (\*|[0-6])', expression)
    if not match or int(match[1]) > 59 or int(match[2]) > 23:
        raise ValueError('Supported UTC schedules use minute hour * * * or minute hour * * weekday (0=Sunday)')
    candidate = now.replace(hour=int(match[2]), minute=int(match[1]), second=0, microsecond=0)
    for _ in range(8):
        if candidate > now and (match[3] == '*' or (candidate.weekday() + 1) % 7 == int(match[3])):
            return candidate
        candidate += timedelta(days=1)
    raise ValueError('Invalid schedule')


async def enqueue(session, tenant, actor, definition, format, key):
    validated = validate_definition(definition.payload)
    await authorize_definition(session, tenant, actor, validated)
    prior = await session.scalar(select(Job).where(Job.tenant_id == tenant, Job.request_key == key))
    if prior:
        if prior.requested_by != actor or prior.report_id != definition.id or prior.format != format:
            raise ValueError('Request key belongs to another report command')
        return prior
    if format not in ('csv', 'json'):
        raise ValueError('Only CSV and JSON report exports are supported')
    job = Job(id=str(uuid.uuid4()), tenant_id=tenant, report_id=definition.id, requested_by=actor,
        request_key=key, definition=validated, format=format, status='QUEUED', version=0, attempts=0)
    session.add(job)
    await session.flush()
    session.add(OutboxEventModel(aggregate_type='Report', aggregate_id=job.id, event_type='ReportExecutionRequested',
        payload={'tenantId': tenant, 'executionId': job.id, 'actorId': actor}))
    await session.flush()
    return job


async def process_job(session, tenant, identifier):
    await tenant_context(session, tenant)
    await locked_config(session, tenant)
    job = await owned(session, Job, tenant, identifier)
    if job.status != 'QUEUED':
        result = job_dto(job)
        await session.rollback()
        return result
    job.attempts += 1
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    try:
        await authorize_definition(session, tenant, job.requested_by, job.definition)
        result = await snapshot(session, tenant, job.definition, now, job.requested_by)
        result['reportType'], result['filters'] = job.definition['type'], job.definition['filters']
    except (ValueError, PermissionError) as exc:
        job.status, job.error, job.result = 'FAILED', str(exc), None
    else:
        job.result, job.status, job.completed_at, job.error = result, 'COMPLETED', now, None
        try:
            await session.run_sync(lambda sync: notify(sync.connection(), tenant, job.requested_by,
                'Report ready: ' + job.definition['name'], now, link=f'/reports/executions/{job.id}/content'))
        except Exception:
            await session.rollback()
            raise
    job.version += 1
    await session.commit()
    return job_dto(job)


async def scheduler_tick(session, tenant):
    await tenant_context(session, tenant)
    await locked_config(session, tenant)
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    schedules = list(await session.scalars(select(Schedule).where(Schedule.tenant_id == tenant, Schedule.active.is_(True),
        Schedule.next_run_at <= now).order_by(Schedule.next_run_at, Schedule.id).limit(100)))
    count = 0
    for row in schedules:
        definition = await owned(session, Definition, tenant, row.report_id)
        try:
            await enqueue(session, tenant, row.created_by, definition, row.format, 'schedule:' + row.id + ':' + utc(row.next_run_at).isoformat())
            row.next_run_at, row.error = next_run(row.cron, now), None
            count += 1
        except (ValueError, PermissionError) as exc:
            row.active, row.error = False, str(exc)
        row.version += 1
    await session.run_sync(lambda sync: evaluate(sync.connection(), tenant))
    await session.commit()
    return {'executionsQueued': count}
