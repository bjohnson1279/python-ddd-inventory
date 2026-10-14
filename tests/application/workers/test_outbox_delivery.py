import asyncio
from datetime import datetime, timezone, timedelta
from dataclasses import replace
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.infrastructure.database import Base
from src.infrastructure.auth.identity import TenantModel
from src.infrastructure.messaging.models import OutboxEventModel as Event, OutboxStreamModel, stream_key
from src.infrastructure.messaging.settings import OutboxSettings
from src.application.workers.outbox_worker import OutboxWorker

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def delivery(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'outbox.sqlite').as_posix())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        session.add_all([TenantModel(id=t, name=t) for t in ('store-a', 'store-b')])
        await session.commit()
    clock = [NOW]
    settings = OutboxSettings(max_attempts=2, publish_timeout=0.05, lease_seconds=10,
        retry_base=2, retry_cap=8, poll_interval=0.01)
    publisher = AsyncMock()
    worker = OutboxWorker(publisher, session_factory=sessions, settings=settings, clock=lambda: clock[0])
    try:
        yield sessions, publisher, worker, clock
    finally:
        await worker.stop()
        await engine.dispose()


async def enqueue(sessions, identifier, aggregate='stock-1', tenant='store-a', **fields):
    async with sessions() as session:
        session.add(Event(id=identifier, aggregate_type='Stock', aggregate_id=aggregate,
            event_type='StockChanged', payload={'tenantId': tenant, 'quantity': 2}, created_at=NOW, **fields))
        await session.commit()


async def saved(sessions, identifier):
    async with sessions() as session:
        return await session.get(Event, identifier)


async def test_success_retains_published_record_and_tenant_partition_identity(delivery):
    sessions, publisher, worker, clock = delivery
    await enqueue(sessions, 'a-1')
    await enqueue(sessions, 'b-1', tenant='store-b')
    assert await worker._process_outbox() == 2
    assert publisher.publish.await_count == 2
    first = publisher.publish.await_args_list[0]
    assert first.args[0] == 'inventory.Stock.events'
    assert first.args[2]['eventId'] == 'a-1' and first.args[2]['tenantId'] == 'store-a'
    assert first.args[1] != publisher.publish.await_args_list[1].args[1]
    row = await saved(sessions, 'a-1')
    assert row.status == 'PUBLISHED' and row.attempts == 1 and row.published_at is not None
    assert row.lease_token is None
    assert await worker._process_outbox() == 0


async def test_failure_backs_off_blocks_only_its_aggregate_and_dead_letters(delivery, caplog):
    sessions, publisher, worker, clock = delivery
    for identifier, aggregate in [('a-1', 'bad'), ('a-2', 'bad'), ('z-1', 'good')]:
        await enqueue(sessions, identifier, aggregate)
    async def publish(topic, key, message, **kwargs):
        if message['eventId'] == 'a-1':
            raise RuntimeError('password=never-log-this')
    publisher.publish.side_effect = publish
    assert await worker._process_outbox() == 2
    assert (await saved(sessions, 'a-1')).status == 'RETRY'
    assert (await saved(sessions, 'a-2')).status == 'PENDING'
    assert (await saved(sessions, 'z-1')).status == 'PUBLISHED'
    assert await worker._process_outbox() == 0
    assert 'never-log-this' not in caplog.text
    clock[0] += timedelta(seconds=2)
    assert await worker._process_outbox() == 1
    row = await saved(sessions, 'a-1')
    assert row.status == 'DEAD_LETTER' and row.attempts == 2 and row.last_error == 'RuntimeError'
    assert (await saved(sessions, 'a-2')).status == 'PENDING'


async def test_expired_lease_recovers_and_stale_owner_cannot_overwrite(delivery):
    sessions, publisher, worker, clock = delivery
    await enqueue(sessions, 'a-1')
    original = await worker._claim('store-a')
    assert await worker._claim('store-a') is None
    clock[0] += timedelta(seconds=11)
    recovered = await worker._claim('store-a')
    assert original['token'] != recovered['token']
    assert not await worker._deliver(original)
    assert not await worker._update_claim(original, {'status': 'PUBLISHED'})
    assert await worker._deliver(recovered)
    assert publisher.publish.await_count == 1
    assert (await saved(sessions, 'a-1')).attempts == 2


async def test_repeated_crashes_exhaust_budget_and_preserve_dead_letter(delivery):
    sessions, publisher, worker, clock = delivery
    await enqueue(sessions, 'a-1')
    await worker._claim('store-a')
    clock[0] += timedelta(seconds=11)
    await worker._claim('store-a')
    clock[0] += timedelta(seconds=11)
    assert await worker._process_outbox() == 1
    row = await saved(sessions, 'a-1')
    assert row.status == 'DEAD_LETTER' and row.last_error == 'LeaseExpired'
    publisher.publish.assert_not_awaited()


async def test_two_workers_claim_once_and_network_wait_does_not_lock_database(delivery):
    sessions, publisher, worker, clock = delivery
    await enqueue(sessions, 'a-1')
    other = OutboxWorker(publisher, session_factory=sessions, settings=worker.settings, clock=lambda: clock[0])
    claims = await asyncio.gather(worker._claim('store-a'), other._claim('store-a'))
    assert sum(claim is not None for claim in claims) == 1
    entered, release = asyncio.Event(), asyncio.Event()
    async def publish(*args, **kwargs):
        entered.set()
        await release.wait()
    publisher.publish.side_effect = publish
    task = asyncio.create_task(worker._deliver(next(c for c in claims if c)))
    await entered.wait()
    await enqueue(sessions, 'z-1', 'another')
    release.set()
    assert await task
    assert (await saved(sessions, 'a-1')).status == 'PUBLISHED'


async def test_timeout_and_acknowledged_but_uncommitted_replay_use_same_event_id(delivery, monkeypatch):
    sessions, publisher, worker, clock = delivery
    await enqueue(sessions, 'a-1')
    worker.settings = replace(worker.settings, max_attempts=3)
    async def publish(*args, **kwargs):
        await asyncio.Event().wait()
    publisher.publish.side_effect = publish
    await worker._process_outbox()
    assert (await saved(sessions, 'a-1')).last_error == 'PublishTimeout'
    clock[0] += timedelta(seconds=2)
    publisher.publish.side_effect = None
    original = worker._update_claim
    async def crash_after_ack(claim, values, **kwargs):
        if values.get('status') == 'PUBLISHED':
            raise RuntimeError('Commit interrupted')
        return await original(claim, values, **kwargs)
    monkeypatch.setattr(worker, '_update_claim', crash_after_ack)
    with pytest.raises(RuntimeError, match='Commit interrupted'):
        await worker._process_outbox()
    assert (await saved(sessions, 'a-1')).status == 'INFLIGHT'
    clock[0] += timedelta(seconds=11)
    monkeypatch.setattr(worker, '_update_claim', original)
    await worker._process_outbox()
    assert (await saved(sessions, 'a-1')).status == 'PUBLISHED'
    assert {call.args[2]['eventId'] for call in publisher.publish.await_args_list} == {'a-1'}
    assert publisher.publish.await_args_list[-1] == publisher.publish.await_args_list[-2]


async def test_cancelled_delivery_leaves_a_recoverable_lease_and_shutdown_is_prompt(delivery):
    sessions, publisher, worker, clock = delivery
    await enqueue(sessions, 'a-1')
    entered = asyncio.Event()
    async def blocked(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    publisher.publish.side_effect = blocked
    task = asyncio.create_task(worker._process_outbox())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await saved(sessions, 'a-1')).status == 'INFLIGHT'
    clock[0] += timedelta(seconds=11)
    publisher.publish.side_effect = None
    delivered = asyncio.Event()
    async def after_publication(claim):
        delivered.set()
    worker.on_published = after_publication
    await worker.start()
    await asyncio.wait_for(delivered.wait(), 5)
    await worker.stop()
    assert not worker.running
    assert (await saved(sessions, 'a-1')).status == 'PUBLISHED'


async def test_poison_metadata_and_cache_hook_failures_do_not_fake_delivery(delivery):
    sessions, publisher, worker, clock = delivery
    await enqueue(sessions, 'bad')
    async with sessions() as session:
        row = await session.get(Event, 'bad')
        row.aggregate_type = '../invalid'
        await session.commit()
    await worker._process_outbox()
    assert (await saved(sessions, 'bad')).status == 'DEAD_LETTER'
    publisher.publish.assert_not_awaited()
    await enqueue(sessions, 'good', 'another')
    worker.on_published = AsyncMock(side_effect=RuntimeError('Redis unavailable'))
    await worker._process_outbox()
    assert (await saved(sessions, 'good')).status == 'PUBLISHED'
    assert await worker._process_outbox() == 0


async def test_unmigrated_database_fails_before_worker_start(tmp_path):
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'empty.sqlite').as_posix())
    worker = OutboxWorker(AsyncMock(), session_factory=async_sessionmaker(engine))
    try:
        from sqlalchemy.exc import OperationalError
        with pytest.raises(OperationalError):
            await worker.start()
        assert not worker.running
    finally:
        await engine.dispose()


async def test_polling_failure_is_observable_and_clears_after_recovery(delivery, monkeypatch):
    sessions, publisher, worker, clock = delivery
    blocked, recover = asyncio.Event(), asyncio.Event()
    calls = 0
    async def process():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('Never expose database credentials')
        blocked.set()
        await recover.wait()
        return 0
    monkeypatch.setattr(worker, '_process_outbox', process)
    await worker.start()
    await asyncio.wait_for(blocked.wait(), 1)
    assert worker.running and worker.last_poll_error == 'RuntimeError'
    recover.set()
    for _ in range(100):
        if worker.last_poll_at is not None:
            break
        await asyncio.sleep(0.001)
    assert worker.last_poll_error is None and worker.last_poll_at is not None
    await worker.stop()


async def test_database_sequence_beats_clock_skew_and_uuid_order(delivery):
    sessions, publisher, worker, clock = delivery
    async with sessions() as session:
        session.add(Event(id='z-first', aggregate_type='Stock', aggregate_id='stock', event_type='First',
            payload={'tenantId': 'store-a'}, created_at=NOW + timedelta(days=1)))
        await session.commit()
    async with sessions() as session:
        session.add(Event(id='a-second', aggregate_type='Stock', aggregate_id='stock', event_type='Second',
            payload={'tenantId': 'store-a'}, created_at=NOW - timedelta(days=1)))
        await session.commit()
    await worker._process_outbox()
    assert [call.args[2]['eventId'] for call in publisher.publish.await_args_list] == ['z-first', 'a-second']
    assert [call.args[2]['sequence'] for call in publisher.publish.await_args_list] == [1, 2]


async def test_sequence_allocation_rolls_back_with_the_producer_transaction(delivery):
    sessions, publisher, worker, clock = delivery
    key = stream_key('store-a', 'Stock', 'stock-1')
    async with sessions() as session:
        row = Event(id='rolled-back', aggregate_type='Stock', aggregate_id='stock-1', event_type='Changed', payload={'tenantId': 'store-a'})
        session.add(row)
        await session.flush()
        assert row.sequence == 1
        await session.rollback()
    await enqueue(sessions, 'committed')
    async with sessions() as session:
        assert (await session.get(OutboxStreamModel, key)).last_sequence == 1
        assert await session.get(Event, 'rolled-back') is None
    await worker._process_outbox()
    assert publisher.publish.await_args.args[2]['sequence'] == 1


async def test_nonfinite_payload_is_rejected_and_corrupt_legacy_json_cannot_stall_valid_events(delivery):
    sessions, publisher, worker, clock = delivery
    async with sessions() as session:
        session.add(Event(aggregate_type='Stock', aggregate_id='stock', event_type='Changed',
            payload={'tenantId': 'store-a', 'quantity': float('nan')}))
        with pytest.raises(ValueError, match='finite JSON'):
            await session.commit()
        await session.rollback()
        await session.execute(text("INSERT INTO outbox_events (id,aggregate_type,aggregate_id,event_type,payload,created_at,status) "
            "VALUES ('legacy-broken','Stock','stock','Changed','{broken',CURRENT_TIMESTAMP,'DEAD_LETTER')"))
        await session.commit()
    await enqueue(sessions, 'valid')
    assert await worker._process_outbox() == 1
    assert (await saved(sessions, 'valid')).status == 'PUBLISHED'


async def test_production_leases_and_acknowledgments_use_database_clock(delivery, monkeypatch):
    sessions, publisher, existing, clock = delivery
    await enqueue(sessions, 'server-clock')
    import src.application.workers.outbox_worker as module
    class SkewedClock:
        @staticmethod
        def now(*args):
            raise AssertionError('A worker clock must not determine lease safety')
    monkeypatch.setattr(module, 'datetime', SkewedClock)
    worker = OutboxWorker(publisher, session_factory=sessions, settings=existing.settings)
    claim = await worker._claim('store-a')
    before = await saved(sessions, 'server-clock')
    assert before.lease_expires_at > before.last_attempt_at
    assert await worker._deliver(claim)
    assert (await saved(sessions, 'server-clock')).published_at is not None
