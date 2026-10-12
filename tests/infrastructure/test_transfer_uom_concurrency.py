"""Independent-request transfer/UOM contracts, including restricted PostgreSQL RLS."""
import asyncio
import os
import uuid
import pytest
import pytest_asyncio
from fastapi import Request
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url, URL
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.presentation.main import app
from src.infrastructure.database import Base, get_db_session
from scripts import migrate_identity_catalog as migration


@pytest_asyncio.fixture(params=['sqlite', 'postgres'])
async def transfer_database(tmp_path, monkeypatch, request):
    backend, owner = request.param, None
    if backend == 'sqlite':
        runtime = create_async_engine(URL.create('sqlite+aiosqlite', database=str(tmp_path / 'transfers.sqlite')))
        async with runtime.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        database = os.getenv('COSTING_TEST_DATABASE_URL')
        if not database:
            pytest.skip('Set COSTING_TEST_DATABASE_URL to disposable local costing_test database')
        url = make_url(database)
        if url.host not in ('127.0.0.1', 'localhost') or url.database != 'costing_test':
            raise ValueError('Transfer tests require disposable local costing_test database')
        suffix = uuid.uuid4().hex
        schema, role = 'transfers_' + suffix, 'operator_' + suffix
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
                await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {'tenant': request.state.tenant_id})
            yield session
    previous = app.dependency_overrides.get(get_db_session)
    app.dependency_overrides[get_db_session] = dependency
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            yield client, runtime, backend
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous
        await runtime.dispose()
        if owner:
            await owner.dispose()


@pytest.mark.asyncio
async def test_transfer_competing_dispatches_duplicate_receipts_and_uom_updates(transfer_database):
    client, runtime, backend = transfer_database
    async def enroll(tenant):
        result = await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant,
            'adminName': 'Admin', 'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
        assert result.status_code == 201, result.text
        login = await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com', 'password': 'test-password'})
        assert login.status_code == 200, login.text
        headers = {'Authorization': 'Bearer ' + login.json()['token']}
        product = await client.post('/products', headers=headers, json={'sku': 'MOVE', 'name': 'Item', 'price_amount': '5.00'})
        assert product.status_code == 201, product.text
        receipt = await client.post('/inventory/receive', headers=headers, json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 24, 'unitCostCents': 11})
        assert receipt.status_code == 200, receipt.text
        config = await client.post('/uom/configurations', headers=headers, json={'sku': 'MOVE', 'expectedVersion': 0,
            'rules': [{'unit': 'case', 'factorToBase': '24'}], 'mutationId': 'configure'})
        assert config.status_code == 200, config.text
        return headers
    a, b = await enroll('store-a'), await enroll('store-b')
    creates = [await client.post('/inventory/transfers', headers=a, json={'sourceLocationId': 'shop',
        'destinationLocationId': 'store', 'items': [{'sku': 'MOVE', 'quantity': 24}], 'mutationId': 'create-' + str(i)}) for i in range(2)]
    assert all(r.status_code == 201 for r in creates), [r.text for r in creates]
    ids = [r.json()['id'] for r in creates]
    assert (await client.get('/inventory/transfers/' + ids[0], headers=b)).status_code == 404
    dispatches = await asyncio.gather(*(client.post('/inventory/transfers/' + identifier + '/dispatch', headers=a,
        json={'expectedVersion': 0, 'sourceVersions': {'MOVE': 1}, 'reason': 'Truck', 'mutationId': 'dispatch-' + str(i)}) for i, identifier in enumerate(ids)))
    assert sorted(r.status_code for r in dispatches) == [200, 409], [r.text for r in dispatches]
    shipped = next(r.json() for r in dispatches if r.status_code == 200)
    payload = {'expectedVersion': 1, 'reason': 'Arrived', 'mutationId': 'receipt-once'}
    endpoint = '/inventory/transfers/' + shipped['id'] + '/receive'
    received = await asyncio.gather(*(client.post(endpoint, headers=a, json=payload) for _ in range(2)))
    assert [r.status_code for r in received] == [200, 200], [r.text for r in received]
    assert received[0].json() == received[1].json()
    rows = (await client.get('/inventory', headers=a)).json()
    assert sum(row['quantity'] for row in rows) == 24
    assert next(row for row in rows if row['locationId'] == 'store')['quantity'] == 24
    assert (await client.get('/accounting/valuation-report', headers=a)).json()[0]['totalOwnedValueCents'] == 264
    assert (await client.get('/inventory/transfers', headers=b)).json() == []
    updates = await asyncio.gather(*(client.post('/uom/configurations', headers=a, json={'sku': 'MOVE',
        'expectedVersion': 1, 'rules': [{'unit': 'case', 'factorToBase': str(size)}], 'mutationId': 'update-' + str(size)}) for size in (12, 24)))
    assert sorted(r.status_code for r in updates) == [200, 409], [r.text for r in updates]
    assert (await client.get('/uom/configurations/MOVE', headers=a)).json()['version'] == 2
    assert (await client.get('/uom/configurations/MOVE', headers=b)).json()['version'] == 1
    if backend == 'postgres':
        async with runtime.begin() as conn:
            for table in ('python_stock_transfers', 'python_inventory_actions', 'python_uom_configs'):
                assert await conn.scalar(text('SELECT count(*) FROM ' + table)) == 0
            await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-b', true)"))
            assert await conn.scalar(text('SELECT count(*) FROM python_stock_transfers')) == 0
            assert await conn.scalar(text('SELECT count(*) FROM python_uom_configs')) == 1
        product = await client.post('/products', headers=a, json={'sku': 'ONLY_A', 'name': 'Private', 'price_amount': '1.00'})
        assert product.status_code == 201
        with pytest.raises(DBAPIError):
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-b', true)"))
                await conn.execute(text("INSERT INTO python_uom_configs (id,tenant_id,sku,base_unit,category,purchase_unit,sale_unit,rules,version) "
                    "VALUES ('bad-parent','store-b','ONLY_A','ea','discrete','ea','ea','[]',0)"))
        with pytest.raises(DBAPIError):
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-b', true)"))
                await conn.execute(text("INSERT INTO python_stock_transfers (id,tenant_id,source_location_id,destination_location_id,status,version,items,created_by,created_at) "
                    "VALUES ('bad-transfer','store-b','shop','store','DRAFT',0,'[{\"sku\":\"ONLY_A\",\"quantity\":1}]','attacker',now())"))
