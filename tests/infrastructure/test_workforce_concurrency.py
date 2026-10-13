import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import text, select, func
from sqlalchemy.exc import DBAPIError
from tests.infrastructure.test_outbox_contract import outbox_database
from src.infrastructure.workforce_models import TaskPerformance
from src.application.workers.outbox_worker import set_worker_tenant

@pytest.mark.asyncio
async def test_workforce_concurrent_assignment_replay_and_database_ownership(outbox_database):
    client, sessions, runtime, backend = outbox_database
    async def enroll(tenant):
        response = await client.post('/auth/setup', json={'tenantId': tenant, 'orgName': tenant, 'adminName': 'Admin',
            'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
        assert response.status_code == 201, response.text
        response = await client.post('/auth/login', json={'tenantId': tenant, 'email': 'admin@example.com', 'password': 'test-password'})
        headers = {'Authorization': 'Bearer '+response.json()['token']}
        response = await client.post('/users', headers=headers, json={'email': 'worker@example.com', 'name': 'Worker', 'role': 'WAREHOUSE_OPERATOR', 'password': 'worker-password'})
        assert response.status_code == 201, response.text
        user = response.json()['id']
        response = await client.put('/workforce/profiles/'+user, headers=headers,
            json={'targetPicksPerHour': 60, 'maxConsecutiveHours': 8, 'expectedVersion': 0, 'mutationId': 'profile', 'reason': 'Set target'})
        assert response.status_code == 200, response.text
        profile = response.json()
        response = await client.post('/auth/login', json={'tenantId': tenant, 'email': 'worker@example.com', 'password': 'worker-password'})
        worker = {'Authorization': 'Bearer '+response.json()['token']}
        return headers, worker, user, profile
    a, worker, user, profile = await enroll('store-a')
    b, _, other_user, foreign = await enroll('store-b')
    assert (await client.put('/workforce/profiles/'+other_user, headers=a, json={'expectedVersion': 1, 'targetPicksPerHour': 60, 'maxConsecutiveHours': 8, 'mutationId': 'cross', 'reason': 'Review'})).status_code == 404
    now = datetime.now(timezone.utc)
    payload = {'operatorId': profile['id'], 'startTime': (now-timedelta(minutes=1)).isoformat(),
        'endTime': (now+timedelta(hours=2)).isoformat(), 'locationId': 'default', 'reason': 'Assign operator'}
    responses = await asyncio.gather(*(client.post('/workforce/shifts', headers=a, json={**payload, 'mutationId': 'overlap-'+str(i)}) for i in range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409], [r.text for r in responses]
    row = next(r.json() for r in responses if r.status_code == 200)
    path = '/workforce/shifts/'+row['id']+'/start'
    body = {'expectedVersion': 0, 'mutationId': 'start', 'reason': 'Clock in'}
    responses = await asyncio.gather(*(client.post(path, headers=worker, json=body) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    assert (await client.post(path, headers=a, json=body)).status_code == 409
    assert (await client.get('/workforce/shifts/'+row['id']+'/sources', headers=b)).status_code == 404
    assert (await client.get('/workforce/shifts', headers=b)).json() == []
    assert (await client.post('/products', headers=a, json={'sku': 'A', 'name': 'A', 'price_amount': '1.00'})).status_code == 201
    assert (await client.post('/inventory/receive', headers=a, json={'sku': 'A', 'quantity': 2, 'unitCostCents': 100})).status_code == 200
    assert (await client.post('/inventory/dispatch', headers=worker, json={'sku': 'A', 'quantity': 1})).status_code == 200
    sources = (await client.get('/workforce/shifts/'+row['id']+'/sources', headers=worker)).json()
    assert len(sources) == 1
    task = {'kind': 'PICK', 'sourceId': sources[0]['id'], 'durationSeconds': 60, 'mutationId': 'activity', 'reason': 'Completed task'}
    path = '/workforce/shifts/'+row['id']+'/activities'
    responses = await asyncio.gather(*(client.post(path, headers=worker, json=task) for _ in range(2)))
    assert [r.status_code for r in responses] == [200, 200], [r.text for r in responses]
    assert responses[0].json() == responses[1].json()
    activity = responses[0].json()['id']
    async with sessions() as session:
        await set_worker_tenant(session, 'store-a')
        assert await session.scalar(select(func.count()).select_from(TaskPerformance)) == 1
    if backend == 'postgres':
        async with runtime.connect() as conn:
            role = (await conn.execute(text('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user'))).one()
            assert role == (False, False)
            for table in ('python_operator_profiles', 'python_labor_shifts', 'python_task_performance', 'python_staffing_plans'):
                flags = (await conn.execute(text('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=cast(:table AS regclass)'), {'table': table})).one()
                assert flags == (True, True)
                assert await conn.scalar(text('SELECT count(*) FROM '+table)) == 0
        async with runtime.begin() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant_id','store-a',true)"))
            with pytest.raises(DBAPIError) as failure:
                await conn.execute(text("UPDATE python_task_performance SET duration_seconds=1 WHERE id=:id"), {'id': activity})
            assert failure.value.orig.sqlstate == '55000'
            await conn.rollback()
        async with runtime.begin() as conn:
            await conn.execute(text("SELECT set_config('app.current_tenant_id','store-a',true)"))
            with pytest.raises(DBAPIError) as failure:
                await conn.execute(text("INSERT INTO python_labor_shifts(id,tenant_id,operator_id,planned_start,planned_end,location_id,required_certifications,status,version) VALUES ('bad','store-a',:foreign,now(),now()+interval '1 hour','default','[]','PLANNED',0)"), {'foreign': foreign['id']})
            assert failure.value.orig.sqlstate == '42501'
            await conn.rollback()
