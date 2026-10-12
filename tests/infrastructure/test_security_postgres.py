"""Exercise forced RLS with a non-owner, non-superuser database login."""
import os
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.presentation.main import app
from src.infrastructure.database import get_db_session
from scripts import migrate_identity_catalog as migration


@pytest.mark.asyncio
async def test_restricted_postgres_role_enforces_tenant_scope_and_auth_revocation(monkeypatch):
    database = os.getenv('COSTING_TEST_DATABASE_URL')
    if not database:
        pytest.skip('Set COSTING_TEST_DATABASE_URL to the disposable local costing_test database')
    url = make_url(database)
    if url.host not in ('127.0.0.1', 'localhost') or url.database != 'costing_test':
        raise ValueError('Security tests require a disposable local costing_test database')
    suffix = uuid.uuid4().hex
    schema, role = 'security_' + suffix, 'retail_' + suffix
    bootstrap = create_async_engine(url)
    async with bootstrap.begin() as conn:
        await conn.execute(text('CREATE SCHEMA ' + schema))
        await conn.execute(text("CREATE ROLE " + role + " LOGIN PASSWORD 'test-role-only' NOSUPERUSER NOBYPASSRLS"))
    await bootstrap.dispose()
    owner_engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    monkeypatch.setattr(migration, 'engine', owner_engine)
    await migration.migrate()
    await migration.migrate()  # Existing-table upgrades and policy installation are repeatable.
    async with owner_engine.begin() as conn:
        await conn.execute(text('GRANT USAGE ON SCHEMA ' + schema + ' TO ' + role))
        await conn.execute(text('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ' + schema + ' TO ' + role))
    restricted = create_async_engine(url.set(username=role, password='test-role-only'),
        connect_args={'server_settings': {'search_path': schema}})
    sessions = async_sessionmaker(restricted, expire_on_commit=False)

    async def session_dependency(request: Request):
        async with sessions() as session:
            tenant = getattr(request.state, 'tenant_id', None)
            if tenant:
                await session.execute(text("SELECT set_config('app.current_tenant_id', :tenant, true)"), {'tenant': tenant})
            yield session

    previous = app.dependency_overrides.get(get_db_session)
    app.dependency_overrides[get_db_session] = session_dependency
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            async def enroll(tenant):
                setup = await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant,
                    'adminName': 'Admin', 'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
                assert setup.status_code == 201, setup.text
                login = await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com',
                                                             'password': 'test-password'})
                assert login.status_code == 200, login.text
                return setup.json()['userId'], {'Authorization': 'Bearer ' + login.json()['token']}

            admin_a, a = await enroll('store-a')
            admin_b, b = await enroll('store-b')
            for headers in (a, b):
                created = await client.post('/products', headers=headers, json={'sku': 'SAME-SKU', 'name': 'Item', 'price_amount': '99.00'})
                assert created.status_code == 201, created.text
                received = await client.post('/inventory/receive', headers=headers, json={'sku': 'SAME-SKU',
                    'locationId': 'store', 'quantity': 2, 'unitCostCents': 100})
                assert received.status_code == 200, received.text
            report = await client.post('/reports/', headers=a, json={'name': 'Private'})
            assert report.status_code == 200, report.text
            assert (await client.get('/reports/', headers=b)).json()['reports'] == []
            assert (await client.post('/reports/' + report.json()['report']['id'] + '/execute', headers=b)).status_code == 404
            assert (await client.get('/accounting/valuation-report', headers=a)).json()[0]['totalValueCents'] == 200

            # No application filters here: the database itself must restrict rows.
            async with restricted.begin() as conn:
                assert await conn.scalar(text('SELECT count(*) FROM products')) == 0
                assert await conn.scalar(text('SELECT count(*) FROM outbox_events')) == 0
                await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                assert await conn.scalar(text('SELECT count(*) FROM products')) == 1
                assert await conn.scalar(text("SELECT count(*) FROM products WHERE tenant_id = 'store-b'")) == 0
                assert await conn.scalar(text('SELECT count(*) FROM python_report_definitions')) == 1
                assert await conn.scalar(text('SELECT count(*) FROM outbox_events')) == 1
            async with restricted.begin() as conn:
                assert await conn.scalar(text('SELECT count(*) FROM products')) == 0  # No pooled-connection scope leak.
            with pytest.raises(DBAPIError):
                async with restricted.begin() as conn:
                    await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                    await conn.execute(text("INSERT INTO python_report_definitions (id,tenant_id,created_by,payload) "
                        "VALUES ('forged','store-b','attacker','{}')"))
            with pytest.raises(DBAPIError):
                async with restricted.begin() as conn:
                    await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                    await conn.execute(text("INSERT INTO python_notifications (id,tenant_id,user_id,message,status,created_at) "
                        "VALUES ('bad-recipient','store-a',:user,'private','UNREAD',now())"), {'user': admin_b})
            assert (await client.patch('/users/' + admin_b + '/role', headers=a, json={'role': 'USER'})).status_code == 404
            employee = await client.post('/users', headers=a, json={'email': 'reader@example.com',
                'role': 'VIEWER', 'password': 'test-password'})
            assert employee.status_code == 201, employee.text
            employee_id = employee.json()['id']
            login = await client.post('/auth/login', json={'tenantId': 'store-a',
                'email': 'reader@example.com', 'password': 'test-password'})
            reader = {'Authorization': 'Bearer ' + login.json()['token']}
            assert (await client.get('/products', headers=reader)).status_code == 200
            assert (await client.post('/products', headers=reader, json={
                'sku': 'FORBIDDEN', 'name': 'Forbidden', 'price_amount': '1.00'})).status_code == 403
            assert (await client.patch('/users/' + employee_id + '/role', headers=a,
                json={'role': 'ACCOUNTANT'})).status_code == 200
            assert (await client.get('/products', headers=reader)).status_code == 401
            assert (await client.patch('/users/' + employee_id + '/status', headers=a,
                json={'isActive': False})).status_code == 200
            assert (await client.post('/auth/login', json={'tenantId': 'store-a',
                'email': 'reader@example.com', 'password': 'test-password'})).status_code == 401
            async with restricted.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id', 'store-a', true)"))
                assert await conn.scalar(text('SELECT count(*) FROM python_security_audit')) == 3
            assert (await client.post('/auth/logout', headers=a)).status_code == 200
            assert (await client.get('/products', headers=a)).status_code == 401
            assert (await client.get('/products', headers=b)).status_code == 200
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_db_session, None)
        else:
            app.dependency_overrides[get_db_session] = previous
        await restricted.dispose()
        await owner_engine.dispose()
