"""Incoming worker scenarios adapted to leased delivery and nonblocking start."""
import asyncio
from unittest.mock import AsyncMock
import pytest
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
