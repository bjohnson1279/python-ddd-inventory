"""Leased, tenant-scoped, at-least-once delivery with aggregate head ordering."""
import asyncio
import copy
import json
import logging
import re
import uuid
from contextlib import suppress
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update, and_, or_, exists, false, text, case, func
from sqlalchemy.orm import aliased
from src.infrastructure.database import async_session
from src.infrastructure.auth.identity import TenantModel
from src.infrastructure.messaging.models import OutboxEventModel as Event, stream_key
from src.infrastructure.messaging.settings import OutboxSettings
from src.domain.product.layer_costing import utc

logger = logging.getLogger(__name__)


def tenant_events(tenant, model=Event, *, session):
    value = model.payload['tenantId'].as_string()
    if session.bind.dialect.name == 'sqlite':
        # Corrupt legacy JSON must not stall every valid tenant's queue/inbox.
        value = case((func.json_valid(model.payload), value), else_=None)
    return value == tenant


async def set_worker_tenant(session, tenant):
    if session.bind.dialect.name == 'postgresql':
        await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {'tenant': tenant})


class InvalidEventEnvelope(ValueError):
    pass


class OutboxWorker:
    def __init__(self, publisher, poll_interval=None, *, session_factory=None, settings=None,
                 clock=None, on_published=None):
        self.publisher = publisher
        self.settings = settings or OutboxSettings(poll_interval=poll_interval or 2.0)
        self.sessions = session_factory or async_session
        self.clock = clock
        self.on_published = on_published
        self._stop = asyncio.Event()
        self._task = None
        self.last_poll_error = None
        self.last_poll_at = None

    @property
    def running(self):
        return self._task is not None and not self._task.done()

    async def start(self):
        if self.running:
            return
        # Verify the explicit migration and worker permissions before advertising
        # a running service. No business rows are modified by this preflight.
        async with self.sessions() as session:
            await session.execute(select(Event).limit(0))
            await session.execute(select(TenantModel.id).limit(0))
            await session.execute(update(Event).where(false()).values(version=Event.version))
        self._stop.clear()
        self._task = asyncio.create_task(self.run(), name='inventory-outbox')

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._task), self.settings.publish_timeout + 5)
            except TimeoutError:
                self._task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._task
            finally:
                self._task = None

    async def run(self):
        while not self._stop.is_set():
            try:
                await self._process_outbox()
            except Exception as exc:
                self.last_poll_error = type(exc).__name__
                logger.error('Outbox polling failed (%s)', type(exc).__name__)
            else:
                self.last_poll_error = None
                self.last_poll_at = utc(self.clock()) if self.clock else datetime.now(timezone.utc)
            with suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), self.settings.poll_interval)

    async def _tenants(self):
        async with self.sessions() as session:
            query = select(TenantModel.id).order_by(TenantModel.id)
            if self.settings.tenant_ids is not None:
                query = query.where(TenantModel.id.in_(self.settings.tenant_ids))
            return list(await session.scalars(query))

    async def _now(self, session):
        if self.clock is not None:
            return utc(self.clock())
        expression = func.clock_timestamp() if session.bind.dialect.name == 'postgresql' else func.current_timestamp()
        return utc(await session.scalar(select(expression)))

    async def _claim(self, tenant):
        async with self.sessions() as session:
            await set_worker_tenant(session, tenant)
            if session.bind.dialect.name == 'sqlite':
                # Acquire SQLite's write lock before reading a candidate. PG uses
                # row locks below; both release before any broker/network I/O.
                await session.execute(update(Event).where(false()).values(version=Event.version))
            now = await self._now(session)
            older = aliased(Event)
            before = or_(and_(older.sequence.is_(None), Event.sequence.is_not(None)), older.sequence < Event.sequence,
                and_(older.sequence.is_(None), Event.sequence.is_(None), older.id < Event.id))
            same_stream = or_(older.stream_key == Event.stream_key,
                and_(or_(older.stream_key.is_(None), Event.stream_key.is_(None)),
                    older.aggregate_type == Event.aggregate_type, older.aggregate_id == Event.aggregate_id))
            blocked = exists(select(older.id).where(tenant_events(tenant, older, session=session),
                same_stream, older.status != 'PUBLISHED', before))
            eligible = or_(and_(Event.status.in_(('PENDING', 'RETRY')),
                                or_(Event.next_attempt_at.is_(None), Event.next_attempt_at <= now)),
                           and_(Event.status == 'INFLIGHT', Event.lease_expires_at <= now))
            row = await session.scalar(select(Event).where(tenant_events(tenant, session=session), eligible, ~blocked)
                .order_by(Event.created_at.asc().nulls_first(), Event.id).limit(1).with_for_update(skip_locked=True))
            if row is None:
                return None
            if row.cycle_attempts >= self.settings.max_attempts:
                row.status, row.last_error = 'DEAD_LETTER', 'LeaseExpired' if row.status == 'INFLIGHT' else row.last_error or 'AttemptsExhausted'
                row.lease_token, row.lease_expires_at, row.next_attempt_at = None, None, None
                row.version += 1
                await session.commit()
                return {'expired': True}
            token = uuid.uuid4().hex
            now = await self._now(session)
            row.status, row.lease_token = 'INFLIGHT', token
            row.lease_expires_at = now + timedelta(seconds=self.settings.lease_seconds)
            row.attempts, row.cycle_attempts, row.version = row.attempts + 1, row.cycle_attempts + 1, row.version + 1
            row.last_attempt_at = now
            claim = {'id': row.id, 'tenant': tenant, 'token': token, 'cycleAttempts': row.cycle_attempts,
                'aggregateType': row.aggregate_type, 'aggregateId': row.aggregate_id,
                'eventType': row.event_type, 'payload': copy.deepcopy(row.payload), 'createdAt': row.created_at,
                'streamKey': row.stream_key, 'sequence': row.sequence}
            await session.commit()
            return claim

    async def _update_claim(self, claim, values, *, live=False, retry_delay=None):
        async with self.sessions() as session:
            await set_worker_tenant(session, claim['tenant'])
            now = await self._now(session)
            values = dict(values)
            query = update(Event).where(Event.id == claim['id'], tenant_events(claim['tenant'], session=session),
                Event.status == 'INFLIGHT', Event.lease_token == claim['token'])
            if live:
                query = query.where(Event.lease_expires_at > now)
                values['lease_expires_at'] = now + timedelta(seconds=self.settings.lease_seconds)
            if retry_delay is not None:
                values['next_attempt_at'] = now + timedelta(seconds=retry_delay)
            if values.get('status') == 'PUBLISHED':
                values['published_at'] = now
            changed = await session.execute(query.values(**values, version=Event.version + 1))
            await session.commit()
            return changed.rowcount == 1

    def _envelope(self, claim):
        if (not isinstance(claim['payload'], dict) or claim['payload'].get('tenantId') != claim['tenant']
            or not claim['createdAt'] or not claim['aggregateId'] or not claim['eventType']
            or type(claim['sequence']) is not int or claim['sequence'] <= 0
            or claim['streamKey'] != stream_key(claim['tenant'], claim['aggregateType'], claim['aggregateId'])
            or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', claim['aggregateType'] or '')):
            raise InvalidEventEnvelope('Invalid event metadata')
        envelope = {'schemaVersion': 1, 'eventId': claim['id'], 'tenantId': claim['tenant'],
            'sequence': claim['sequence'],
            'aggregateType': claim['aggregateType'], 'aggregateId': claim['aggregateId'],
            'eventType': claim['eventType'], 'occurredAt': utc(claim['createdAt']).isoformat(), 'payload': claim['payload']}
        try:
            json.dumps(envelope, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise InvalidEventEnvelope('Invalid JSON event') from exc
        return envelope

    async def _deliver(self, claim):
        renewed = await self._update_claim(claim, {}, live=True)
        if not renewed:
            return False
        try:
            envelope = self._envelope(claim)
            topic = self.settings.topic_prefix + '.' + claim['aggregateType'] + '.events'
            key = json.dumps([claim['tenant'], claim['aggregateType'], claim['aggregateId']], separators=(',', ':'))
            await asyncio.wait_for(self.publisher.publish(topic, key, envelope,
                headers=[('event-id', claim['id'].encode()), ('tenant-id', claim['tenant'].encode()),
                         ('event-type', claim['eventType'].encode())]), self.settings.publish_timeout)
        except Exception as exc:
            terminal = isinstance(exc, InvalidEventEnvelope) or claim['cycleAttempts'] >= self.settings.max_attempts
            error = 'PublishTimeout' if isinstance(exc, TimeoutError) else type(exc).__name__[:100]
            delay = min(self.settings.retry_cap, self.settings.retry_base * 2 ** (claim['cycleAttempts'] - 1))
            await self._update_claim(claim, {'status': 'DEAD_LETTER' if terminal else 'RETRY', 'last_error': error,
                'next_attempt_at': None, 'lease_token': None, 'lease_expires_at': None},
                retry_delay=None if terminal else delay)
            logger.warning('Outbox event %s failed (%s)', claim['id'], error)
            return False
        published = await self._update_claim(claim, {'status': 'PUBLISHED',
            'last_error': None, 'next_attempt_at': None, 'lease_token': None, 'lease_expires_at': None})
        if published and self.on_published is not None:
            try:
                await self.on_published(claim)
            except Exception as exc:
                # A secondary cache/observer failure never undoes a broker acknowledgment.
                logger.warning('Post-publication hook failed for %s (%s)', claim['id'], type(exc).__name__)
        return published

    async def _process_outbox(self):
        handled = 0
        for tenant in await self._tenants():
            for _ in range(self.settings.batch_size):
                if self._stop.is_set():
                    return handled
                claim = await self._claim(tenant)
                if claim is None:
                    break
                handled += 1
                if not claim.get('expired'):
                    await self._deliver(claim)
        return handled

