import uuid
import pytest
from sqlalchemy import select, func
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.return_models import ReturnModel, ReturnActionModel
from src.infrastructure.messaging.models import OutboxEventModel

pytestmark = pytest.mark.asyncio


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def sale(client, quantity=2, cost=100, sale_quantity=2, **metadata):
    ok(client.post('/products', json={'sku': 'RET', 'name': 'Returned item', 'price_amount': '99.00'}), 201)
    ok(client.post('/inventory/receive', json={'sku': 'RET', 'quantity': quantity, 'unitCostCents': cost, **metadata}))
    sold = ok(client.post('/inventory/dispatch', json={'sku': 'RET', 'quantity': sale_quantity, 'mutationId': 'sale'}))
    movement = next(m for m in client.get('/inventory/movements').json() if m['mutationId'] == 'sale')
    return movement['id'], sold['consumedLayers']


def create(client, source, layers, disposition='QUARANTINE', **fields):
    return ok(client.post('/returns/rmas', json={'sourceMovementId': source, 'reason': 'Customer return',
        'items': [{'layerId': layer['layerId'], 'quantity': layer['quantity'], 'disposition': disposition} for layer in layers],
        'mutationId': str(uuid.uuid4()), **fields}), 201)


def command(client, row, name, **fields):
    return client.post('/returns/rmas/' + row['id'] + '/' + name, json={
        'expectedVersion': row['version'], 'reason': 'Verified', 'mutationId': str(uuid.uuid4()), **fields})


def receive(client, row, items=None, receipt=None):
    return client.post('/returns/rmas/' + row['id'] + '/receive', json={
        'expectedVersion': row['version'], 'receiptId': receipt or str(uuid.uuid4()),
        'items': items or [{'layerId': i['layerId'], 'quantity': i['quantity'] - i.get('receivedQuantity', i.get('shippedQuantity', 0))} for i in row['items']]})


@pytest.mark.parametrize('method', ['FIFO', 'LIFO', 'WAC', 'FEFO'])
async def test_partial_returns_restore_original_cogs_and_every_penny(client, method):
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': method}))
    ok(client.post('/products', json={'sku': 'RET', 'name': 'Item', 'price_amount': '99.00'}), 201)
    for cost in (1, 2):
        ok(client.post('/inventory/receive', json={'sku': 'RET', 'quantity': 2, 'unitCostCents': cost,
            'lotNumber': 'LOT-' + str(cost), 'expirationDate': '2030-01-01T00:00:00Z'}))
    sold = ok(client.post('/inventory/dispatch', json={'sku': 'RET', 'quantity': 3, 'mutationId': 'sale'}))
    source = next(m['id'] for m in client.get('/inventory/movements').json() if m['mutationId'] == 'sale')
    row = create(client, source, sold['consumedLayers'], 'RESTOCK')
    row = ok(command(client, row, 'authorize'))
    reversed_cost = 0
    for entry in sold['consumedLayers']:
        for _ in range(entry['quantity']):
            row = ok(receive(client, row, [{'layerId': entry['layerId'], 'quantity': 1}]))
            reversed_cost += row['stockResults'][0]['cogsReversedCents']
    assert reversed_cost == sold['costOfGoodsSoldCents']
    assert row['status'] == 'RECEIVED'
    assert client.get('/inventory').json()[0]['quantity'] == 4
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 6
    assert command(client, row, 'cancel').status_code == 409


async def test_authorization_caps_returns_cancel_releases_only_unreceived_balance(client):
    source, layers = sale(client)
    first = create(client, source, layers, 'RESTOCK')
    second = create(client, source, [{**layers[0], 'quantity': 1}], 'RESTOCK')
    assert receive(client, first).status_code == 409
    first = ok(command(client, first, 'authorize'))
    assert command(client, second, 'authorize').status_code == 409
    first = ok(receive(client, first, [{'layerId': layers[0]['layerId'], 'quantity': 1}]))
    first = ok(command(client, first, 'cancel'))
    second = ok(command(client, second, 'authorize'))
    ok(receive(client, second))
    assert client.get('/inventory').json()[0]['quantity'] == 2
    assert command(client, create(client, source, [{**layers[0], 'quantity': 1}]), 'authorize').status_code == 409


async def test_receipt_replay_and_changed_payload_never_reverse_twice(client, db_session):
    source, layers = sale(client)
    row = ok(command(client, create(client, source, layers, 'RESTOCK'), 'authorize'))
    first = ok(receive(client, row, receipt='once'))
    assert ok(receive(client, row, receipt='once')) == first
    assert receive(client, row, [{'layerId': layers[0]['layerId'], 'quantity': 1}], 'once').status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(ReturnActionModel).where(
        ReturnActionModel.mutation_id.like('receipt:%'))) == 1
    assert client.get('/inventory').json()[0]['quantity'] == 2


async def test_quarantine_blocks_sale_then_restocks_without_second_cogs_reversal(client):
    source, layers = sale(client)
    row = ok(command(client, create(client, source, layers), 'authorize'))
    received = ok(receive(client, row))
    assert received['stockResults'][0]['cogsReversedCents'] == 200
    assert client.post('/inventory/dispatch', json={'sku': 'RET', 'quantity': 1}).status_code == 400
    quarantine = client.get('/returns/quarantine').json()[0]
    body = {'resolution': 'restock', 'expectedVersion': quarantine['version'], 'reason': 'Inspection passed', 'mutationId': 'restock'}
    resolved = ok(client.post('/returns/quarantine/' + quarantine['id'] + '/resolve', json=body))
    assert resolved['status'] == 'RESTOCKED'
    assert ok(client.post('/returns/quarantine/' + quarantine['id'] + '/resolve', json=body)) == resolved
    assert client.get('/returns/quarantine').json() == []
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 200
    assert ok(client.post('/inventory/dispatch', json={'sku': 'RET', 'quantity': 1}))['costOfGoodsSoldCents'] == 100


async def test_quarantine_partial_writeoff_and_count_adjustment_update_pending_quantity(client):
    source, layers = sale(client, quantity=3, sale_quantity=3)
    row = ok(command(client, create(client, source, layers), 'authorize'))
    received = ok(receive(client, row))
    layer_id = received['stockResults'][0]['restoredLayerIds'][0]
    stock = client.get('/inventory').json()[0]
    ok(client.post('/returns/write-offs', json={'sku': 'RET', 'quantity': 1, 'layerId': layer_id,
        'expectedVersion': stock['version'], 'reasonCode': 'DAMAGE', 'reason': 'Damaged return', 'mutationId': 'loss-1'}))
    pending = client.get('/returns/quarantine').json()[0]
    assert pending['quantity'] == 2 and pending['version'] == 1
    stock = client.get('/inventory').json()[0]
    ok(client.post('/api/cycle-counts/adjust', json={'sku': 'RET', 'delta': -1, 'expected_version': stock['version'],
        'reason_code': 'DAMAGE', 'reason': 'Another damaged unit', 'mutation_id': 'loss-2'}))
    pending = client.get('/returns/quarantine').json()[0]
    assert pending['quantity'] == 1 and pending['version'] == 2
    ok(client.post('/returns/quarantine/' + pending['id'] + '/resolve', json={'resolution': 'SCRAP',
        'expectedVersion': 2, 'reason': 'Failed inspection', 'mutationId': 'scrap-last'}))
    assert client.get('/inventory').json()[0]['quantity'] == 0
    assert client.get('/returns/quarantine').json() == []


async def test_scrapped_customer_return_reverses_cogs_and_recognizes_loss_without_stock_gain(client):
    source, layers = sale(client)
    row = ok(command(client, create(client, source, layers, 'SCRAP'), 'authorize'))
    result = ok(receive(client, row))['stockResults'][0]
    assert result['cogsReversedCents'] == result['writeOffCents'] == 200
    assert result['inventoryValueChangeCents'] == result['quantity'] == 0
    assert client.get('/returns/quarantine').json() == []


async def test_unknown_cogs_are_not_invented_from_later_opening_pricing(client):
    source, layers = sale(client, cost=None, quantity=3, sale_quantity=2)
    original_layer = client.get('/accounting/cost-layers').json()[0]
    ok(client.post('/accounting/cost-layers/' + original_layer['id'] + '/price', json={'unitCostCents': 500, 'mutationId': 'later-price'}))
    row = ok(command(client, create(client, source, layers, 'RESTOCK'), 'authorize'))
    result = ok(receive(client, row))['stockResults'][0]
    assert result['cogsReversedCents'] is None and result['inventoryValueChangeCents'] is None
    assert client.get('/accounting/valuation-report').status_code == 409


async def test_returns_failure_rolls_back_stock_cost_rma_and_outbox(client, db_session, monkeypatch):
    source, layers = sale(client)
    row = ok(command(client, create(client, source, layers, 'RESTOCK'), 'authorize'))
    original = db_session.flush
    async def fail(*args, **kwargs):
        if any(isinstance(obj, OutboxEventModel) and obj.event_type == 'StockReturnAdjusted' for obj in db_session.new):
            raise RuntimeError('Injected return outbox failure')
        return await original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(db_session, 'flush', fail)
        with pytest.raises(RuntimeError, match='return outbox'):
            receive(client, row)
    assert client.get('/inventory').json()[0]['quantity'] == 0
    current = await db_session.get(ReturnModel, row['id'])
    assert current.status == 'AUTHORIZED' and current.items[0]['receivedQuantity'] == 0
    assert sum(await db_session.scalars(select(CostLayerModel.remaining_quantity))) == 0
    assert await db_session.scalar(select(func.count()).select_from(ReturnActionModel).where(
        ReturnActionModel.mutation_id.like('receipt:%'))) == 0


async def test_supplier_return_uses_receipt_goods_preserves_po_history_and_requests_credit(client):
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'WAC'}))
    supplier = ok(client.post('/suppliers', json={'name': 'Vendor', 'mutationId': 'vendor'}), 201)
    ok(client.post('/products', json={'sku': 'RET', 'name': 'Item', 'price_amount': '99.00'}), 201)
    po = ok(client.post('/purchase-orders', json={'supplierId': supplier['id'], 'items': [
        {'sku': 'RET', 'quantity': 5, 'unitCostCents': 100}], 'mutationId': 'po'}), 201)
    for step in ('approve', 'send'):
        ok(client.post('/purchase-orders/' + po['id'] + '/' + step, json={'mutationId': step}))
    receipt = ok(client.post('/purchase-orders/' + po['id'] + '/receive', json={'receiptId': 'vendor-delivery',
        'items': [{'sku': 'RET', 'quantity': 5}]}))
    source = next(m['id'] for m in client.get('/inventory/movements').json() if m['operation'] == 'receive')
    ok(client.post('/inventory/receive', json={'sku': 'RET', 'quantity': 5, 'unitCostCents': 200}))
    layer_id = receipt['stockResults'][0]['costLayerId']
    row = create(client, source, [{'layerId': layer_id, 'quantity': 2}], None, kind='SUPPLIER')
    row = ok(command(client, row, 'authorize'))
    shipped = ok(client.post('/returns/rmas/' + row['id'] + '/ship', json={'shipmentId': 'vendor-shipment',
        'expectedVersion': row['version'], 'items': [{'layerId': layer_id, 'quantity': 2}]}))
    assert shipped['status'] == 'SHIPPED' and shipped['items'][0]['shippedQuantity'] == 2
    result = shipped['stockResults'][0]
    assert result['supplierCreditExpectedCents'] == 200 and result['inventoryValueChangeCents'] == -300
    assert result['creditVarianceCents'] == -100
    assert result['quantity'] == 8 and result['cogsReversedCents'] == 0
    po = client.get('/purchase-orders/' + po['id']).json()
    assert po['status'] == 'received' and po['items'][0]['receivedQuantity'] == 5


@pytest.mark.parametrize('quantity', [0, -1, True, 1.5])
async def test_writeoff_requires_positive_integer_and_protects_reservations(client, quantity):
    source, layers = sale(client, quantity=5, sale_quantity=1)
    stock = client.get('/inventory').json()[0]
    body = {'sku': 'RET', 'quantity': quantity, 'expectedVersion': stock['version'],
        'reasonCode': 'THEFT', 'reason': 'Missing goods', 'mutationId': 'invalid'}
    assert client.post('/returns/write-offs', json=body).status_code == 422


async def test_writeoff_rejects_reserved_goods_and_keeps_warehouse_costs_hidden(client):
    source, layers = sale(client, quantity=5, sale_quantity=1)
    ok(client.post('/inventory/allocate', json={'sku': 'RET', 'quantity': 4}))
    stock = client.get('/inventory').json()[0]
    assert client.post('/returns/write-offs', json={'sku': 'RET', 'quantity': 1, 'expectedVersion': stock['version'],
        'reasonCode': 'THEFT', 'reason': 'Missing', 'mutationId': 'reserved'}).status_code == 409


async def test_cross_tenant_and_read_only_return_boundaries(client):
    source, layers = sale(client)
    row = create(client, source, layers)
    ok(client.post('/auth/setup', json={'tenantId': 'other', 'orgName': 'Other', 'adminName': 'Admin',
        'adminEmail': 'other@example.com', 'adminPassword': 'test-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'other', 'email': 'other@example.com', 'password': 'test-password'}))['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get('/returns/rmas', headers=headers).json() == []
    assert client.post('/returns/rmas', headers=headers, json={'sourceMovementId': source,
        'items': [{'layerId': layers[0]['layerId'], 'quantity': 1}], 'reason': 'Foreign source',
        'mutationId': 'foreign-source'}).status_code == 404
    assert client.get('/returns/rmas/' + row['id'], headers=headers).status_code == 404
    assert client.post('/returns/rmas/' + row['id'] + '/authorize', headers=headers,
        json={'expectedVersion': 0, 'reason': 'Unauthorized', 'mutationId': 'foreign'}).status_code == 404
    user = ok(client.post('/users', json={'email': 'viewer@example.com', 'role': 'VIEWER', 'password': 'test-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'], 'password': 'test-password'}))['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get('/returns/rmas', headers=headers).status_code == 200
    assert client.post('/returns/write-offs', headers=headers, json={}).status_code == 403


async def test_return_after_lot_expiry_requires_quarantine_and_cannot_be_released(client, monkeypatch):
    from datetime import datetime, timezone
    from src.application import return_service
    source, layers = sale(client, quantity=1, sale_quantity=1, expirationDate='2030-01-01T00:00:00Z')
    class ExpiredClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2031, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(return_service, 'datetime', ExpiredClock)
    row = ok(command(client, create(client, source, layers, 'RESTOCK'), 'authorize'))
    assert receive(client, row).status_code == 409
    ok(command(client, row, 'cancel'))
    row = ok(command(client, create(client, source, layers), 'authorize'))
    ok(receive(client, row))
    quarantine = client.get('/returns/quarantine').json()[0]
    body = {'resolution': 'RESTOCK', 'expectedVersion': quarantine['version'], 'reason': 'Inspect', 'mutationId': 'expired'}
    assert client.post('/returns/quarantine/' + quarantine['id'] + '/resolve', json=body).status_code == 409
    body.update(resolution='SCRAP', mutationId='dispose-expired')
    assert client.post('/returns/quarantine/' + quarantine['id'] + '/resolve', json=body).status_code == 200
