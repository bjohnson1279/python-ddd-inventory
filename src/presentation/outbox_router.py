import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.messaging.models import OutboxEventModel as Event
from src.infrastructure.transfer_models import InventoryActionModel as Action
from src.application.costing_service import locked_config
from src.application.workers.outbox_worker import tenant_events
from src.domain.product.layer_costing import utc

router = APIRouter(prefix='/outbox', tags=['event delivery'])


def event_dto(row):
    return {'id': row.id, 'eventType': row.event_type, 'aggregateType': row.aggregate_type,
        'aggregateId': row.aggregate_id, 'payload': row.payload, 'status': row.status,
        'attempts': row.attempts, 'cycleAttempts': row.cycle_attempts, 'version': row.version,
        'sequence': row.sequence,
        'error': row.last_error, 'createdAt': utc(row.created_at).isoformat() if row.created_at else None,
        'nextAttemptAt': utc(row.next_attempt_at).isoformat() if row.next_attempt_at else None,
        'publishedAt': utc(row.published_at).isoformat() if row.published_at else None}


@router.get('/stats')
async def stats(request: Request, session: AsyncSession = Depends(get_db_session)):
    counts = dict((await session.execute(select(Event.status, func.count()).where(tenant_events(current_tenant(request), session=session))
        .group_by(Event.status))).all())
    return {'pending': sum(counts.get(s, 0) for s in ('PENDING', 'RETRY', 'INFLIGHT')),
            'published': counts.get('PUBLISHED', 0), 'failed': counts.get('DEAD_LETTER', 0),
            'retrying': counts.get('RETRY', 0), 'inFlight': counts.get('INFLIGHT', 0)}


@router.get('/dead-letter')
async def dead_letters(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                       session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(Event).where(tenant_events(current_tenant(request), session=session), Event.status == 'DEAD_LETTER')
        .order_by(Event.created_at.asc().nulls_first(), Event.id).limit(limit).offset(offset))
    return [event_dto(row) for row in rows]


@router.get('/{event_id}')
async def get_event(event_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    row = await session.scalar(select(Event).where(Event.id == event_id, tenant_events(current_tenant(request), session=session)))
    if row is None:
        raise HTTPException(404, 'Event not found')
    return event_dto(row)


class RetryDTO(BaseModel):
    expectedVersion: int = Field(ge=0, strict=True)
    mutationId: str = Field(min_length=1, max_length=100)
    reason: str = Field(default='Operator requested redelivery', min_length=1, max_length=500, pattern=r'\S')


@router.post('/{event_id}/retry')
async def retry(event_id: str, data: RetryDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    key = 'outbox-retry:' + data.mutationId
    fingerprint = {'operation': 'retry-outbox', 'eventId': event_id, 'expectedVersion': data.expectedVersion, 'reason': data.reason}
    try:
        await locked_config(session, tenant)
        row = await session.scalar(select(Event).where(Event.id == event_id, tenant_events(tenant, session=session)).with_for_update())
        if row is None:
            raise HTTPException(404, 'Event not found')
        prior = await session.scalar(select(Action).where(Action.tenant_id == tenant, Action.mutation_id == key))
        if prior:
            if prior.actor_id != request.state.user_id or prior.request != fingerprint:
                raise HTTPException(409, 'Mutation ID was already used for another request')
            result = prior.result
            await session.rollback()
            return result
        if row.status != 'DEAD_LETTER' or row.version != data.expectedVersion:
            raise HTTPException(409, 'Only a current dead-letter event can be retried')
        changed = await session.execute(update(Event).where(Event.id == event_id, tenant_events(tenant, session=session),
            Event.status == 'DEAD_LETTER', Event.version == data.expectedVersion).values(
                status='PENDING', cycle_attempts=0, version=Event.version + 1, next_attempt_at=None,
                lease_token=None, lease_expires_at=None, last_error=None))
        if changed.rowcount != 1:
            raise HTTPException(409, 'Event version conflict')
        result = {'id': event_id, 'status': 'PENDING', 'version': data.expectedVersion + 1}
        session.add(Action(id=str(uuid.uuid4()), tenant_id=tenant, mutation_id=key,
            actor_id=request.state.user_id, request=fingerprint, result=result))
        await session.commit()
        return result
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, 'Concurrent retry; repeat the same mutation ID') from exc
    except Exception:
        await session.rollback()
        raise
