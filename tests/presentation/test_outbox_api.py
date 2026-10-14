from datetime import datetime, timezone
import pytest
from sqlalchemy import select, func
from src.infrastructure.messaging.models import OutboxEventModel as Event
from src.infrastructure.transfer_models import InventoryActionModel
from tests.presentation.test_tenant_security import staff, other_store

pytestmark = pytest.mark.asyncio


async def record(db, identifier, tenant='tenant-1', status='DEAD_LETTER'):
    db.add(Event(id=identifier, aggregate_type='Stock', aggregate_id='stock', event_type='StockChanged',
        payload={'tenantId': tenant, 'quantity': 2}, status=status, attempts=5, cycle_attempts=5,
        last_error='PublishTimeout', created_at=datetime.now(timezone.utc)))
    await db.commit()


async def test_stats_dead_letters_and_retry_are_owned_and_not_public(client, db_session):
    await record(db_session, 'failed')
    await record(db_session, 'pending', status='RETRY')
    await record(db_session, 'published', status='PUBLISHED')
    _, foreign = other_store(client)
    await record(db_session, 'foreign', tenant='other')
    assert client.get('/outbox/stats').json() == {'pending': 1, 'published': 1, 'failed': 1, 'retrying': 1, 'inFlight': 0}
    assert [row['id'] for row in client.get('/api/outbox/dead-letter').json()] == ['failed']
    assert client.get('/outbox/foreign').status_code == 404
    assert client.get('/outbox/failed', headers=foreign).status_code == 404
    assert client.post('/outbox/foreign/retry', json={'expectedVersion': 0, 'mutationId': 'foreign'}).status_code == 404
    _, reader = staff(client)
    assert client.get('/outbox/stats', headers=reader).status_code == 403
    assert client.post('/outbox/failed/retry', headers=reader, json={'expectedVersion': 0, 'mutationId': 'reader'}).status_code == 403
    assert client.get('/outbox/stats', headers={'Authorization': ''}).status_code == 401
    assert client.get('/outbox/dead-letter?limit=0').status_code == 422


async def test_retry_is_versioned_actor_bound_and_replays_after_delivery(client, db_session):
    await record(db_session, 'failed')
    payload = {'expectedVersion': 0, 'mutationId': 'retry-once', 'reason': 'Broker recovered'}
    first = client.post('/outbox/failed/retry', json=payload)
    assert first.status_code == 200, first.text
    assert first.json()['version'] == 1
    row = await db_session.get(Event, 'failed')
    assert row.attempts == 5 and row.cycle_attempts == 0 and row.status == 'PENDING'
    row.status = 'PUBLISHED'
    await db_session.commit()
    assert client.post('/api/outbox/failed/retry', json=payload).json() == first.json()
    assert client.post('/outbox/failed/retry', json={**payload, 'reason': 'Changed'}).status_code == 409
    assert client.post('/outbox/failed/retry', json={'expectedVersion': 1, 'mutationId': 'again'}).status_code == 409
    _, manager = staff(client, role='MANAGER', email='manager@example.com')
    assert client.post('/outbox/failed/retry', headers=manager, json=payload).status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(InventoryActionModel)) == 1
    assert client.get('/outbox/failed').json()['status'] == 'PUBLISHED'


async def test_retry_failure_rolls_back_event_and_audit(client, db_session, monkeypatch):
    await record(db_session, 'failed')
    original = db_session.add
    def fail(row, *args, **kwargs):
        if isinstance(row, InventoryActionModel):
            raise RuntimeError('Injected retry audit failure')
        original(row, *args, **kwargs)
    monkeypatch.setattr(db_session, 'add', fail)
    with pytest.raises(RuntimeError, match='retry audit'):
        client.post('/outbox/failed/retry', json={'expectedVersion': 0, 'mutationId': 'retry'})
    monkeypatch.setattr(db_session, 'add', original)
    assert client.get('/outbox/failed').json()['status'] == 'DEAD_LETTER'
    assert client.get('/outbox/failed').json()['version'] == 0


async def test_invalid_stale_and_published_retries_are_rejected(client, db_session):
    await record(db_session, 'failed')
    assert client.post('/outbox/failed/retry', json={}).status_code == 422
    assert client.post('/outbox/failed/retry', json={'expectedVersion': 1, 'mutationId': 'stale'}).status_code == 409
    assert client.post('/outbox/failed/retry', json={'expectedVersion': True, 'mutationId': 'invalid'}).status_code == 422
    assert client.get('/outbox/stats?tenantId=other').status_code == 403
