import pytest
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update, func
from src.infrastructure.report_alert_models import ReportJob, ReportSchedule, AlertCondition, AlertNotice
from src.infrastructure.owned_feature_models import NotificationModel, ReportDefinitionModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.cost_models import CostLayerModel


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()

def stock(client, sku='A', quantity=5, cost=125, **metadata):
    ok(client.post('/products', json={'sku': sku, 'name': sku, 'price_amount': '5.00'}), 201)
    return ok(client.post('/inventory/receive', json={'sku': sku, 'quantity': quantity, 'unitCostCents': cost, **metadata}))

def definition(client, kind='STOCK', **filters):
    return ok(client.post('/reports', json={'name': kind, 'type': kind, 'filters': filters,
        'mutationId': 'define-' + kind}))['report']

def execute(client, report, format='json', key='run'):
    return client.post('/reports/' + report['id'] + '/execute', json={'format': format, 'mutationId': key})

def generate(client, report, format='json', key='run'):
    queued = ok(execute(client, report, format, key), 202)
    job = ok(client.post('/reports/executions/' + queued['executionId'] + '/process'))
    assert job['status'] == 'COMPLETED', job
    return job, client.get(job['fileUrl'])

def policy(client):
    supplier = ok(client.post('/suppliers', json={'name': 'Supplier', 'mutationId': 'supplier'}), 201)
    return ok(client.post('/reorder-policies', json={'sku': 'A', 'supplierId': supplier['id'],
        'reorderPoint': 2, 'targetStock': 10, 'casePack': 3, 'mutationId': 'policy'}))

def enable(client, low=True, expiry=True):
    return ok(client.post('/alerts/config', json={'expectedVersion': 0, 'lowStock': low, 'expiry': expiry,
        'expiryDays': 7, 'mutationId': 'enable-alerts', 'reason': 'Retail alert monitoring'}))

async def test_job_replay_snapshot_persistence_and_authenticated_download(client, db_session):
    stock(client)
    report = definition(client)
    first = ok(execute(client, report), 202)
    assert ok(execute(client, report), 202) == first
    assert client.get('/reports/executions/' + first['executionId'] + '/content').status_code == 409
    completed = ok(client.post('/reports/executions/' + first['executionId'] + '/process'))
    assert completed['status'] == 'COMPLETED' and completed['attempts'] == 1
    assert ok(client.post('/reports/executions/' + first['executionId'] + '/process')) == completed
    result = ok(client.get(completed['fileUrl']))
    assert result['rows'][0]['quantity'] == 5
    ok(client.post('/inventory/dispatch', json={'sku': 'A', 'quantity': 2}))
    assert ok(client.get(completed['fileUrl'])) == result
    assert client.get(completed['fileUrl'], headers={'Authorization': ''}).status_code == 401
    assert await db_session.scalar(select(func.count()).select_from(ReportJob)) == 1
    assert await db_session.scalar(select(func.count()).select_from(NotificationModel)) == 1

@pytest.mark.parametrize('cost', [125, None])
async def test_valuation_exact_cents_and_unknown_cost_is_not_zero(client, cost):
    stock(client, cost=cost)
    job, response = generate(client, definition(client, 'INVENTORY_VALUATION'))
    data = ok(response)
    assert data['rows'][0]['valueCents'] == (625 if cost is not None else None)
    assert data['complete'] == (cost is not None)

async def test_remaining_layer_aging_ignores_reservations_and_sold_goods(client, db_session):
    stock(client)
    await db_session.execute(update(CostLayerModel).values(received_at=datetime.now(timezone.utc) - timedelta(days=110)))
    await db_session.commit()
    ok(client.post('/inventory/dispatch', json={'sku': 'A', 'quantity': 2}))
    ok(client.post('/inventory/allocate', json={'sku': 'A', 'quantity': 1}))
    result = ok(generate(client, definition(client, 'INVENTORY_AGING'))[1])
    assert result['rows'][0]['ageOver90'] == 3 and result['rows'][0]['age0To30'] == 0
    assert ok(client.get('/api/aging/report'))['rows'][0]['ageOver90'] == 3

async def test_csv_is_actual_data_and_no_pdf_or_email_fallback(client):
    stock(client)
    report = definition(client)
    job, response = generate(client, report, 'csv')
    assert response.headers['content-type'].startswith('text/csv')
    assert 'sku,locationId' in response.text and 'A,default,5' in response.text and 'MOCK-' not in response.text
    assert execute(client, report, 'pdf', 'unsupported').status_code == 422
    assert client.post('/reports/' + report['id'] + '/schedule', json={'cronExpression': '0 0 * * *', 'deliveryMethod': 'EMAIL'}).status_code == 422
    assert client.post('/reports', json={'name': 'Unsafe filter', 'filters': {'sql': 'DROP TABLE products'}}).status_code == 400

async def test_failed_job_visible_and_retry_uses_original_definition(client, db_session):
    report = definition(client, 'TRIAL_BALANCE')
    queued = ok(execute(client, report), 202)
    failed = ok(client.post('/reports/executions/' + queued['executionId'] + '/process'))
    assert failed['status'] == 'FAILED' and failed['error']
    assert await db_session.scalar(select(func.count()).select_from(NotificationModel)) == 0
    ok(client.post('/accounting/financial/activate', json={'expectedVersion': 0, 'mutationId': 'activate', 'reason': 'Open financial ledger'}))
    body = {'expectedVersion': failed['version'], 'mutationId': 'retry', 'reason': 'Accounting now active'}
    path = '/reports/executions/' + failed['id'] + '/retry'
    retried = ok(client.post(path, json=body))
    assert ok(client.post(path, json=body)) == retried
    assert ok(client.post('/reports/executions/' + failed['id'] + '/process'))['status'] == 'COMPLETED'

async def test_schedules_coalesce_missed_runs_and_do_not_queue_twice(client, db_session):
    report = definition(client)
    body = {'cronExpression': '0 9 * * *', 'format': 'json', 'mutationId': 'schedule'}
    path = '/reports/' + report['id'] + '/schedule'
    scheduled = ok(client.post(path, json=body))
    assert ok(client.post(path, json=body)) == scheduled
    await db_session.execute(update(ReportSchedule).values(next_run_at=datetime.now(timezone.utc) - timedelta(days=10)))
    await db_session.commit()
    assert ok(client.post('/reports/scheduler/tick'))['executionsQueued'] == 1
    assert ok(client.post('/reports/scheduler/tick'))['executionsQueued'] == 0
    assert await db_session.scalar(select(func.count()).select_from(ReportJob)) == 1
    assert client.post(path, json={'cronExpression': '*/5 * * * *'}).status_code == 400

async def test_low_stock_episode_dedup_resolution_recurrence_and_snooze(client, db_session):
    stock(client)
    policy(client)
    enable(client)
    assert ok(client.get('/notifications')) == []
    ok(client.post('/inventory/dispatch', json={'sku': 'A', 'quantity': 3, 'mutationId': 'sale'}))
    inbox = ok(client.get('/notifications'))
    assert len(inbox) == 1 and 'Low stock' in inbox[0]['message']
    for _ in range(2):
        assert ok(client.post('/alerts/evaluate'))['notificationsCreated'] == 0
    ok(client.post('/notifications/' + inbox[0]['id'] + '/snooze', json={'hours': 1}))
    assert ok(client.get('/api/notifications/inbox/unread'))['unread_count'] == 0
    await db_session.execute(update(NotificationModel).values(snoozed_until=datetime.now(timezone.utc) - timedelta(seconds=10)))
    await db_session.commit()
    assert ok(client.get('/api/notifications/inbox/unread'))['unread_count'] == 1
    ok(client.post('/inventory/receive', json={'sku': 'A', 'quantity': 4, 'unitCostCents': 125}))
    assert ok(client.get('/notifications')) == [] and ok(client.get('/alerts')) == []
    ok(client.post('/inventory/dispatch', json={'sku': 'A', 'quantity': 4}))
    assert len(ok(client.get('/notifications'))) == 1
    assert ok(client.get('/alerts'))[0]['episode'] == 2
    assert len(ok(client.get('/notifications?includeResolved=true'))) == 2

async def test_expiry_alerts_and_quarantine_do_not_show_usable_stock(client):
    stock(client, expirationDate=(datetime.now(timezone.utc) + timedelta(days=2)).isoformat(), lotNumber='near')
    policy(client)
    enable(client)
    assert any(r['kind'] == 'NEAR_EXPIRY' for r in ok(client.get('/alerts')))
    ok(client.post('/inventory/receive', json={'sku': 'A', 'quantity': 10, 'unitCostCents': 125, 'quarantined': True,
        'expirationDate': '2020-01-01T00:00:00Z', 'lotNumber': 'blocked'}))
    assert not any(r['kind'] == 'EXPIRED' for r in ok(client.get('/alerts')))
    row = ok(generate(client, definition(client, 'REPLENISHMENT'))[1])['rows'][0]
    assert row['unusableQuantity'] == 10 and row['availableQuantity'] == 5

async def test_report_ready_outbox_failure_rolls_back_completion_and_notification(client, db_session, monkeypatch):
    stock(client)
    queued = ok(execute(client, definition(client)), 202)
    import src.application.report_service as service
    original = service.notify
    def fail_after_notice(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('Simulated notification/outbox commit failure')
    monkeypatch.setattr(service, 'notify', fail_after_notice)
    with pytest.raises(RuntimeError):
        client.post('/reports/executions/' + queued['executionId'] + '/process')
    row = await db_session.scalar(select(ReportJob))
    assert row.status == 'QUEUED' and row.result is None and row.attempts == 0
    assert await db_session.scalar(select(func.count()).select_from(NotificationModel)) == 0
    monkeypatch.setattr(service, 'notify', original)
    assert ok(client.post('/reports/executions/' + row.id + '/process'))['status'] == 'COMPLETED'

async def test_financial_job_permission_is_rechecked_after_role_change(client):
    ok(client.post('/accounting/financial/activate', json={'expectedVersion': 0, 'mutationId': 'active', 'reason': 'Open books'}))
    employee = ok(client.post('/users', json={'email': 'accountant@example.com', 'role': 'ACCOUNTANT', 'password': 'accountant-password'}), 201)
    login = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': 'accountant@example.com', 'password': 'accountant-password'}))
    headers = {'Authorization': 'Bearer ' + login['token']}
    report = ok(client.post('/reports', headers=headers, json={'name': 'Finance', 'type': 'TRIAL_BALANCE'}))['report']
    body = {'format': 'json', 'mutationId': 'financial-run'}
    queued = ok(client.post('/reports/' + report['id'] + '/execute', headers=headers, json=body), 202)
    ok(client.patch('/users/' + employee['id'] + '/role', json={'role': 'VIEWER'}))
    new_login = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': 'accountant@example.com', 'password': 'accountant-password'}))
    viewer = {'Authorization': 'Bearer ' + new_login['token']}
    assert client.post('/reports/' + report['id'] + '/execute', headers=viewer, json=body).status_code == 403
    failed = ok(client.post('/reports/executions/' + queued['executionId'] + '/process'))
    assert failed['status'] == 'FAILED' and 'permission' in failed['error']
    assert client.get('/reports/executions/' + queued['executionId'] + '/content', headers=viewer).status_code == 403

async def test_report_base_units_and_warehouse_aging_do_not_expose_costs(client):
    ok(client.post('/products', json={'sku': 'A', 'name': 'Bulk item', 'price_amount': '5.00'}), 201)
    config = ok(client.post('/uom/configurations', json={'sku': 'A', 'baseUnit': 'g', 'category': 'weight',
        'rules': [], 'purchaseUnit': 'kg', 'expectedVersion': 0, 'mutationId': 'units'}))
    ok(client.post('/inventory/receive', json={'sku': 'A', 'quantity': '1.5', 'unit': 'kg', 'uomVersion': config['version'], 'unitCostCents': 1000, 'mutationId': 'bulk-receipt'}))
    row = ok(generate(client, definition(client))[1])['rows'][0]
    assert row['quantity'] == 1500 and row['baseUnit'] == 'g' and row['uomVersion'] == config['version']
    ok(client.post('/users', json={'email': 'warehouse@example.com', 'role': 'WAREHOUSE_OPERATOR', 'password': 'warehouse-password'}), 201)
    login = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': 'warehouse@example.com', 'password': 'warehouse-password'}))
    headers = {'Authorization': 'Bearer ' + login['token']}
    aged = ok(client.get('/api/aging/report', headers=headers))['rows'][0]
    assert aged['age0To30'] == 1500 and 'valueCents' not in aged
    assert client.get('/api/aging/A?location_id=default', headers=headers).status_code == 403
