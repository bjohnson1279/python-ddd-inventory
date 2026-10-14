import pytest
from unittest.mock import patch
from sqlalchemy import select
from src.infrastructure.cost_models import CostLayerModel

pytestmark = pytest.mark.asyncio


def product(client, sku):
    response = client.post('/products', json={'sku': sku, 'name': sku, 'price_amount': '1.00'})
    assert response.status_code == 201, response.text
    return response.json()


def register(client, number='SN-1', sku='SERIAL'):
    response = client.post('/serials', json={'sku': sku, 'serialNumber': number, 'locationId': 'shop',
        'mutationId': 'register-' + number, 'reason': 'Register item'})
    assert response.status_code == 201, response.text
    return response.json()


def serial(client, action, version, key, number='SN-1', **extra):
    return client.post('/api/serials/' + number + '/' + action, json={'expectedVersion': version,
        'mutationId': key, 'reason': action, 'referenceId': 'sale-1', **extra})


def stocks(client):
    return {s['sku']: s for s in client.get('/inventory').json() if s['locationId'] == 'shop'}


def define(client, components=None, version=0, key='formula'):
    return client.post('/kits', json={'sku': 'KIT', 'name': 'Retail bundle', 'expectedVersion': version,
        'mutationId': key, 'reason': 'Define bundle', 'components': components or [{'sku': 'PART', 'quantity': 2}]})


def kit(client, action, quantity, key, version=1, **extra):
    current = stocks(client)
    skus = {'KIT'} if action == 'disassemble' else {'PART'} if action == 'sale' else {'PART', 'KIT'}
    payload = {'kitSku': 'KIT', 'locationId': 'shop', 'quantity': quantity,
        'expectedVersion': version, 'mutationId': key, 'reason': action, 'referenceId': key,
        'stockVersions': {sku: current.get(sku, {}).get('version', 0) for sku in skus}, **extra}
    return client.post('/kits/' + action, json=payload), payload


async def test_serial_receipt_reservation_sale_return_and_original_cost(client):
    product(client, 'SERIAL')
    first = register(client)
    assert first['status'] == 'pending' and first['history'][0]['actor']
    receipt = serial(client, 'receive', 0, 'receive', unitCostCents=123)
    assert receipt.status_code == 200, receipt.text
    assert serial(client, 'receive', 0, 'receive', unitCostCents=123).json() == receipt.json()
    assert serial(client, 'receive', 0, 'receive', unitCostCents=124).status_code == 409
    assert client.get('/inventory/products').json()[0]['variants'][0]['trackingMode'] == 'serial'
    assert client.post('/inventory/dispatch', json={'sku': 'SERIAL', 'locationId': 'shop', 'quantity': 1}).status_code == 400
    assert serial(client, 'reserve', 1, 'reserve').status_code == 200
    assert stocks(client)['SERIAL']['allocated'] == 1
    assert serial(client, 'write-off', 2, 'reserved-loss').status_code == 409
    assert serial(client, 'sell', 2, 'wrong-sale', referenceId='other-sale').status_code == 409
    sold = serial(client, 'sell', 2, 'sell')
    assert sold.status_code == 200, sold.text
    assert stocks(client)['SERIAL']['quantity'] == 0
    returned = serial(client, 'return', 3, 'return')
    assert returned.status_code == 200, returned.text
    assert returned.json()['costCents'] == 123 and stocks(client)['SERIAL']['quantity'] == 1
    assert serial(client, 'sell', 4, 'sell-returned').status_code == 409
    assert serial(client, 'restock', 4, 'restock').status_code == 200
    assert serial(client, 'sell', 5, 'resale').status_code == 200
    trace = client.get('/serials/trace/SN-1').json()
    assert trace['status'] == 'sold' and len(trace['history']) == 7
    assert client.get('/outbox/stats').json()['pending'] >= 7


async def test_serial_transfer_value_and_writeoff_preserve_owned_value(client):
    product(client, 'SERIAL')
    register(client)
    assert serial(client, 'receive', 0, 'receive', unitCostCents=101).status_code == 200
    moved = serial(client, 'transfer', 1, 'move', destinationId='branch')
    assert moved.status_code == 200, moved.text
    assert stocks(client)['SERIAL']['quantity'] == 0
    value = client.get('/accounting/valuation-report').json()[0]
    assert value['inTransitQuantity'] == 1 and value['totalOwnedValueCents'] == 101
    assert value['costingMethod'] == 'SPECIFIC_IDENTIFICATION'
    assert serial(client, 'receive', 2, 'receive-branch', locationId='branch').status_code == 200
    assert serial(client, 'write-off', 3, 'loss').status_code == 200
    assert sum(s['quantity'] for s in client.get('/inventory').json()) == 0
    assert serial(client, 'receive', 4, 'resurrect').status_code == 409


async def test_serial_registration_validation_isolation_and_unknown_cost(client):
    product(client, 'SERIAL')
    register(client)
    assert client.post('/serials', json={'sku': 'SERIAL', 'serialNumber': 'SN-1', 'mutationId': 'duplicate', 'reason': 'duplicate'}).status_code == 409
    assert serial(client, 'receive', 0, 'unpriced').status_code == 200
    assert serial(client, 'sell', 0, 'stale').status_code == 409
    assert serial(client, 'sell', 1, 'sell-unpriced').json()['costCents'] is None
    assert client.get('/serials?tenantId=other').status_code == 403
    assert client.get('/serials/trace/missing').status_code == 404
    assert client.post('/serials', json={'sku': 'SERIAL', 'serialNumber': 'bad space', 'mutationId': 'bad', 'reason': 'bad'}).status_code == 422


@pytest.mark.parametrize('method', ['FIFO', 'LIFO', 'WAC', 'FEFO'])
async def test_kit_assembly_partial_disassembly_sale_and_value_conservation(client, method):
    product(client, 'PART')
    product(client, 'KIT')
    assert client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': method, 'accountingMethod': 'ACCRUAL'}).status_code == 200
    assert client.post('/inventory/receive', json={'sku': 'PART', 'locationId': 'shop', 'quantity': 10, 'unitCostCents': 101}).status_code == 200
    assert define(client).status_code == 201
    response, payload = kit(client, 'assemble', 3, 'build')
    assert response.status_code == 200, response.text
    assert client.post('/kits/assemble', json=payload).json() == response.json()
    assert stocks(client)['PART']['quantity'] == 4 and stocks(client)['KIT']['quantity'] == 3
    value_before = sum(v['totalOwnedValueCents'] for v in client.get('/accounting/valuation-report').json())
    response, _ = kit(client, 'disassemble', 1, 'undo-one')
    assert response.status_code == 200, response.text
    assert stocks(client)['PART']['quantity'] == 6 and stocks(client)['KIT']['quantity'] == 2
    assert sum(v['totalOwnedValueCents'] for v in client.get('/accounting/valuation-report').json()) == value_before == 1010
    response, _ = kit(client, 'sale', 2, 'bundle-sale')
    assert response.status_code == 200, response.text
    assert response.json()['costOfGoodsSoldCents'] == 404
    assert stocks(client)['PART']['quantity'] == 2 and stocks(client)['KIT']['quantity'] == 2


async def test_kit_formula_edits_preserve_original_manifest_and_reject_nested_serial(client):
    for sku in ('PART', 'KIT', 'SERIAL'):
        product(client, sku)
    register(client)
    assert define(client, [{'sku': 'SERIAL', 'quantity': 1}]).status_code == 400
    assert define(client, [{'sku': 'KIT', 'quantity': 1}]).status_code == 400
    assert client.post('/inventory/receive', json={'sku': 'PART', 'locationId': 'shop', 'quantity': 5, 'unitCostCents': 10}).status_code == 200
    assert define(client).status_code == 201
    built, payload = kit(client, 'assemble', 1, 'original-build')
    assert built.status_code == 200, built.text
    assert define(client, [{'sku': 'PART', 'quantity': 3}], 1, 'new-formula').status_code == 201
    undo, _ = kit(client, 'disassemble', 1, 'original-undo', version=2)
    assert undo.status_code == 200, undo.text
    assert stocks(client)['PART']['quantity'] == 5
    assert client.post('/kits/assemble', json=payload).json() == built.json()


async def test_kit_validation_and_atomic_rollback(client, db_session):
    product(client, 'PART')
    product(client, 'KIT')
    assert client.post('/inventory/receive', json={'sku': 'PART', 'locationId': 'shop', 'quantity': 3, 'unitCostCents': 1}).status_code == 200
    assert define(client).status_code == 201
    failed, _ = kit(client, 'assemble', 2, 'insufficient')
    assert failed.status_code == 409, failed.text
    assert stocks(client)['PART']['quantity'] == 3 and 'KIT' not in stocks(client)
    from src.application.serial_kit_service import SerialKitWorkflow
    with patch('src.application.transfer_service.OutboxEventModel', side_effect=RuntimeError('Injected enqueue failure')):
        with pytest.raises(RuntimeError):
            await SerialKitWorkflow(db_session, 'tenant-1', 'operator').execute('use_kit', {'kitSku': 'KIT', 'locationId': 'shop', 'quantity': 1,
                'action': 'assemble', 'expectedVersion': 1, 'reason': 'build', 'referenceId': 'failed', 'stockVersions': {'PART': 1, 'KIT': 0}}, 'failed')
    assert stocks(client)['PART']['quantity'] == 3
    assert list(await db_session.scalars(select(CostLayerModel).where(CostLayerModel.sku == 'KIT'))) == []


async def test_serial_permissions_redaction_and_explicit_pricing(client):
    from tests.presentation.test_tenant_security import staff, other_store
    product(client, 'SERIAL')
    register(client)
    _, operator = staff(client, 'WAREHOUSE_OPERATOR', 'operator@example.com')
    _, viewer = staff(client, 'VIEWER', 'viewer@example.com')
    _, accountant = staff(client, 'ACCOUNTANT', 'accountant@example.com')
    command = {'expectedVersion': 0, 'mutationId': 'receive-operator', 'reason': 'Receipt', 'referenceId': 'receipt'}
    assert client.post('/serials/SN-1/receive', headers=viewer, json=command).status_code == 403
    assert client.post('/serials/SN-1/receive', headers=operator, json={**command, 'unitCostCents': 10}).status_code == 403
    assert client.post('/serials/SN-1/receive', headers=operator, json=command).status_code == 200
    assert 'costCents' not in client.get('/serials/trace/SN-1', headers=operator).json()
    pricing = {'expectedVersion': 1, 'mutationId': 'price-serial', 'reason': 'Invoice matched', 'referenceId': 'invoice', 'unitCostCents': 123}
    assert client.post('/serials/SN-1/price', headers=operator, json=pricing).status_code == 403
    priced = client.post('/serials/SN-1/price', headers=accountant, json=pricing)
    assert priced.status_code == 200, priced.text
    assert priced.json()['costCents'] == 123
    assert client.post('/serials/SN-1/write-off', headers=operator, json={**command, 'expectedVersion': 2}).status_code == 403
    _, foreign = other_store(client)
    assert client.get('/serials/trace/SN-1', headers=foreign).status_code == 404
    assert client.post('/serials/SN-1/sell', headers=foreign, json=command).status_code == 404
    assert client.get('/kits', headers={'Authorization': ''}).status_code == 401


async def test_serial_specific_identification_under_wac_and_rollback(client, db_session):
    product(client, 'SERIAL')
    assert client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'WAC'}).status_code == 200
    for number, cost in [('SN-1', 101), ('SN-2', 203)]:
        register(client, number)
        assert serial(client, 'receive', 0, 'receive-' + number, number=number, unitCostCents=cost).status_code == 200
    from src.application.serial_kit_service import SerialKitWorkflow
    with patch('src.application.transfer_service.OutboxEventModel', side_effect=RuntimeError('Injected enqueue failure')):
        with pytest.raises(RuntimeError):
            await SerialKitWorkflow(db_session, 'tenant-1', 'operator').execute('change_serial', {'serialNumber': 'SN-2',
                'action': 'sell', 'expectedVersion': 1, 'reason': 'Sale', 'referenceId': 'failed-sale'}, 'failed-sale')
    assert client.get('/serials/trace/SN-2').json()['version'] == 1
    assert stocks(client)['SERIAL']['quantity'] == 2
    assert serial(client, 'sell', 1, 'specific-sale', number='SN-2').json()['costCents'] == 203
    assert client.get('/accounting/valuation-report').json()[0]['totalOwnedValueCents'] == 101


async def test_returned_serial_preserves_expiration_and_cannot_sell_expired_goods(client, db_session):
    from datetime import datetime, timezone
    from src.infrastructure.serial_kit_models import SerialModel
    product(client, 'SERIAL')
    register(client)
    assert serial(client, 'receive', 0, 'receive', unitCostCents=10, expirationDate='2099-01-01T00:00:00Z').status_code == 200
    assert serial(client, 'sell', 1, 'sale').status_code == 200
    row = await db_session.scalar(select(SerialModel))
    layer = await db_session.get(CostLayerModel, row.layer_id)
    layer.expiration_date = datetime(2000, 1, 1, tzinfo=timezone.utc)
    await db_session.commit()
    assert serial(client, 'return', 2, 'return').status_code == 200
    assert serial(client, 'restock', 3, 'restock').status_code == 200
    assert serial(client, 'sell', 4, 'expired-sale').status_code == 409
    assert stocks(client)['SERIAL']['quantity'] == 1


async def test_kit_preserves_fractional_base_costs_and_multi_component_rollback(client):
    for sku in ('PART', 'OTHER', 'KIT'):
        product(client, sku)
    assert client.post('/uom/configurations', json={'sku': 'PART', 'baseUnit': 'ea', 'category': 'discrete',
        'rules': [{'unit': 'pack', 'factorToBase': '2'}], 'expectedVersion': 0, 'mutationId': 'pack'}).status_code == 200
    assert client.post('/inventory/receive', json={'sku': 'PART', 'locationId': 'shop', 'quantity': 1,
        'unit': 'pack', 'uomVersion': 1, 'unitCostCents': 101, 'mutationId': 'pack-receipt'}).status_code == 200
    assert client.post('/inventory/receive', json={'sku': 'OTHER', 'locationId': 'shop', 'quantity': 2}).status_code == 200
    assert define(client, [{'sku': 'PART', 'quantity': 1}, {'sku': 'OTHER', 'quantity': 1}]).status_code == 201
    failed, _ = kit(client, 'assemble', 1, 'unpriced', stockVersions={'PART': 1, 'OTHER': 1, 'KIT': 0})
    assert failed.status_code == 400, failed.text
    assert stocks(client)['PART']['quantity'] == 2 and stocks(client)['OTHER']['quantity'] == 2
    assert define(client, [{'sku': 'PART', 'quantity': 1}], 1, 'priced-formula').status_code == 201
    built, _ = kit(client, 'assemble', 2, 'fractional-build', version=2)
    assert built.status_code == 200, built.text
    assert built.json()['carryingValueCents'] == 101
    undone, _ = kit(client, 'disassemble', 2, 'fractional-undo', version=2)
    assert undone.status_code == 200, undone.text
    assert undone.json()['carryingValueCents'] == 101 and stocks(client)['PART']['quantity'] == 2


async def test_new_sales_feed_replenishment_and_bundle_components_can_be_returned(client):
    for sku in ('PART', 'KIT', 'SERIAL'):
        product(client, sku)
    assert client.post('/inventory/receive', json={'sku': 'PART', 'locationId': 'shop', 'quantity': 4, 'unitCostCents': 10}).status_code == 200
    assert define(client).status_code == 201
    sold, _ = kit(client, 'sale', 1, 'bundle-sale')
    assert sold.status_code == 200, sold.text
    register(client, 'SN-1')
    register(client, 'SN-2')
    assert serial(client, 'receive', 0, 'receipt-1', unitCostCents=20).status_code == 200
    assert serial(client, 'receive', 0, 'receipt-2', number='SN-2', unitCostCents=20).status_code == 200
    assert serial(client, 'sell', 1, 'serial-sale').status_code == 200
    assert serial(client, 'transfer', 1, 'branch-transfer', number='SN-2', destinationId='branch').status_code == 200
    supplier = client.post('/suppliers', json={'name': 'Vendor', 'mutationId': 'vendor'}).json()
    for sku, location in [('PART', 'shop'), ('SERIAL', 'shop'), ('SERIAL', 'branch')]:
        response = client.post('/reorder-policies', json={'sku': sku, 'locationId': location, 'supplierId': supplier['id'],
            'reorderPoint': 2, 'mutationId': 'policy-' + sku + '-' + location})
        assert response.status_code == 200, response.text
    suggestions = {(s['sku'], s['locationId']): s for s in client.get('/reorder-policies/suggestions').json()}
    assert suggestions[('PART', 'shop')]['soldLast30Days'] == 2
    assert suggestions[('SERIAL', 'shop')]['soldLast30Days'] == 1
    assert suggestions[('SERIAL', 'branch')]['incomingQuantity'] == 1
    movement = next(m for m in client.get('/inventory/movements').json() if m['operation'] == 'kit-sale-component')
    assert movement['result']['costOfGoodsSoldCents'] == 20
    from tests.presentation.test_returns_api import create, command, receive
    rma = create(client, movement['id'], movement['result']['consumedLayers'])
    approval = command(client, rma, 'authorize')
    assert approval.status_code == 200, approval.text
    returned = receive(client, approval.json())
    assert returned.status_code == 200, returned.text
    assert stocks(client)['PART']['quantity'] == 4
    assert client.get('/api/aging/PART?location_id=shop').status_code == 200
    response, payload = kit(client, 'sale', 1, 'wrong-tenant', tenantId='other')
    assert response.status_code == 403
