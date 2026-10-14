import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy import select, func
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.transfer_models import TransferModel, InventoryActionModel
from src.infrastructure.messaging.models import OutboxEventModel
from tests.presentation.test_tenant_security import staff, other_store

pytestmark = pytest.mark.asyncio


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def product(client, sku='MOVE'):
    return ok(client.post('/products', json={'sku': sku, 'name': sku, 'price_amount': '10.00'}), 201)


def stocks(client):
    return {(row['sku'], row['locationId']): row for row in ok(client.get('/inventory'))}


def receipt(client, sku='MOVE', quantity=2, cost=100, location='shop', **fields):
    return ok(client.post('/inventory/receive', json={'sku': sku, 'quantity': quantity,
        'unitCostCents': cost, 'locationId': location, **fields}))


def create(client, items=None, **fields):
    return ok(client.post('/inventory/transfers', json={'sourceLocationId': 'shop', 'destinationLocationId': 'store',
        'items': items or [{'sku': 'MOVE', 'quantity': 2}], 'mutationId': str(uuid.uuid4()), **fields}), 201)


def command(client, row, action, **fields):
    return client.post('/inventory/transfers/' + row['id'] + '/' + action, json={
        'expectedVersion': row['version'], 'mutationId': str(uuid.uuid4()), 'reason': 'Verified movement', **fields})


def configure(client, sku='MOVE', **fields):
    return ok(client.post('/uom/configurations', json={'sku': sku, 'baseUnit': 'ea', 'category': 'discrete',
        'rules': [{'unit': 'case', 'factorToBase': '24'}], 'purchaseUnit': 'case',
        'expectedVersion': 0, 'mutationId': str(uuid.uuid4()), **fields}))


@pytest.mark.parametrize('method', ['FIFO', 'LIFO', 'WAC', 'FEFO'])
async def test_transfer_preserves_total_ownership_cost_and_lot_metadata(client, method):
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': method}))
    product(client)
    for cost in (1, 2):
        receipt(client, cost=cost, lotNumber='lot-' + str(cost), expirationDate='2035-01-01T00:00:00Z')
    receipt(client, cost=3, location='store')
    row = create(client, [{'sku': 'MOVE', 'quantity': 3}])
    body = {'expectedVersion': 0, 'mutationId': 'dispatch', 'reason': 'Truck collected', 'sourceVersions': {'MOVE': 2}}
    endpoint = '/api/inventory/transfers/' + row['id'] + '/dispatch'
    row = ok(client.post(endpoint, json=body))
    assert ok(client.post(endpoint, json=body)) == row
    assert row['stockResults'][0]['costOfGoodsSoldCents'] == 0
    valuation = ok(client.get('/accounting/valuation-report'))[0]
    assert (valuation['totalQuantity'], valuation['inTransitQuantity'], valuation['totalOwnedQuantity']) == (3, 3, 6)
    assert valuation['totalOwnedValueCents'] == 12
    assert client.post('/inventory/dispatch', json={'sku': 'MOVE', 'locationId': 'store', 'quantity': 3}).status_code == 400
    assert command(client, row, 'dispatch', sourceVersions={'MOVE': 3}).status_code == 409
    received = ok(command(client, row, 'receive'))
    assert received['status'] == 'RECEIVED'
    assert stocks(client)[('MOVE', 'store')]['quantity'] == 5
    assert ok(client.get('/accounting/valuation-report'))[0]['totalOwnedValueCents'] == 12
    layers = ok(client.get('/accounting/cost-layers?sku=MOVE&locationId=store'))
    assert any(layer['lotNumber'] and layer['expirationDate'].startswith('2035') for layer in layers)
    assert command(client, received, 'cancel').status_code == 409


async def test_cancel_transit_restores_source_and_blocks_method_change_until_resolved(client):
    product(client)
    receipt(client, cost=101)
    row = create(client)
    row = ok(command(client, row, 'dispatch', sourceVersions={'MOVE': 1}))
    assert stocks(client)[('MOVE', 'shop')]['quantity'] == 0
    assert client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'WAC'}).status_code == 409
    payload = {'expectedVersion': row['version'], 'reason': 'Goods returned to source', 'mutationId': 'cancel'}
    endpoint = '/inventory/transfers/' + row['id'] + '/cancel'
    result = ok(client.post(endpoint, json=payload))
    assert ok(client.post(endpoint, json=payload)) == result
    assert result['status'] == 'CANCELLED'
    assert stocks(client)[('MOVE', 'shop')]['quantity'] == 2
    report = ok(client.get('/accounting/valuation-report'))[0]
    assert report['totalOwnedValueCents'] == 202 and report['inTransitQuantity'] == 0
    draft = create(client)
    ok(command(client, draft, 'cancel'))
    assert stocks(client)[('MOVE', 'shop')]['quantity'] == 2


async def test_multi_item_failure_rolls_back_stock_costs_workflow_and_events(client, db_session):
    for sku in ('MOVE', 'SHORT'):
        product(client, sku)
        receipt(client, sku=sku, quantity=2 if sku == 'MOVE' else 1)
    row = create(client, [{'sku': sku, 'quantity': 2} for sku in ('MOVE', 'SHORT')])
    before = await db_session.scalar(select(func.count()).select_from(OutboxEventModel))
    assert command(client, row, 'dispatch', sourceVersions={'MOVE': 1, 'SHORT': 1}).status_code == 409
    assert stocks(client)[('MOVE', 'shop')]['quantity'] == 2
    assert ok(client.get('/inventory/transfers/' + row['id']))['status'] == 'DRAFT'
    assert await db_session.scalar(select(func.count()).select_from(OutboxEventModel)) == before
    assert await db_session.scalar(select(func.count()).select_from(InventoryActionModel)) == 1
    assert ok(client.get('/accounting/valuation-report'))[0]['totalOwnedValueCents'] == 200


async def test_transfer_outbox_failure_rolls_back_dispatch(client, monkeypatch, db_session):
    product(client)
    receipt(client)
    row = create(client)
    original = db_session.add
    def fail_event(value, *args, **kwargs):
        if isinstance(value, OutboxEventModel):
            raise RuntimeError('Injected transfer event failure')
        original(value, *args, **kwargs)
    monkeypatch.setattr(db_session, 'add', fail_event)
    with pytest.raises(RuntimeError, match='Injected transfer'):
        command(client, row, 'dispatch', sourceVersions={'MOVE': 1})
    monkeypatch.setattr(db_session, 'add', original)
    assert stocks(client)[('MOVE', 'shop')]['quantity'] == 2
    assert ok(client.get('/inventory/transfers/' + row['id']))['items'][0]['batches'] == []


async def test_transfer_checks_source_versions_reservations_lots_and_quarantine(client):
    product(client)
    receipt(client, lotNumber='good')
    receipt(client, quantity=1, quarantined=True, lotNumber='bad')
    ok(client.post('/inventory/allocate', json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 2}))
    assert client.post('/inventory/dispatch', json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 1}).status_code == 400
    row = create(client, [{'sku': 'MOVE', 'quantity': 1, 'lotNumber': 'good'}])
    assert command(client, row, 'dispatch', sourceVersions={'MOVE': 2}).status_code == 409
    assert command(client, row, 'dispatch', sourceVersions={'MOVE': 3}).status_code == 409
    ok(client.post('/inventory/release-allocation', json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 2}))
    blocked = create(client, [{'sku': 'MOVE', 'quantity': 1, 'lotNumber': 'bad'}])
    assert command(client, blocked, 'dispatch', sourceVersions={'MOVE': 4}).status_code == 400
    assert client.post('/inventory/transfers', json={'sourceLocationId': 'shop', 'destinationLocationId': 'shop',
        'items': [{'sku': 'MOVE', 'quantity': 1}], 'mutationId': 'same'}).status_code == 400


async def test_expiry_during_transit_is_preserved_and_not_sellable(client, db_session):
    product(client)
    receipt(client, expirationDate='2035-01-01T00:00:00Z')
    row = ok(command(client, create(client), 'dispatch', sourceVersions={'MOVE': 1}))
    # Simulate expiry in the persisted transit snapshot, independent of wall clock.
    stored = await db_session.get(TransferModel, row['id'])
    import copy
    items = copy.deepcopy(stored.items)
    items[0]['batches'][0]['expirationDate'] = '2020-01-01T00:00:00+00:00'
    stored.items = items
    await db_session.commit()
    ok(command(client, row, 'receive'))
    assert client.post('/inventory/dispatch', json={'sku': 'MOVE', 'locationId': 'store', 'quantity': 1}).status_code == 400


@pytest.mark.parametrize('method', ['FIFO', 'LIFO', 'WAC', 'FEFO'])
async def test_case_receipts_preserve_invoice_pennies_and_stock_mutations_use_base_units(client, method):
    ok(client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': method}))
    product(client)
    config = configure(client)
    data = {'sku': 'MOVE', 'locationId': 'shop', 'quantity': '2', 'unit': 'case', 'uomVersion': config['version'],
        'unitCostCents': 1001, 'mutationId': 'case-receipt'}
    result = ok(client.post('/inventory/receive', json=data))
    assert ok(client.post('/inventory/receive', json=data)) == result
    assert result['quantity'] == 48 and result['receiptValueCents'] == 2002
    assert len(result['costLayerIds']) == 1
    assert ok(client.get('/accounting/valuation-report'))[0]['totalValueCents'] == 2002
    assert stocks(client)[('MOVE', 'shop')]['baseUnit'] == 'ea'
    for op in ('allocate', 'release-allocation'):
        result = ok(client.post('/inventory/' + op, json={'sku': 'MOVE', 'locationId': 'shop',
            'quantity': '0.5', 'unit': 'case', 'uomVersion': config['version'], 'mutationId': op}))
    assert result['allocated'] == 0
    result = ok(client.post('/inventory/dispatch', json={'sku': 'MOVE', 'locationId': 'shop',
        'quantity': '0.5', 'unit': 'case', 'uomVersion': config['version'], 'mutationId': 'sale'}))
    assert result['quantity'] == 36
    value = ok(client.get('/accounting/valuation-report'))[0]['totalValueCents']
    assert value + result['costOfGoodsSoldCents'] == 2002


async def test_measured_goods_and_conversion_preview_reject_lossy_ledger_updates(client):
    product(client)
    config = configure(client, baseUnit='g', category='weight', rules=[], purchaseUnit='kg', saleUnit='g')
    result = receipt(client, quantity='1.5', cost=199, unit='kg', uomVersion=config['version'], mutationId='weighed')
    assert result['quantity'] == 1500 and result['receiptValueCents'] == 299
    assert stocks(client)[('MOVE', 'shop')]['baseUnit'] == 'g'
    preview = ok(client.post('/uom/configurations/MOVE/convert', json={'quantity': 1500, 'fromUnit': 'g', 'toUnit': 'kg'}))
    assert preview['quantity'] == '1.5'
    assert client.post('/inventory/receive', json={'sku': 'MOVE', 'quantity': '0.0001', 'unit': 'kg',
        'uomVersion': config['version'], 'mutationId': 'fractional-base'}).status_code == 400
    assert client.post('/uom/configurations/MOVE/convert', json={'quantity': 1, 'fromUnit': 'l'}).status_code == 400
    assert ok(client.get('/accounting/valuation-report'))[0]['totalValueCents'] == 299


async def test_uom_versions_rules_immutability_and_replay_after_configuration_changes(client):
    product(client)
    config = configure(client)
    conversion = ok(client.post('/uom/configurations/MOVE/convert', json={
        'quantity': 1, 'fromUnit': 'case', 'unitCostCents': 1001}))
    assert conversion['baseQuantity'] == '24'
    assert conversion['exactUnitCostCents'] == {'numerator': '1001', 'denominator': '24'}
    assert conversion['roundedUnitCostCents'] == 42
    payload = {'sku': 'MOVE', 'quantity': 1, 'unit': 'case', 'uomVersion': 1, 'unitCostCents': 2400, 'mutationId': 'receipt'}
    first = ok(client.post('/inventory/receive', json=payload))
    updated = configure(client, expectedVersion=1, rules=[{'unit': 'case', 'factorToBase': '12'}])
    assert updated['version'] == 2
    assert ok(client.post('/inventory/receive', json=payload)) == first
    assert client.post('/inventory/receive', json={**payload, 'mutationId': 'stale'}).status_code == 409
    assert client.post('/uom/configurations', json={'sku': 'MOVE', 'baseUnit': 'g', 'category': 'weight',
        'rules': [], 'expectedVersion': 2, 'mutationId': 'reinterpret'}).status_code == 409
    assert client.post('/inventory/receive', json={'sku': 'MOVE', 'quantity': '1'}).status_code == 422
    assert client.post('/inventory/receive', json={'sku': 'MOVE', 'quantity': 1, 'unit': 'case'}).status_code == 422
    assert client.post('/uom/configurations', json={'sku': 'MOVE', 'rules': [{'unit': 'case', 'factorToBase': '0'}],
        'expectedVersion': 2, 'mutationId': 'bad-factor'}).status_code == 400
    assert ok(client.get('/uom/configurations/MOVE'))['version'] == 2


async def test_transfer_uom_snapshot_survives_pack_size_update(client):
    product(client)
    configure(client)
    receipt(client, quantity=48)
    row = create(client, [{'sku': 'MOVE', 'quantity': '0.5', 'unit': 'case', 'uomVersion': 1}])
    configure(client, expectedVersion=1, rules=[{'unit': 'case', 'factorToBase': '12'}])
    row = ok(command(client, row, 'dispatch', sourceVersions={'MOVE': 1}))
    assert row['items'][0]['quantity'] == 12
    ok(command(client, row, 'receive'))
    assert stocks(client)[('MOVE', 'store')]['quantity'] == 12


async def test_named_unit_writeoff_and_base_only_workflows_do_not_silently_ignore_units(client):
    product(client)
    configure(client)
    receipt(client, quantity=24)
    payload = {'sku': 'MOVE', 'locationId': 'shop', 'quantity': '0.5', 'unit': 'case', 'uomVersion': 1,
        'expectedVersion': 1, 'reason': 'Damaged case', 'reasonCode': 'DAMAGE', 'mutationId': 'case-loss'}
    result = ok(client.post('/returns/write-offs', json=payload))
    assert result['quantity'] == 12 and result['writeOffCents'] == 1200
    assert ok(client.post('/returns/write-offs', json=payload)) == result
    assert client.post('/api/cycle-counts/adjust', json={'sku': 'MOVE', 'delta': 1, 'unit': 'case',
        'expected_version': 2, 'reason_code': 'FOUND', 'reason': 'Found', 'mutation_id': 'count'}).status_code == 422
    assert client.post('/purchase-orders', json={'supplierId': 'vendor', 'items': [
        {'sku': 'MOVE', 'quantity': 1, 'unit': 'case', 'unitCostCents': 100}], 'mutationId': 'po'}).status_code == 422
    assert stocks(client)[('MOVE', 'shop')]['quantity'] == 12


async def test_transfer_roles_tenant_boundaries_and_nested_cost_redaction(client):
    product(client)
    configure(client)
    receipt(client)
    row = create(client)
    _, reader = staff(client)
    _, warehouse = staff(client, role='WAREHOUSE_OPERATOR', email='warehouse@example.com')
    _, foreign = other_store(client)
    assert client.get('/inventory/transfers/' + row['id'], headers=foreign).status_code == 404
    assert client.get('/uom/configurations/MOVE', headers=foreign).status_code == 404
    assert client.post('/inventory/transfers/' + row['id'] + '/dispatch', headers=reader, json={
        'expectedVersion': 0, 'sourceVersions': {'MOVE': 1}, 'reason': 'Truck', 'mutationId': 'unauthorized'}).status_code == 403
    assert client.post('/uom/configurations', headers=warehouse, json={'sku': 'MOVE', 'expectedVersion': 1,
        'mutationId': 'unauthorized-config'}).status_code == 403
    row = ok(client.post('/inventory/transfers/' + row['id'] + '/dispatch', headers=warehouse, json={
        'expectedVersion': 0, 'sourceVersions': {'MOVE': 1}, 'reason': 'Truck', 'mutationId': 'warehouse-dispatch'}))
    assert 'costCents' not in row['items'][0]['batches'][0]
    assert 'inventoryValueChangeCents' not in row['stockResults'][0]
    assert 'costCents' not in ok(client.get('/inventory/transfers', headers=warehouse))[0]['items'][0]['batches'][0]
    assert 'costCents' in ok(client.get('/inventory/transfers/' + row['id']))['items'][0]['batches'][0]
    assert client.post('/inventory/transfers/' + row['id'] + '/receive', headers=foreign, json={
        'expectedVersion': 1, 'reason': 'Hijack', 'mutationId': 'foreign'}).status_code == 404
    assert client.get('/inventory/transfers', params={'tenantId': 'other'}).status_code == 403


async def test_incoming_transfers_reduce_replenishment_without_counting_as_sales(client):
    product(client)
    receipt(client, quantity=12)
    supplier = ok(client.post('/suppliers', json={'name': 'Vendor', 'mutationId': 'vendor'}), 201)
    policy = ok(client.post('/reorder-policies', json={'sku': 'MOVE', 'supplierId': supplier['id'], 'locationId': 'store',
        'reorderPoint': 5, 'targetStock': 12, 'unitCostCents': 100, 'mutationId': 'policy'}))
    assert ok(client.get('/reorder-policies/suggestions'))[0]['suggestedQuantity'] == 12
    row = ok(command(client, create(client, [{'sku': 'MOVE', 'quantity': 12}]), 'dispatch', sourceVersions={'MOVE': 1}))
    suggestion = ok(client.get('/reorder-policies/suggestions'))[0]
    assert suggestion['incomingQuantity'] == 12 and suggestion['suggestedQuantity'] == 0
    assert suggestion['soldLast30Days'] == 0
    ok(command(client, row, 'cancel'))
    assert ok(client.get('/reorder-policies/suggestions'))[0]['suggestedQuantity'] == 12


async def test_legacy_inventory_cannot_be_reinterpreted_as_weight_and_unpriced_transfers_stay_unknown(client):
    product(client)
    ok(client.post('/inventory/receive', json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 2}))
    assert client.post('/uom/configurations', json={'sku': 'MOVE', 'baseUnit': 'g', 'category': 'weight',
        'rules': [], 'expectedVersion': 0, 'mutationId': 'reinterpret'}).status_code == 409
    row = ok(command(client, create(client), 'dispatch', sourceVersions={'MOVE': 1}))
    assert row['items'][0]['batches'][0]['costCents'] is None
    assert client.get('/accounting/valuation-report').status_code == 409
    ok(command(client, row, 'receive'))
    layers = ok(client.get('/accounting/cost-layers?sku=MOVE&locationId=store'))
    assert layers[0]['unitCostCents'] is None


async def test_subcent_base_cost_rounds_partial_sales_returns_and_losses_proportionally(client):
    product(client)
    configure(client, baseUnit='g', category='weight', rules=[], purchaseUnit='kg')
    receipt(client, quantity='1.5', cost=199, unit='kg', uomVersion=1, mutationId='weighed')
    sold = ok(client.post('/inventory/dispatch', json={'sku': 'MOVE', 'locationId': 'shop',
        'quantity': '0.5', 'unit': 'kg', 'uomVersion': 1, 'mutationId': 'sale'}))
    assert sold['costOfGoodsSoldCents'] == 100
    source = next(row['id'] for row in ok(client.get('/inventory/movements')) if row['mutationId'] == 'sale')
    rma = ok(client.post('/returns/rmas', json={'sourceMovementId': source, 'reason': 'Partial return',
        'items': [{'layerId': sold['consumedLayers'][0]['layerId'], 'quantity': 250, 'disposition': 'RESTOCK'}], 'mutationId': 'rma'}), 201)
    ok(client.post('/returns/rmas/' + rma['id'] + '/authorize', json={'expectedVersion': 0, 'reason': 'Reviewed', 'mutationId': 'authorize'}))
    returned = ok(client.post('/returns/rmas/' + rma['id'] + '/receive', json={'expectedVersion': 1, 'receiptId': 'arrived',
        'items': [{'layerId': sold['consumedLayers'][0]['layerId'], 'quantity': 250}]}))
    assert returned['stockResults'][0]['cogsReversedCents'] == 50
    assert ok(client.get('/accounting/valuation-report'))[0]['totalValueCents'] == 249
    loss = ok(client.post('/returns/write-offs', json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 250,
        'layerId': returned['stockResults'][0]['restoredLayerIds'][0], 'expectedVersion': 3,
        'reasonCode': 'DAMAGE', 'reason': 'Returned goods damaged', 'mutationId': 'loss'}))
    assert loss['writeOffCents'] == 50
    final = ok(client.post('/inventory/dispatch', json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 1000}))
    assert final['costOfGoodsSoldCents'] == 199
    assert final['quantity'] == 0


async def test_valuation_preview_matches_cumulative_cent_rounding_for_remaining_layer(client):
    item = product(client)
    configure(client, rules=[{'unit': 'case', 'factorToBase': '3'}])
    receipt(client, quantity=1, cost=5, unit='case', uomVersion=1, mutationId='three-items')
    first = ok(client.post('/inventory/dispatch', json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 1}))
    preview = ok(client.get('/accounting/valuation/' + item['id'], params={'quantity': 1}))
    second = ok(client.post('/inventory/dispatch', json={'sku': 'MOVE', 'locationId': 'shop', 'quantity': 1}))
    assert first['costOfGoodsSoldCents'] == 2
    assert preview['totalCostCents'] == second['costOfGoodsSoldCents'] == 1
    adjusted = ok(client.post('/api/cycle-counts/adjust', json={'sku': 'MOVE', 'location_id': 'shop', 'delta': -1,
        'expected_version': 3, 'reason_code': 'DAMAGE', 'reason': 'Broken item', 'mutation_id': 'count-loss'}))
    assert adjusted['inventoryValueChangeCents'] == -2 and adjusted['quantity'] == 0


async def test_draft_transfer_blocks_reinterpreting_a_legacy_product_base_unit(client):
    product(client)
    row = create(client)
    assert row['items'][0]['baseUnit'] == 'ea'
    assert client.post('/uom/configurations', json={'sku': 'MOVE', 'baseUnit': 'g', 'category': 'weight',
        'rules': [], 'expectedVersion': 0, 'mutationId': 'reinterpret'}).status_code == 409
