"""The same return/replay contract on independent SQLite and restricted PostgreSQL sessions."""
import asyncio
import os
import uuid
import pytest
from fastapi import Request
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url, URL
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.presentation.main import app
from src.infrastructure.database import Base, get_db_session
from scripts import migrate_identity_catalog as migration


@pytest.mark.asyncio
@pytest.mark.parametrize('backend', ['sqlite', 'postgres'])
async def test_return_replay_competing_receipts_and_tenant_ownership(tmp_path, monkeypatch, backend):
    owner = None
    if backend == 'sqlite':
        runtime = create_async_engine(URL.create('sqlite+aiosqlite', database=str(tmp_path / 'returns.sqlite')))
        async with runtime.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        database = os.getenv('COSTING_TEST_DATABASE_URL')
        if not database:
            pytest.skip('Set COSTING_TEST_DATABASE_URL to disposable local costing_test database')
        url = make_url(database)
        if url.host not in ('127.0.0.1', 'localhost') or url.database != 'costing_test':
            raise ValueError('Return tests require disposable local costing_test database')
        suffix = uuid.uuid4().hex
        schema, role = 'returns_' + suffix, 'inspector_' + suffix
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
            tenant = getattr(request.state, 'tenant_id', None)
            if backend == 'postgres' and tenant:
                await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {'tenant': tenant})
            yield session
    previous = app.dependency_overrides.get(get_db_session)
    app.dependency_overrides[get_db_session] = dependency
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            async def enroll(tenant):
                setup = await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant,
                    'adminName': 'Admin', 'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
                assert setup.status_code == 201, setup.text
                login = await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com', 'password': 'test-password'})
                assert login.status_code == 200, login.text
                headers = {'Authorization': 'Bearer ' + login.json()['token']}
                product = await client.post('/products', headers=headers, json={'sku': 'RET', 'name': 'Item', 'price_amount': '5.00'})
                assert product.status_code == 201, product.text
                receipt = await client.post('/inventory/receive', headers=headers, json={'sku': 'RET', 'quantity': 2, 'unitCostCents': 100})
                assert receipt.status_code == 200, receipt.text
                sold = await client.post('/inventory/dispatch', headers=headers, json={'sku': 'RET', 'quantity': 2, 'mutationId': 'sale'})
                assert sold.status_code == 200, sold.text
                movements = (await client.get('/inventory/movements', headers=headers)).json()
                source = next(m['id'] for m in movements if m['mutationId'] == 'sale')
                return headers, source, sold.json()['consumedLayers'][0]['layerId'], receipt.json()['id']
            a, source_a, layer_a, stock_a = await enroll('store-a')
            b, source_b, layer_b, stock_b = await enroll('store-b')
            data = {'sourceMovementId': source_a, 'items': [{'layerId': layer_a, 'quantity': 2, 'disposition': 'RESTOCK'}],
                    'reason': 'Customer return', 'mutationId': 'rma'}
            assert (await client.post('/returns/rmas', headers=b, json=data)).status_code == 404
            created = await client.post('/returns/rmas', headers=a, json=data)
            assert created.status_code == 201, created.text
            rma_id = created.json()['id']
            approved = await client.post('/returns/rmas/' + rma_id + '/authorize', headers=a,
                json={'expectedVersion': 0, 'reason': 'Verified', 'mutationId': 'approve'})
            assert approved.status_code == 200, approved.text
            endpoint = '/returns/rmas/' + rma_id + '/receive'
            payload = {'receiptId': 'first', 'expectedVersion': 1, 'items': [{'layerId': layer_a, 'quantity': 1}]}
            same = await asyncio.gather(*(client.post(endpoint, headers=a, json=payload) for _ in range(2)))
            assert [r.status_code for r in same] == [200, 200], [r.text for r in same]
            assert same[0].json() == same[1].json()
            assert (await client.get('/inventory', headers=a)).json()[0]['quantity'] == 1
            competing = await asyncio.gather(*(client.post(endpoint, headers=a, json={
                'receiptId': 'last-' + str(i), 'expectedVersion': 2, 'items': [{'layerId': layer_a, 'quantity': 1}]}) for i in range(2)))
            assert sorted(r.status_code for r in competing) == [200, 409], [r.text for r in competing]
            assert (await client.get('/accounting/valuation-report', headers=a)).json()[0]['totalValueCents'] == 200
            assert (await client.get('/returns/rmas', headers=b)).json() == []
            if backend == 'postgres':
                async with runtime.begin() as conn:
                    assert await conn.scalar(text('SELECT count(*) FROM python_returns')) == 0
                    await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                    assert await conn.scalar(text('SELECT count(*) FROM python_returns')) == 1
                    assert await conn.scalar(text("SELECT count(*) FROM python_stock_movements WHERE operation='customer-return'")) == 2
                with pytest.raises(DBAPIError):
                    async with runtime.begin() as conn:
                        await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                        await conn.execute(text("INSERT INTO python_returns "
                            "(id,tenant_id,source_movement_id,kind,status,version,reason,items,created_by,created_at) "
                            "VALUES ('bad-origin','store-a',:source,'CUSTOMER','REQUESTED',0,'bad','[]','attacker',now())"), {'source': source_b})
                with pytest.raises(DBAPIError):
                    async with runtime.begin() as conn:
                        await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                        await conn.execute(text("INSERT INTO python_return_quarantine "
                            "(id,tenant_id,return_id,stock_id,sku,location_id,quantity,reason,layer_ids,status,version,created_at) "
                            "VALUES ('bad-stock','store-a',:rma,:stock,'RET','default',1,'bad','[]','QUARANTINED',0,now())"),
                            {'rma': rma_id, 'stock': stock_b})
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous
        await runtime.dispose()
        if owner:
            await owner.dispose()
