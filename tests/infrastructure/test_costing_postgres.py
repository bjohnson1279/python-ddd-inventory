"""Opt-in test against a disposable PostgreSQL database, in a fresh schema."""
import asyncio
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.presentation.main import app
from src.infrastructure.database import get_db_session
from scripts import migrate_identity_catalog as migration


@pytest.mark.asyncio
async def test_postgres_legacy_migration_costing_and_concurrent_sales(monkeypatch):
    database = os.getenv('COSTING_TEST_DATABASE_URL')
    if not database:
        pytest.skip('Set COSTING_TEST_DATABASE_URL to a disposable local PostgreSQL costing_test database')
    url = make_url(database)
    if url.host not in ('127.0.0.1', 'localhost') or url.database != 'costing_test':
        raise ValueError('PostgreSQL costing tests require a disposable local costing_test database')
    schema = 'costing_' + uuid.uuid4().hex
    bootstrap = create_async_engine(url)
    async with bootstrap.begin() as conn:
        await conn.execute(text('CREATE SCHEMA ' + schema))
    await bootstrap.dispose()
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(migration, 'engine', engine)

    async def session_dependency():
        async with sessions() as session:
            yield session

    previous = app.dependency_overrides.get(get_db_session)
    app.dependency_overrides[get_db_session] = session_dependency
    try:
        await migration.migrate(identity_only=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            async def enroll(tenant):
                response = await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant,
                    'adminName': 'Admin', 'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
                assert response.status_code == 201, response.text
                login = await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com',
                                                             'password': 'test-password'})
                assert login.status_code == 200
                return {'Authorization': 'Bearer ' + login.json()['token']}

            owner = await enroll('store-a')
            async with engine.begin() as conn:
                await conn.execute(text('CREATE TABLE products (id VARCHAR PRIMARY KEY, sku VARCHAR UNIQUE NOT NULL, '
                    'name VARCHAR NOT NULL, description VARCHAR, price_amount NUMERIC(10,2) NOT NULL, '
                    'created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())'))
                await conn.execute(text("INSERT INTO products (id,sku,name,price_amount) VALUES ('legacy','COST-SKU','Legacy',99)"))
            await migration.migrate(legacy_tenant='store-a')
            await migration.migrate(legacy_tenant='store-a')
            assert (await client.get('/products', headers=owner)).json()[0]['sku'] == 'COST-SKU'

            async def change(operation, sku, amount, key, headers=owner, **metadata):
                return await client.post('/inventory/' + operation, headers=headers, json={
                    'sku': sku, 'locationId': 'store', 'quantity': amount, 'mutationId': key, **metadata})

            assert (await change('receive', 'COST-SKU', 10, 'receipt-a', unitCostCents=100,
                                receivedAt='2024-01-01T00:00:00Z')).status_code == 200
            assert (await change('receive', 'COST-SKU', 10, 'receipt-b', unitCostCents=200,
                                receivedAt='2024-01-02T00:00:00Z')).status_code == 200
            for method, expected in [('FIFO', 1400), ('LIFO', 2200), ('WAC', 1800)]:
                response = await client.get('/accounting/valuation/legacy', headers=owner,
                    params={'locationId': 'store', 'quantity': 12, 'method': method})
                assert response.status_code == 200, response.text
                assert response.json()['totalCostCents'] == expected
            assert (await change('dispatch', 'COST-SKU', 12, 'sale')).json()['costOfGoodsSoldCents'] == 1400
            assert (await client.get('/accounting/valuation-report', headers=owner)).json()[0]['totalValueCents'] == 1600

            other = await enroll('store-b')
            assert (await client.post('/products', headers=other, json={
                'sku': 'COST-SKU', 'name': 'Other Store', 'price_amount': '99.00'})).status_code == 201
            assert (await client.get('/accounting/cost-layers', headers=other)).json() == []

            assert (await client.post('/products', headers=owner, json={
                'sku': 'LAST-SKU', 'name': 'Last Unit', 'price_amount': '99.00'})).status_code == 201
            assert (await change('receive', 'LAST-SKU', 1, 'last-receipt', unitCostCents=50)).status_code == 200
            buyers = await asyncio.gather(change('dispatch', 'LAST-SKU', 1, 'buyer-a'),
                                          change('dispatch', 'LAST-SKU', 1, 'buyer-b'))
            assert sorted(response.status_code for response in buyers) == [200, 400]
            assert next(response for response in buyers if response.status_code == 200).json()['costOfGoodsSoldCents'] == 50
            replayed = await asyncio.gather(change('receive', 'LAST-SKU', 2, 'duplicate-receipt', unitCostCents=50),
                                           change('receive', 'LAST-SKU', 2, 'duplicate-receipt', unitCostCents=50))
            assert all(response.status_code == 200 for response in replayed)
            assert replayed[0].json() == replayed[1].json()
            stocks = (await client.get('/inventory', headers=owner)).json()
            assert next(stock for stock in stocks if stock['sku'] == 'LAST-SKU')['quantity'] == 2

            average_owner = await enroll('store-wac')
            assert (await client.post('/accounting/tenant-config', headers=average_owner, json={
                'tenantId': 'store-wac', 'costingMethod': 'WAC'})).status_code == 200
            await client.post('/products', headers=average_owner, json={'sku': 'WAC-SKU', 'name': 'Average', 'price_amount': '99.00'})
            assert (await change('receive', 'WAC-SKU', 1, 'wac-a', headers=average_owner, unitCostCents=1)).status_code == 200
            assert (await change('receive', 'WAC-SKU', 2, 'wac-b', headers=average_owner, unitCostCents=2)).status_code == 200
            costs = [(await change('dispatch', 'WAC-SKU', 1, 'wac-sale-' + str(i), headers=average_owner))
                     .json()['costOfGoodsSoldCents'] for i in range(3)]
            assert costs == [2, 2, 1]
            assert (await client.get('/accounting/valuation-report', headers=average_owner)).json()[0]['totalValueCents'] == 0
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous
        await engine.dispose()
