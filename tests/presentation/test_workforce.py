import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from src.infrastructure.workforce_models import TaskPerformance

def ok(response, code=200):
    assert response.status_code == code, response.text
    return response.json()

def command(**data):
    return {'mutationId': str(uuid.uuid4()), 'reason': 'Reviewed workforce operation', **data}

def operator(client, name='worker'):
    user = ok(client.post('/users', json={'email': name+'@example.com', 'name': name,
        'role': 'WAREHOUSE_OPERATOR', 'password': 'worker-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'], 'password': 'worker-password'}))['token']
    profile = ok(client.put('/workforce/profiles/'+user['id'], json=command(expectedVersion=0,
        targetPicksPerHour=60, maxConsecutiveHours=8, certifications=['forklift'])))
    return user, profile, {'Authorization': 'Bearer '+token}

def shift(client, profile, **fields):
    now = datetime.now(timezone.utc)
    return client.post('/workforce/shifts', json=command(operatorId=profile['id'],
        startTime=(now-timedelta(minutes=1)).isoformat(), endTime=(now+timedelta(hours=2)).isoformat(),
        locationId='default', **fields))

def start(client, row, headers):
    return ok(client.post('/api/workforce/shifts/'+row['id']+'/start', headers=headers,
        json=command(expectedVersion=row['version'])))

async def test_profile_shift_activity_metrics_and_weighted_rates(client, db_session):
    user, profile, headers = operator(client)
    blank = ok(client.get('/workforce/me', headers=headers))['metrics']
    assert blank['picksPerReportedHour'] is None and blank['countAccuracyPercent'] is None
    row = start(client, ok(shift(client, profile)), headers)
    assert len(ok(client.get('/notifications', headers=headers))) == 1
    ok(client.post('/products', json={'sku': 'A', 'name': 'A', 'price_amount': '1.00'}), 201)
    ok(client.post('/inventory/receive', json={'sku': 'A', 'quantity': 5, 'unitCostCents': 100}))
    ids = []
    for index, seconds in enumerate((60, 120)):
        sale = ok(client.post('/inventory/dispatch', headers=headers, json={'sku': 'A', 'quantity': 1, 'mutationId': 'pick-'+str(index)}))
        sources = ok(client.get('/workforce/shifts/'+row['id']+'/sources', headers=headers))
        assert len(sources) == 1
        payload = command(kind='PICK', sourceId=sources[0]['id'], durationSeconds=seconds, traversalDistanceMeters=10)
        path = '/workforce/shifts/'+row['id']+'/activities'
        result = ok(client.post(path, headers=headers, json=payload))
        assert ok(client.post(path, headers=headers, json=payload)) == result
        assert client.post(path, headers=headers, json={**payload, 'mutationId': 'again-'+str(index)}).status_code == 409
        ids.append(result['id'])
    metrics = ok(client.get('/workforce/me', headers=headers))['metrics']
    assert metrics['recordedPickTasks'] == 2 and metrics['picksPerReportedHour'] == 40
    assert metrics['traversalDistanceMeters'] == 20 and metrics['countAccuracyPercent'] is None
    assert ok(client.get('/workforce/operators/'+profile['id']+'/metrics')) == metrics
    assert client.get('/workforce/operators', headers=headers).status_code == 403
    assert client.get('/workforce/operators/'+profile['id']+'/metrics', headers=headers).status_code == 403
    finished = ok(client.post('/workforce/shifts/'+row['id']+'/complete', headers=headers, json=command(expectedVersion=row['version'])))
    assert finished['status'] == 'COMPLETED' and finished['endedAt']
    assert client.post('/workforce/shifts/'+row['id']+'/complete', headers=headers, json=command(expectedVersion=row['version'])).status_code == 409
    with pytest.raises(IntegrityError):
        await db_session.execute(update(TaskPerformance).where(TaskPerformance.id == ids[0]).values(duration_seconds=1))
    await db_session.rollback()

async def test_shift_ownership_overlap_certifications_limits_and_reassignment(client):
    user, profile, headers = operator(client)
    _, other, other_headers = operator(client, 'other')
    assert shift(client, profile, requiredCertifications=['missing']).status_code == 409
    row = ok(shift(client, profile))
    assert shift(client, profile).status_code == 409
    next_start = datetime.fromisoformat(row['endTime'])
    assert client.post('/workforce/shifts', json=command(operatorId=profile['id'], startTime=next_start.isoformat(),
        endTime=(next_start+timedelta(hours=7)).isoformat(), locationId='default')).status_code == 409
    assert ok(client.get('/workforce/shifts', headers=other_headers)) == []
    assert client.get('/workforce/shifts/'+row['id']+'/activities', headers=other_headers).status_code == 404
    assert client.post('/workforce/shifts/'+row['id']+'/start', headers=other_headers, json=command(expectedVersion=0)).status_code == 404
    assert client.put('/workforce/profiles/'+user['id'], headers=headers, json=command(expectedVersion=1, targetPicksPerHour=20, maxConsecutiveHours=8)).status_code == 403
    assert client.put('/workforce/profiles/'+user['id'], json=command(expectedVersion=profile['version'], targetPicksPerHour=20, maxConsecutiveHours=1)).status_code == 409
    ok(client.patch('/users/'+user['id']+'/status', json={'isActive': False}))
    assert client.post('/workforce/shifts/'+row['id']+'/start', json=command(expectedVersion=0)).status_code == 409
    assert ok(client.post('/workforce/shifts/'+row['id']+'/cancel', json=command(expectedVersion=0)))['status'] == 'CANCELLED'

async def test_staffing_inputs_recommendation_and_publish_capacity_recheck(client):
    user, profile, _ = operator(client)
    payload = command(targetDate=datetime.now(timezone.utc).date().isoformat(), projectedInboundVolume=100,
        projectedOutboundVolume=500, averageOperatorTargetPicks=60, shiftDurationHours=8, requiredCertifications=['forklift'])
    draft = ok(client.post('/workforce/plans', json=payload))
    assert draft['recommendedHeadcount'] == 2 and draft['eligibleOperatorCount'] == 1 and draft['shortfall'] == 1
    assert ok(client.post('/workforce/plans', json=payload)) == draft
    ok(client.patch('/users/'+user['id']+'/status', json={'isActive': False}))
    published = ok(client.post('/workforce/plans/'+draft['id']+'/publish', json=command(expectedVersion=0)))
    assert published['status'] == 'PUBLISHED' and published['shortfall'] == 2
    assert client.post('/workforce/plans/'+draft['id']+'/publish', json=command(expectedVersion=0)).status_code == 409
    for field, bad in [('averageOperatorTargetPicks', 0), ('averageOperatorTargetPicks', True), ('shiftDurationHours', -1), ('projectedInboundVolume', -1), ('projectedOutboundVolume', True)]:
        assert client.post('/workforce/plans', json={**payload, field: bad, 'mutationId': 'invalid-'+field}).status_code == 422

async def test_assignment_notice_failure_rolls_back_shift_and_retry(client, monkeypatch):
    import src.presentation.workforce_router as service
    _, profile, headers = operator(client)
    original = service.notify
    def failure(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Simulated assignment notification commit failure')
    monkeypatch.setattr(service, 'notify', failure)
    now = datetime.now(timezone.utc)
    body = command(operatorId=profile['id'], startTime=now.isoformat(), endTime=(now+timedelta(hours=1)).isoformat(), locationId='default')
    with pytest.raises(RuntimeError):
        client.post('/workforce/shifts', json=body)
    # Closing the failed request rolls back both ORM shift and Core outbox notice.
    assert ok(client.get('/workforce/shifts', headers=headers)) == []
    assert ok(client.get('/notifications', headers=headers)) == []
    monkeypatch.setattr(service, 'notify', original)
    first = ok(client.post('/workforce/shifts', json=body))
    assert ok(client.post('/workforce/shifts', json=body)) == first
    assert len(ok(client.get('/notifications', headers=headers))) == 1

def test_consecutive_limits_ignore_unrelated_history():
    from src.presentation.workforce_router import consecutive_limit
    now = datetime.now(timezone.utc)
    history = (now-timedelta(days=2), now-timedelta(days=2)+timedelta(hours=8))
    proposed = (now, now+timedelta(hours=4))
    consecutive_limit([history, proposed], 4, proposed)
    adjacent = (proposed[1], proposed[1]+timedelta(hours=1))
    with pytest.raises(Exception, match='Consecutive'):
        consecutive_limit([proposed, adjacent], 4, adjacent)

async def test_count_accuracy_is_derived_from_reviewed_lines(client):
    from tests.presentation.test_count_reconciliation import seed, start as create_count, submit, review
    user, profile, headers = operator(client)
    seed(client)
    row = start(client, ok(shift(client, profile)), headers)
    record = create_count(client, assigned_operator_id=user['id'])
    ok(submit(client, record, {'COUNT': 8}))
    ok(review(client, record))
    result = ok(client.post('/workforce/shifts/'+row['id']+'/activities', headers=headers,
        json=command(kind='COUNT', sourceId=record['record_id'], durationSeconds=120)))
    assert result['accurateCountLines'] == 0 and result['totalCountLines'] == 1
    metrics = ok(client.get('/workforce/me', headers=headers))['metrics']
    assert metrics['countAccuracyPercent'] == 0 and metrics['traversalDistanceMeters'] is None

async def test_cross_tenant_and_actor_bound_retries(client):
    user, profile, headers = operator(client)
    payload = command(expectedVersion=profile['version'], targetPicksPerHour=50, maxConsecutiveHours=8)
    updated = ok(client.put('/workforce/profiles/'+user['id'], json=payload))
    assert ok(client.put('/workforce/profiles/'+user['id'], json=payload)) == updated
    assert client.put('/workforce/profiles/'+user['id'], json={**payload, 'targetPicksPerHour': 99}).status_code == 409
    ok(client.post('/auth/setup', json={'tenantId': 'other-store', 'orgName': 'Other', 'adminName': 'Other', 'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'}), 201)
    login = ok(client.post('/auth/login', json={'tenantId': 'other-store', 'email': 'admin@example.com', 'password': 'test-password'}))
    foreign = {'Authorization': 'Bearer '+login['token']}
    assert client.put('/workforce/profiles/'+user['id'], headers=foreign, json=payload).status_code == 404
    assert ok(client.get('/workforce/operators', headers=foreign)) == []

