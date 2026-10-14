import asyncio
import pytest
from sqlalchemy import text, select, func
from sqlalchemy.exc import DBAPIError
from tests.infrastructure.test_outbox_contract import outbox_database
from src.infrastructure.report_alert_models import ReportJob, AlertCondition
from src.infrastructure.owned_feature_models import NotificationModel
from src.application.workers.outbox_worker import set_worker_tenant

@pytest.mark.asyncio
async def test_report_workers_alert_replay_and_owned_database_parents(outbox_database):
    client, sessions, runtime, backend = outbox_database
    async def enroll(tenant):
        assert (await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant, 'adminName': 'Admin',
            'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})).status_code == 201
        login = (await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com', 'password': 'test-password'})).json()
        headers = {'Authorization': 'Bearer ' + login['token']}
        assert (await client.post('/products', headers=headers, json={'sku': 'A', 'name': 'A', 'price_amount': '5.00'})).status_code == 201
        assert (await client.post('/inventory/receive', headers=headers, json={'sku': 'A', 'quantity': 5, 'unitCostCents': 125})).status_code == 200
        return headers
    a, b = await enroll('store-a'), await enroll('store-b')
    definition = (await client.post('/reports', headers=a, json={'name': 'Stock', 'type': 'STOCK', 'mutationId': 'report'})).json()['report']
    path = '/reports/' + definition['id'] + '/execute'
    responses = await asyncio.gather(*(client.post(path, headers=a, json={'format': 'json', 'mutationId': 'once'}) for _ in range(2)))
    assert [r.status_code for r in responses] == [202, 202], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    job_id = responses[0].json()['executionId']
    results = await asyncio.gather(*(client.post('/reports/executions/' + job_id + '/process', headers=a) for _ in range(2)))
    assert [r.status_code for r in results] == [200, 200], [r.text for r in results]
    assert results[0].json() == results[1].json() and results[0].json()['attempts'] == 1
    url = results[0].json()['fileUrl']
    assert (await client.get(url, headers=a)).json()['rows'][0]['quantity'] == 5
    assert (await client.get(url, headers=b)).status_code == 404
    assert (await client.post(path, headers=b, json={'format': 'json'})).status_code == 404
    supplier = (await client.post('/suppliers', headers=a, json={'name': 'Supplier', 'mutationId': 'supplier'})).json()
    policy = await client.post('/reorder-policies', headers=a, json={'sku': 'A', 'supplierId': supplier['id'], 'reorderPoint': 2,
        'targetStock': 10, 'mutationId': 'policy'})
    assert policy.status_code == 200, policy.text
    config = await client.post('/alerts/config', headers=a, json={'lowStock': True, 'expiry': False, 'expiryDays': 7,
        'expectedVersion': 0, 'mutationId': 'enable', 'reason': 'Monitor stock'})
    assert config.status_code == 200, config.text
    responses = await asyncio.gather(*(client.post('/inventory/dispatch', headers=a,
        json={'sku': 'A', 'quantity': 2, 'mutationId': 'sale-' + str(index)}) for index in range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    inbox = (await client.get('/notifications', headers=a)).json()
    assert len(inbox) == 2  # One ready report and one shortage episode.
    assert (await client.post('/alerts/evaluate', headers=a)).json()['notificationsCreated'] == 0
    assert (await client.get('/notifications', headers=b)).json() == []
    assert (await client.post('/notifications/' + inbox[0]['id'] + '/read', headers=b)).status_code == 404
    manager = await client.post('/users', headers=a, json={'email': 'manager@example.com', 'role': 'MANAGER', 'name': 'Manager', 'password': 'manager-password'})
    assert manager.status_code == 201, manager.text
    target = manager.json()['id']
    source = next(row for row in inbox if row['message'].startswith('Low stock'))
    forwarded = await asyncio.gather(*(client.post('/notifications/' + source['id'] + '/escalate', headers=a,
        json={'target_manager_id': target}) for _ in range(2)))
    assert sorted(response.status_code for response in forwarded) == [200, 409], [r.text for r in forwarded]
    manager_login = (await client.post('/auth/login', json={'tenantId': 'store-a', 'email': 'manager@example.com', 'password': 'manager-password'})).json()
    mh = {'Authorization': 'Bearer ' + manager_login['token']}
    assert len((await client.get('/notifications', headers=mh)).json()) == 1
    await client.post('/inventory/receive', headers=a, json={'sku': 'A', 'quantity': 5, 'unitCostCents': 125})
    assert (await client.get('/notifications', headers=mh)).json() == []
    if backend == 'postgres':
        async with runtime.begin() as conn:
            assert (await conn.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user'))).one() == (False, False)
            for table in ('python_report_jobs','python_report_schedules','python_alert_config','python_alert_conditions','python_alert_notices'):
                assert await conn.scalar(text('SELECT count(*) FROM ' + table)) == 0
                assert await conn.scalar(text("SELECT relforcerowsecurity FROM pg_class WHERE oid=CAST(:name AS regclass)"), {'name': table})
        with pytest.raises(DBAPIError) as denied:
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id','store-a',true)"))
                await conn.execute(text("UPDATE python_report_jobs SET result='{}' WHERE id=:id"), {'id': job_id})
        assert denied.value.orig.sqlstate == '55000'
        actor_b = (await client.get('/auth/me', headers=b)).json()['id']
        with pytest.raises(DBAPIError) as denied:
            async with runtime.begin() as conn:
                await conn.execute(text("SELECT set_config('app.current_tenant_id','store-b',true)"))
                await conn.execute(text("INSERT INTO python_report_jobs (id,tenant_id,report_id,requested_by,request_key,definition,format,status,version,attempts,created_at) VALUES ('injected','store-b',:report,:actor,'foreign','{}','json','QUEUED',0,0,now())"),
                    {'report': definition['id'], 'actor': actor_b})
        assert denied.value.orig.sqlstate == '42501'
