import uuid
import pytest
from sqlalchemy import select, func
from src.infrastructure.procurement_models import PurchaseOrderModel, ProcurementActionModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.cost_models import CostLayerModel

pytestmark = pytest.mark.asyncio


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def setup(client, skus=('A',), **fields):
    supplier = ok(client.post('/suppliers', json={'name': 'Wholesale', 'leadTimeDays': 7,
                                                 'mutationId': str(uuid.uuid4())}), 201)
    for sku in skus:
        ok(client.post('/products', json={'sku': sku, 'name': sku, 'price_amount': '9.00'}), 201)
    order = ok(client.post('/purchase-orders', json={'supplierId': supplier['id'],
        'items': [{'sku': sku, 'quantity': 10, 'unitCostCents': 100} for sku in skus],
        'mutationId': str(uuid.uuid4()), **fields}), 201)
    return supplier, order


def action(client, order, name, **fields):
    return client.post('/purchase-orders/' + order['id'] + '/' + name,
                       json={'mutationId': str(uuid.uuid4()), **fields})


def sent(client, order):
    ok(action(client, order, 'approve'))
    return ok(action(client, order, 'send'))


def receive(client, order, quantity=4, receipt='delivery-1', items=None):
    return client.post('/purchase-orders/' + order['id'] + '/receive', json={'receiptId': receipt,
        'items': items or [{'sku': 'A', 'quantity': quantity}]})


async def test_partial_receipts_replay_overreceipt_and_cancellation(client, db_session):
    supplier, order = setup(client)
    assert receive(client, order).status_code == 409
    assert action(client, order, 'send').status_code == 409
    order = sent(client, order)
    first = ok(receive(client, order))
    assert first['status'] == 'partially_received' and first['outstandingQuantity'] == 6
    assert ok(receive(client, order)) == first
    assert receive(client, order, 5).status_code == 409
    assert receive(client, order, 7, 'too-many').status_code == 409
    assert client.get('/inventory').json()[0]['quantity'] == 4
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 400
    final = ok(receive(client, order, 6, 'delivery-2'))
    assert final['status'] == 'received' and final['outstandingQuantity'] == 0
    assert receive(client, order, 1, 'delivery-3').status_code == 409
    assert action(client, order, 'cancel', reason='Stop').status_code == 409
    assert ok(receive(client, order, 6, 'delivery-2')) == final
    layers = list(await db_session.scalars(select(CostLayerModel)))
    assert sum(layer.remaining_quantity for layer in layers) == 10
    assert {layer.unit_cost_cents for layer in layers} == {100}
    movements = client.get('/inventory/movements').json()
    assert len(movements) == 2 and movements[0]['result']['reference']['purchaseOrderId'] == order['id']
    assert client.get('/purchase-orders').json()[0]['id'] == order['id']


async def test_repeated_state_commands_do_not_duplicate_issue_events(client, db_session):
    _, order = setup(client)
    approved = ok(action(client, order, 'approve'))
    assert ok(action(client, order, 'approve')) == approved
    sent_order = ok(action(client, order, 'send'))
    assert ok(action(client, order, 'send')) == sent_order
    assert await db_session.scalar(select(func.count()).select_from(OutboxEventModel)) == 4


async def test_receipt_failure_rolls_back_all_lines_and_order(client, db_session, monkeypatch):
    _, order = setup(client, ('A', 'B'))
    sent(client, order)
    original = db_session.flush
    async def fail(*args, **kwargs):
        if any(isinstance(row, OutboxEventModel) and row.payload.get('sku') == 'B' for row in db_session.new):
            raise RuntimeError('Injected second-line outbox failure')
        return await original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(db_session, 'flush', fail)
        with pytest.raises(RuntimeError, match='second-line'):
            receive(client, order, items=[{'sku': 'A', 'quantity': 4}, {'sku': 'B', 'quantity': 4}])
    assert client.get('/inventory').json() == []
    row = await db_session.get(PurchaseOrderModel, order['id'])
    assert row.status == 'SENT' and all(i['receivedQuantity'] == 0 for i in row.items)
    assert await db_session.scalar(select(func.count()).select_from(CostLayerModel)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ProcurementActionModel).where(
        ProcurementActionModel.mutation_id.like('receipt:%'))) == 0
    assert ok(receive(client, order, items=[{'sku': 'A', 'quantity': 4}, {'sku': 'B', 'quantity': 4}]))['status'] == 'partially_received'


async def test_cancel_remaining_does_not_reverse_receipts_or_count_incoming(client):
    supplier, order = setup(client)
    sent(client, order)
    ok(receive(client, order))
    cancelled = ok(action(client, order, 'cancel', reason='Supplier cannot deliver balance'))
    assert cancelled['outstandingQuantity'] == 0 and cancelled['items'][0]['receivedQuantity'] == 4
    assert client.get('/inventory').json()[0]['quantity'] == 4
    assert receive(client, order, 6, 'late').status_code == 409
    assert action(client, order, 'send').status_code == 409


@pytest.mark.parametrize('quantity', [0, -1, True, 1.5])
async def test_order_and_receipt_integer_validation(client, quantity):
    supplier, order = setup(client)
    body = {'supplierId': supplier['id'], 'mutationId': 'invalid',
            'items': [{'sku': 'A', 'quantity': quantity, 'unitCostCents': 0}]}
    assert client.post('/purchase-orders', json=body).status_code == 422
    assert receive(client, order, quantity).status_code == 422


async def test_owned_active_suppliers_products_and_mutation_contract(client):
    supplier, order = setup(client)
    body = {'supplierId': supplier['id'], 'items': [{'sku': 'A', 'quantity': 10, 'unitCostCents': 0}], 'mutationId': 'po-id'}
    created = ok(client.post('/purchase-orders', json=body), 201)
    assert ok(client.post('/purchase-orders', json=body), 201) == created
    body['items'][0]['unitCostCents'] = 1
    assert client.post('/purchase-orders', json=body).status_code == 409
    body['mutationId'] = 'duplicate-lines'
    body['items'] *= 2
    assert client.post('/purchase-orders', json=body).status_code == 400
    body['mutationId'], body['items'] = 'unknown-sku', [{'sku': 'missing', 'quantity': 1, 'unitCostCents': 0}]
    assert client.post('/purchase-orders', json=body).status_code == 404
    ok(client.patch('/suppliers/' + supplier['id'], json={'name': supplier['name'], 'isActive': False,
        'expectedVersion': 0, 'mutationId': 'disable'}))
    assert action(client, order, 'approve').status_code == 404
    assert client.post('/users', json={'email': 'supplier@example.com', 'role': 'SUPPLIER_USER',
                                     'supplierId': supplier['id']}).status_code == 404
    assert client.get('/suppliers').json()[0]['leadTimeDays'] == 7


async def test_reorder_incoming_lead_time_packs_and_expiry(client):
    supplier, order = setup(client)
    policy = ok(client.post('/reorder-policies', json={'sku': 'A', 'supplierId': supplier['id'],
        'reorderPoint': 5, 'targetStock': 15, 'casePack': 6, 'minimumOrderQuantity': 10,
        'unitCostCents': 100, 'mutationId': 'policy'}))
    first = client.get('/reorder-policies/suggestions').json()[0]
    assert first['incomingQuantity'] == 0 and first['suggestedQuantity'] == 18
    sent(client, order)
    incoming = client.get('/reorder-policies/suggestions').json()[0]
    assert incoming['incomingQuantity'] == 10 and incoming['suggestedQuantity'] == 0
    ok(receive(client, order))
    incoming = client.get('/reorder-policies/suggestions').json()[0]
    assert incoming['availableQuantity'] == 4 and incoming['incomingQuantity'] == 6
    ok(action(client, order, 'cancel', reason='Cancel remainder'))
    ok(client.post('/inventory/receive', json={'sku': 'A', 'quantity': 3, 'unitCostCents': 100,
        'quarantined': True, 'expirationDate': '2020-01-01T00:00:00Z'}))
    value = client.get('/reorder-policies/suggestions').json()[0]
    assert value['availableQuantity'] == 4 and value['unusableQuantity'] == 3 and value['suggestedQuantity'] == 12
    draft_body = {'expectedVersion': policy['version'], 'mutationId': 'suggestion-draft'}
    draft = ok(client.post('/reorder-policies/' + policy['id'] + '/draft', json=draft_body), 201)
    assert draft['items'][0]['quantity'] == 12 and draft['status'] == 'draft'
    assert ok(client.post('/reorder-policies/' + policy['id'] + '/draft', json=draft_body), 201) == draft
    assert client.get('/reorder-policies/suggestions').json()[0]['incomingQuantity'] == 0
    assert client.post('/reorder-policies', json={'sku': 'A', 'supplierId': supplier['id'],
        'reorderPoint': 5, 'mutationId': 'overwrite-without-version'}).status_code == 409


async def test_supplier_portal_is_bound_persistent_and_does_not_receive_stock(client):
    supplier, order = setup(client)
    other = ok(client.post('/suppliers', json={'name': 'Other', 'mutationId': 'other'}), 201)
    user = ok(client.post('/users', json={'email': 'supplier@example.com', 'role': 'SUPPLIER_USER',
        'supplierId': supplier['id'], 'password': 'test-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'], 'password': 'test-password'}))['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get('/api/supplier-portal/pos', headers=headers).json()['pos'] == []
    sent(client, order)
    assert client.get('/api/supplier-portal/pos', headers=headers).json()['pos'][0]['id'] == order['id']
    acknowledgment = {'expected_delivery_date': '2030-01-01T00:00:00Z', 'mutationId': 'ack'}
    assert client.post('/api/supplier-portal/pos/' + order['id'] + '/acknowledge', headers=headers, json=acknowledgment).status_code == 200
    asn = {'po_id': order['id'], 'tracking_number': 'TRACK', 'estimated_delivery_date': '2030-01-01T00:00:00Z',
           'items': [{'sku': 'A', 'shipped_quantity': 10}], 'mutationId': 'asn'}
    result = ok(client.post('/api/supplier-portal/asns', headers=headers, json=asn))
    assert ok(client.post('/api/supplier-portal/asns', headers=headers, json=asn)) == result
    assert client.get('/api/supplier-portal/asns', headers=headers).json()['asns'][0]['id'] == result['asn_id']
    assert client.get('/purchase-orders/' + order['id'] + '/asns').json()[0]['id'] == result['asn_id']
    assert client.get('/inventory').json() == []
    assert client.post('/purchase-orders/' + order['id'] + '/receive', headers=headers, json={}).status_code == 403
    assert client.get('/api/supplier-portal/performance', headers=headers).json()['performance']['otif_percentage'] is None
    asn['mutationId'] = 'extra-shipment'
    assert client.post('/api/supplier-portal/asns', headers=headers, json=asn).status_code == 409
    unknown = ok(client.post('/purchase-orders', json={'supplierId': other['id'],
        'items': [{'sku': 'A', 'quantity': 1, 'unitCostCents': 100}], 'mutationId': 'other-order'}), 201)
    sent(client, unknown)
    assert client.post('/api/supplier-portal/pos/' + unknown['id'] + '/acknowledge', headers=headers,
                       json={**acknowledgment, 'mutationId': 'wrong-supplier'}).status_code == 404
    ok(receive(client, order, 10))
    assert client.get('/api/supplier-portal/performance', headers=headers).json()['performance']['otif_percentage'] == 100


async def test_tenant_and_reader_boundaries(client):
    supplier, order = setup(client)
    ok(client.post('/auth/setup', json={'tenantId': 'other', 'orgName': 'Other', 'adminName': 'Admin',
        'adminEmail': 'other@example.com', 'adminPassword': 'test-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'other', 'email': 'other@example.com', 'password': 'test-password'}))['token']
    headers = {'Authorization': 'Bearer ' + token}
    assert client.get('/purchase-orders', headers=headers).json() == []
    assert client.get('/suppliers', headers=headers).json() == []
    assert client.get('/purchase-orders/' + order['id'], headers=headers).status_code == 404
    assert client.post('/purchase-orders/' + order['id'] + '/receive', headers=headers,
        json={'receiptId': 'foreign', 'items': [{'sku': 'A', 'quantity': 1}]}).status_code == 404
    user = ok(client.post('/users', json={'email': 'reader@example.com', 'role': 'VIEWER', 'password': 'test-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'], 'password': 'test-password'}))['token']
    reader = {'Authorization': 'Bearer ' + token}
    assert client.get('/purchase-orders', headers=reader).status_code == 200
    assert client.post('/purchase-orders', headers=reader, json={}).status_code == 403


@pytest.mark.parametrize('method', ['FIFO', 'LIFO', 'WAC', 'FEFO'])
async def test_po_receipts_use_existing_costing_methods_and_lot_metadata(client, method):
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': method}))
    _, order = setup(client)
    sent(client, order)
    result = ok(receive(client, order, items=[{'sku': 'A', 'quantity': 10,
        'lotNumber': 'LOT-1', 'expirationDate': '2030-01-01T00:00:00Z'}]))
    assert result['stockResults'][0]['costingMethod'] == method
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 1000
