import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.application.workers.outbox_worker import OutboxWorker
from src.infrastructure.messaging.models import OutboxEventModel

@pytest.mark.asyncio
async def test_outbox_worker_process_outbox_empty():
    publisher = AsyncMock()
    worker = OutboxWorker(publisher)

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = mock_result

    with patch("src.application.workers.outbox_worker.async_session") as mock_async_session:
        mock_async_session.return_value.__aenter__.return_value = mock_session
        await worker._process_outbox()

    publisher.publish.assert_not_called()

@pytest.mark.asyncio
async def test_outbox_worker_process_outbox_with_events():
    publisher = AsyncMock()
    worker = OutboxWorker(publisher)

    event1 = OutboxEventModel(id="1", aggregate_type="product", aggregate_id="p1", payload={"key": "v1"})
    event2 = OutboxEventModel(id="2", aggregate_type="product", aggregate_id="p1", payload={"key": "v2"})
    event3 = OutboxEventModel(id="3", aggregate_type="order", aggregate_id="o1", payload={"key": "v3"})

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [event1, event2, event3]
    mock_session.execute.return_value = mock_result

    with patch("src.application.workers.outbox_worker.async_session") as mock_async_session, \
         patch("src.application.workers.outbox_worker.DistributedCache.invalidate", new_callable=AsyncMock) as mock_invalidate:

        mock_async_session.return_value.__aenter__.return_value = mock_session
        await worker._process_outbox()

        # Check publisher calls
        assert publisher.publish.call_count == 3
        publisher.publish.assert_any_call(topic="inventory.product.events", key="p1", message={"key": "v1"})
        publisher.publish.assert_any_call(topic="inventory.product.events", key="p1", message={"key": "v2"})
        publisher.publish.assert_any_call(topic="inventory.order.events", key="o1", message={"key": "v3"})

        # Check cache invalidations (should be deduplicated to product:p1 and order:o1)
        assert mock_invalidate.call_count == 2
        invalidated_keys = {call.args[0] for call in mock_invalidate.call_args_list}
        assert invalidated_keys == {"product:p1", "order:o1"}

        # Check session commit
        mock_session.commit.assert_called_once()
"""Incoming worker scenarios adapted to leased delivery and nonblocking start."""
import asyncio
from unittest.mock import AsyncMock
from tests.application.workers.test_outbox_delivery import delivery, enqueue, saved

pytestmark = pytest.mark.asyncio

async def test_process_outbox_success(delivery):
    sessions, publisher, worker, clock = delivery
    worker.on_published = AsyncMock()
    await enqueue(sessions, 'event-1', aggregate='product')
    await enqueue(sessions, 'event-2', aggregate='inventory')
    assert await worker._process_outbox() == 2
    assert publisher.publish.await_count == 2
    assert {call.args[2]['eventId'] for call in publisher.publish.await_args_list} == {'event-1','event-2'}
    assert worker.on_published.await_count == 2
    assert (await saved(sessions, 'event-1')).status == 'PUBLISHED'
    assert (await saved(sessions, 'event-2')).status == 'PUBLISHED'

async def test_process_outbox_empty(delivery):
    sessions, publisher, worker, clock = delivery
    worker.on_published = AsyncMock()
    assert await worker._process_outbox() == 0
    publisher.publish.assert_not_awaited()
    worker.on_published.assert_not_awaited()

async def test_start_and_stop_lifecycle(delivery, monkeypatch):
    sessions, publisher, worker, clock = delivery
    polled = asyncio.Event()
    async def process():
        polled.set()
        return 0
    monkeypatch.setattr(worker, '_process_outbox', process)
    await worker.start()
    await asyncio.wait_for(polled.wait(), 5)
    assert worker.running
    await worker.stop()
    assert not worker.running

async def test_start_handles_exception(delivery, monkeypatch, caplog):
    sessions, publisher, worker, clock = delivery
    recovered = asyncio.Event()
    attempts = 0
    async def process():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('private connection detail')
        recovered.set()
        return 0
    monkeypatch.setattr(worker, '_process_outbox', process)
    await worker.start()
    await asyncio.wait_for(recovered.wait(), 5)
    await worker.stop()
    assert attempts >= 2 and worker.last_poll_error is None
    assert 'RuntimeError' in caplog.text
    assert 'private connection detail' not in caplog.text
