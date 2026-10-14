import asyncio
import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from fastapi import Request
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url, URL
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.presentation.main import app
from src.infrastructure.database import Base, get_db_session
from src.infrastructure.messaging.models import OutboxEventModel as Event
from src.infrastructure.messaging.settings import OutboxSettings
from src.application.workers.outbox_worker import OutboxWorker, set_worker_tenant
from scripts import migrate_identity_catalog as migration


@pytest_asyncio.fixture(params=['sqlite', 'postgres'])
async def outbox_database(tmp_path, monkeypatch, request):
    backend, owner = request.param, None
    if backend == 'sqlite':
        runtime = create_async_engine(URL.create('sqlite+aiosqlite', database=str(tmp_path / 'outbox-contract.sqlite')))
        async with runtime.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        database = os.getenv('COSTING_TEST_DATABASE_URL')
        if not database:
            pytest.skip('Set COSTING_TEST_DATABASE_URL to disposable local costing_test database')
        url = make_url(database)
        if url.host not in ('127.0.0.1', 'localhost') or url.database != 'costing_test':
            raise ValueError('Outbox contracts require disposable local costing_test database')
        suffix = uuid.uuid4().hex
        schema, role = 'outbox_' + suffix, 'courier_' + suffix
        bootstrap = create_async_engine(url)
        async with bootstrap.begin() as conn:
            await conn.execute(text('CREATE SCHEMA ' + schema))
            await conn.execute(text("CREATE ROLE " + role + " LOGIN PASSWORD 'test-role-only' NOSUPERUSER NOBYPASSRLS"))
        await bootstrap.dispose()
        owner = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
        monkeypatch.setattr(migration, 'engine', owner)
        await migration.migrate()
        await migration.migrate()
        async with owner.begin() as conn:
            await conn.execute(text('GRANT USAGE ON SCHEMA ' + schema + ' TO ' + role))
            await conn.execute(text('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ' + schema + ' TO ' + role))
        runtime = create_async_engine(url.set(username=role, password='test-role-only'),
            connect_args={'server_settings': {'search_path': schema}})
    sessions = async_sessionmaker(runtime, expire_on_commit=False)
    async def dependency(request: Request):
        async with sessions() as session:
            if backend == 'postgres' and getattr(request.state, 'tenant_id', None):
                await set_worker_tenant(session, request.state.tenant_id)
            yield session
    previous = app.dependency_overrides.get(get_db_session)
    app.dependency_overrides[get_db_session] = dependency
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield client, sessions, runtime, backend
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous
        await runtime.dispose()
        if owner:
            await owner.dispose()


@pytest.mark.asyncio
async def test_multi_worker_ordering_retry_and_restricted_tenant_delivery(outbox_database):
    client, sessions, runtime, backend = outbox_database
    async def enroll(tenant):
        setup = await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant, 'adminName': 'Admin',
            'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
        assert setup.status_code == 201, setup.text
        login = await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com', 'password': 'test-password'})
        assert login.status_code == 200, login.text
        headers = {'Authorization': 'Bearer ' + login.json()['token']}
        product = await client.post('/products', headers=headers, json={'sku': 'EVT', 'name': 'Item', 'price_amount': '5.00'})
        assert product.status_code == 201, product.text
        receipt = await client.post('/inventory/receive', headers=headers, json={'sku': 'EVT', 'quantity': 2, 'unitCostCents': 100})
        assert receipt.status_code == 200, receipt.text
        return headers
    a, b = await enroll('store-a'), await enroll('store-b')
    sold = await client.post('/inventory/dispatch', headers=a, json={'sku': 'EVT', 'quantity': 1})
    assert sold.status_code == 200, sold.text
    entered, release = asyncio.Event(), asyncio.Event()
    delivered = []
    failures = set()
    async def publish(topic, key, message, **kwargs):
        if message['eventId'] in failures:
            raise RuntimeError('Simulated broker rejection')
        delivered.append(message)
        if message['tenantId'] == 'store-a' and len(delivered) == 1:
            entered.set()
            await release.wait()
    publisher = AsyncMock()
    publisher.publish.side_effect = publish
    settings = OutboxSettings(max_attempts=1, publish_timeout=30, lease_seconds=60)
    first = OutboxWorker(publisher, session_factory=sessions, settings=settings)
    second = OutboxWorker(publisher, session_factory=sessions, settings=settings)
    task = asyncio.create_task(first._process_outbox())
    await asyncio.wait_for(entered.wait(), 10)
    try:
        # A slow broker acknowledgment holds no DB locks and cannot let the next
        # event for the same aggregate overtake the leased head.
        allocated = await client.post('/inventory/allocate', headers=a, json={'sku': 'EVT', 'quantity': 1})
        assert allocated.status_code == 200, allocated.text
        assert await second._process_outbox() == 1
        assert [event['tenantId'] for event in delivered] == ['store-a', 'store-b']
    finally:
        release.set()
        await task
    assert len(delivered) == 4 and len({event['eventId'] for event in delivered}) == 4
    owned = [event for event in delivered if event['tenantId'] == 'store-a']
    assert [event['payload']['operation'] for event in owned] == ['receive', 'dispatch', 'allocate']
    assert (await client.get('/outbox/stats', headers=a)).json()['published'] == 3
    assert (await client.get('/outbox/stats', headers=b)).json()['published'] == 1
    poison_id = uuid.uuid4().hex
    async with sessions() as session:
        await set_worker_tenant(session, 'store-a')
        session.add(Event(id=poison_id, aggregate_type='Stock', aggregate_id='poison', event_type='TestEvent',
            payload={'tenantId': 'store-a'}, created_at=datetime.now(timezone.utc)))
        await session.commit()
    failures.add(poison_id)
    await first._process_outbox()
    dead = (await client.get('/outbox/dead-letter', headers=a)).json()[0]
    assert dead['id'] == poison_id and dead['status'] == 'DEAD_LETTER'
    assert (await client.get('/outbox/' + poison_id, headers=b)).status_code == 404
    endpoint = '/outbox/' + poison_id + '/retry'
    payload = {'expectedVersion': dead['version'], 'mutationId': 'retry-once', 'reason': 'Broker recovered'}
    retries = await asyncio.gather(*(client.post(endpoint, headers=a, json=payload) for _ in range(2)))
    assert [r.status_code for r in retries] == [200, 200], [r.text for r in retries]
    assert retries[0].json() == retries[1].json()
    failures.clear()
    await first._process_outbox()
    assert (await client.post(endpoint, headers=a, json=payload)).json() == retries[0].json()
    assert (await client.get('/outbox/stats', headers=a)).json()['published'] == 4
    stock = (await client.get('/inventory', headers=a)).json()[0]
    assert stock['quantity'] == 1 and stock['allocated'] == 1
    if backend == 'postgres':
        async with runtime.begin() as conn:
            assert await conn.scalar(text('SELECT count(*) FROM outbox_events')) == 0
            assert (await conn.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user'))).one() == (False, False)
            assert await conn.scalar(text("SELECT relforcerowsecurity FROM pg_class WHERE oid='outbox_events'::regclass"))
            await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
            assert await conn.scalar(text("SELECT count(*) FROM outbox_events WHERE status='PUBLISHED'")) == 4
        with pytest.raises(DBAPIError):
            async with sessions() as session:
                await set_worker_tenant(session, 'store-a')
                session.add(Event(aggregate_type='Stock', aggregate_id='foreign', event_type='Injected', payload={'tenantId': 'store-b'}))
                await session.commit()
