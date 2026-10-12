"""Restricted PostgreSQL role and concurrent PO receipt contracts."""
import asyncio
import os
import uuid
import pytest
from fastapi import Request
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.presentation.main import app
from src.infrastructure.database import get_db_session
from scripts import migrate_identity_catalog as migration


@pytest.mark.asyncio
async def test_postgres_procurement_concurrency_and_parent_ownership(monkeypatch):
    database = os.getenv('COSTING_TEST_DATABASE_URL')
    if not database:
        pytest.skip('Set COSTING_TEST_DATABASE_URL to the disposable local costing_test database')
    url = make_url(database)
    if url.host not in ('127.0.0.1', 'localhost') or url.database != 'costing_test':
        raise ValueError('Procurement tests require the disposable local costing_test database')
    suffix = uuid.uuid4().hex
    schema, role = 'procurement_' + suffix, 'buyer_' + suffix
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
    restricted = create_async_engine(url.set(username=role, password='test-role-only'),
        connect_args={'server_settings': {'search_path': schema}})
    sessions = async_sessionmaker(restricted, expire_on_commit=False)
    async def dependency(request: Request):
        async with sessions() as session:
            tenant = getattr(request.state, 'tenant_id', None)
            if tenant:
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
                supplier = await client.post('/suppliers', headers=headers, json={'name': tenant, 'mutationId': 'supplier'})
                assert supplier.status_code == 201, supplier.text
                product = await client.post('/products', headers=headers, json={'sku': 'SAME', 'name': 'Item', 'price_amount': '5.00'})
                assert product.status_code == 201, product.text
                return headers, supplier.json()['id']
            a, supplier_a = await enroll('store-a')
            b, supplier_b = await enroll('store-b')
            created = await client.post('/purchase-orders', headers=a, json={'supplierId': supplier_a,
                'items': [{'sku': 'SAME', 'quantity': 3, 'unitCostCents': 100}], 'mutationId': 'order'})
            assert created.status_code == 201, created.text
            po_id = created.json()['id']
            for operation in ('approve', 'send'):
                response = await client.post('/purchase-orders/' + po_id + '/' + operation, headers=a,
                                             json={'mutationId': operation})
                assert response.status_code == 200, response.text
            assert (await client.get('/purchase-orders', headers=b)).json() == []
            endpoint = '/purchase-orders/' + po_id + '/receive'
            payload = {'receiptId': 'delivery-one', 'items': [{'sku': 'SAME', 'quantity': 1}]}
            results = await asyncio.gather(*(client.post(endpoint, headers=a, json=payload) for _ in range(2)))
            assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
            assert results[0].json() == results[1].json()
            assert (await client.get('/inventory', headers=a)).json()[0]['quantity'] == 1
            # Two different deliveries compete for the same outstanding two units.
            results = await asyncio.gather(*(client.post(endpoint, headers=a, json={
                'receiptId': 'final-' + str(index), 'items': [{'sku': 'SAME', 'quantity': 2}]}) for index in range(2)))
            assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
            assert (await client.get('/inventory', headers=a)).json()[0]['quantity'] == 3
            assert (await client.get('/accounting/valuation-report', headers=a)).json()[0]['totalValueCents'] == 300
            async with restricted.begin() as conn:
                assert await conn.scalar(text('SELECT count(*) FROM python_purchase_orders')) == 0
                await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                assert await conn.scalar(text('SELECT count(*) FROM python_purchase_orders')) == 1
                assert await conn.scalar(text('SELECT count(*) FROM python_stock_movements')) == 2
                assert await conn.scalar(text("SELECT count(*) FROM python_procurement_actions WHERE mutation_id LIKE 'receipt:%'")) == 2
            with pytest.raises(DBAPIError):
                async with restricted.begin() as conn:
                    await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                    await conn.execute(text("INSERT INTO python_purchase_orders "
                        "(id,tenant_id,supplier_id,supplier_name,location_id,status,version,items,created_by,created_at) "
                        "VALUES ('forged','store-a',:supplier,'Other','default','DRAFT',0,'[]','attacker',now())"), {'supplier': supplier_b})
            with pytest.raises(DBAPIError):
                async with restricted.begin() as conn:
                    await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                    await conn.execute(text("INSERT INTO python_supplier_asns (id,tenant_id,supplier_id,po_id,payload) "
                        "VALUES ('forged-asn','store-a',:supplier,:po,'{}')"), {'supplier': supplier_b, 'po': po_id})
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous
        await restricted.dispose()
        await owner.dispose()
