import uuid
import pytest
from sqlalchemy import select, func
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.cycle_count.models import CycleCountRecordModel

pytestmark = pytest.mark.asyncio


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def seed(client, sku='COUNT', quantity=10, cost=100, **metadata):
    ok(client.post('/products', json={'sku': sku, 'name': sku, 'price_amount': '5.00'}), 201)
    return ok(client.post('/inventory/receive', json={'sku': sku, 'quantity': quantity,
               'unitCostCents': cost, **metadata}))


def start(client, **fields):
    return ok(client.post('/api/cycle-counts/start', json={'mutation_id': str(uuid.uuid4()), **fields}))


def submit(client, record, quantities, **fields):
    return client.post('/api/cycle-counts/submit', json={'record_id': record['record_id'],
        'revision': record['revision'], 'mutation_id': 'submit-' + str(uuid.uuid4()),
        'items': [{'sku': sku, 'counted_quantity': qty} for sku, qty in quantities.items()], **fields})


def review(client, record, action='approve', key=None):
    return client.post('/api/cycle-counts/' + record['record_id'] + '/' + action,
        json={'revision': record['revision'], 'reason': 'Verified shelf count',
              'mutation_id': key or str(uuid.uuid4())})


async def test_count_approval_reconciles_once_with_value_and_audit(client, db_session):
    seed(client)
    record = start(client)
    ok(submit(client, record, {'COUNT': 8}))
    assert client.get('/inventory').json()[0]['quantity'] == 10
    result = ok(review(client, record, key='approve-once'))
    assert result['adjustments'][0]['delta'] == -2
    assert result['adjustments'][0]['inventoryValueChangeCents'] == -200
    assert ok(review(client, record, key='approve-once')) == result
    assert ok(review(client, record)) == result
    assert client.get('/inventory').json()[0]['quantity'] == 8
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 800
    assert await db_session.scalar(select(func.count()).select_from(StockMovementModel).where(
        StockMovementModel.operation == 'adjust')) == 1
    assert await db_session.scalar(select(func.count()).select_from(OutboxEventModel).where(
        OutboxEventModel.event_type == 'StockAdjusted')) == 1


async def test_stale_count_requires_new_snapshot_and_rejects_old_offline_data(client):
    seed(client)
    record = start(client)
    ok(submit(client, record, {'COUNT': 8}))
    ok(client.post('/inventory/dispatch', json={'sku': 'COUNT', 'quantity': 1}))
    assert review(client, record).status_code == 409
    fresh = ok(review(client, record, 'recount'))
    assert fresh['revision'] == 1 and fresh['items'][0]['expected_quantity'] == 9
    assert submit(client, record, {'COUNT': 8}).status_code == 409
    ok(submit(client, fresh, {'COUNT': 8}))
    assert ok(review(client, fresh))['adjustments'][0]['delta'] == -1


async def test_approval_cannot_remove_reservations_and_rolls_back_every_line(client, db_session):
    seed(client, 'A')
    seed(client, 'Z')
    ok(client.post('/inventory/allocate', json={'sku': 'Z', 'quantity': 9}))
    record = start(client)
    ok(submit(client, record, {'A': 8, 'Z': 8}))
    assert review(client, record).status_code == 409
    assert [s['quantity'] for s in client.get('/inventory').json()] == [10, 10]
    assert (await db_session.get(CycleCountRecordModel, record['record_id'])).status == 'SUBMITTED'
    assert await db_session.scalar(select(func.count()).select_from(StockMovementModel).where(
        StockMovementModel.operation == 'adjust')) == 0
    assert sum(await db_session.scalars(select(CostLayerModel.remaining_quantity))) == 20


async def test_outbox_failure_rolls_back_quantity_costs_and_count(client, db_session, monkeypatch):
    seed(client)
    record = start(client)
    ok(submit(client, record, {'COUNT': 8}))
    original = db_session.flush
    async def fail(*args, **kwargs):
        if any(isinstance(row, OutboxEventModel) for row in db_session.new):
            raise RuntimeError('Injected outbox failure')
        return await original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(db_session, 'flush', fail)
        with pytest.raises(RuntimeError, match='Injected outbox failure'):
            review(client, record)
    assert client.get('/inventory').json()[0]['quantity'] == 10
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 1000
    assert (await db_session.get(CycleCountRecordModel, record['record_id'])).status == 'SUBMITTED'


@pytest.mark.parametrize('quantity', [-1, True, 1.5])
async def test_invalid_observations_rejected(client, quantity):
    seed(client)
    record = start(client)
    assert submit(client, record, {'COUNT': quantity}).status_code == 422


async def test_complete_unique_submission_and_offline_atomic_replay(client):
    seed(client, 'A')
    seed(client, 'B')
    record = start(client)
    assert submit(client, record, {'A': 8}).status_code == 400
    payload = {'record_id': record['record_id'], 'revision': 0, 'mutation_id': 'offline',
               'items': [{'sku': 'A', 'counted_quantity': 8}, {'sku': 'B', 'counted_quantity': 9}]}
    duplicate = {**payload, 'items': [payload['items'][0]] * 2}
    assert client.post('/api/cycle-counts/submit', json=duplicate).status_code == 400
    missing = {**payload, 'record_id': 'missing', 'mutation_id': 'missing'}
    assert client.post('/api/cycle-counts/sync', json=[payload, missing]).status_code == 404
    assert client.get('/api/cycle-counts').json()[0]['status'] == 'PENDING'
    first = ok(client.post('/api/cycle-counts/sync', json=[payload]))
    assert ok(client.post('/api/cycle-counts/sync', json=[payload])) == first
    assert ok(client.post('/api/cycle-counts/submit', json=payload)) == first['results'][0]
    payload['items'][0]['counted_quantity'] = 7
    assert client.post('/api/cycle-counts/sync', json=[payload]).status_code == 409


@pytest.mark.parametrize('method', ['FIFO', 'LIFO', 'WAC', 'FEFO'])
async def test_adjustments_consume_costs_including_expired_stock(client, method):
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': method}))
    stock = seed(client, expirationDate='2020-01-01T00:00:00Z', quarantined=True)
    data = {'sku': 'COUNT', 'delta': -2, 'expected_version': stock['version'], 'reason_code': 'EXPIRED',
            'reason': 'Expired disposal', 'mutation_id': 'dispose'}
    result = ok(client.post('/api/cycle-counts/adjust', json=data))
    assert result['inventoryValueChangeCents'] == -200
    assert ok(client.post('/api/cycle-counts/adjust', json=data)) == result
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 800
    data['delta'] = -3
    assert client.post('/api/cycle-counts/adjust', json=data).status_code == 409


async def test_count_gain_never_invents_cost_and_direct_gain_can_be_priced(client):
    seed(client)
    record = start(client)
    ok(submit(client, record, {'COUNT': 12}))
    assert ok(review(client, record))['adjustments'][0]['inventoryValueChangeCents'] is None
    assert client.get('/accounting/valuation-report').status_code == 409
    stock = client.get('/inventory').json()[0]
    result = ok(client.post('/api/cycle-counts/adjust', json={'sku': 'COUNT', 'delta': 1,
        'expected_version': stock['version'], 'reason_code': 'FOUND', 'reason': 'Found carton',
        'mutation_id': 'gain', 'unit_cost_cents': 150}))
    assert result['inventoryValueChangeCents'] == 150


async def test_operator_blind_counts_cannot_approve_or_adjust_and_foreign_ids_hidden(client):
    seed(client)
    operator = ok(client.post('/users', json={'email': 'counter@example.com', 'role': 'WAREHOUSE_OPERATOR',
                                            'password': 'test-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': operator['email'],
                                              'password': 'test-password'}))['token']
    headers = {'Authorization': 'Bearer ' + token}
    record = start(client, assigned_operator_id=operator['id'])
    assert client.get('/api/cycle-counts/assigned', headers=headers).json()[0]['items'][0]['expected_quantity'] is None
    assert client.post('/api/cycle-counts/' + record['record_id'] + '/approve', headers=headers,
        json={'revision': 0, 'reason': 'Checked', 'mutation_id': 'bad'}).status_code == 403
    assert client.post('/api/cycle-counts/adjust', headers=headers, json={}).status_code == 403
    assert client.post('/api/cycle-counts/start', headers=headers, json={}).status_code == 403


async def test_oversized_location_cannot_create_an_unsubmittable_count(client, db_session):
    db_session.add_all([StockModel(tenant_id='tenant-1', sku=f'SKU-{index}', location_id='default',
                                  quantity=0, allocated=0, version=0) for index in range(1001)])
    await db_session.commit()
    response = client.post('/api/cycle-counts/start', json={'mutation_id': 'large-location'})
    assert response.status_code == 400 and '1000 SKUs' in response.json()['detail']
    assert await db_session.scalar(select(func.count()).select_from(CycleCountRecordModel)) == 0
