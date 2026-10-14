import pytest
from unittest.mock import patch
from sqlalchemy import select
from src.application.stock_service import StockService
from src.infrastructure.cost_models import CostLayerModel


def product(client, sku='COST-SKU'):
    response = client.post('/products', json={'sku': sku, 'name': 'Costed Item', 'price_amount': '99.00'})
    assert response.status_code == 201
    return response.json()['id']


def mutate(client, operation, quantity, mutation_id, **metadata):
    return client.post('/inventory/' + operation, json={'sku': 'COST-SKU', 'locationId': 'store',
        'quantity': quantity, 'mutationId': mutation_id, **metadata})


def config(client, method):
    result = client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1',
        'costingMethod': method, 'accountingMethod': 'accrual'})
    assert result.status_code == 200, result.text
    return result.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('method,cogs,remainder', [('fifo', 1400, 1600), ('LIFO', 2200, 800),
                                                 ('weighted_average_cost', 1800, 1200)])
async def test_costed_dispatch_and_valuation_conserve_value(client, method, cogs, remainder):
    variant = product(client)
    config(client, method)
    first = mutate(client, 'receive', 10, 'receipt-a', unitCostCents=100, lotNumber='A', receivedAt='2024-01-01T00:00:00Z')
    assert first.status_code == 200, first.text
    assert mutate(client, 'receive', 10, 'receipt-a', unitCostCents=100, lotNumber='A', receivedAt='2024-01-01T00:00:00Z').json() == first.json()
    assert mutate(client, 'receive', 10, 'receipt-a', unitCostCents=101, lotNumber='A').status_code == 409
    assert mutate(client, 'receive', 10, 'receipt-b', unitCostCents=200, lotNumber='B', receivedAt='2024-01-02T00:00:00Z').status_code == 200
    preview = client.get(f'/accounting/valuation/{variant}?quantity=12&locationId=store')
    assert preview.status_code == 200 and preview.json()['totalCostCents'] == cogs
    sale = mutate(client, 'dispatch', 12, 'sale')
    assert sale.status_code == 200, sale.text
    assert sale.json()['costOfGoodsSoldCents'] == cogs
    assert mutate(client, 'dispatch', 12, 'sale').json() == sale.json()
    report = client.get('/accounting/valuation-report?locationId=store').json()
    assert report[0]['totalQuantity'] == 8 and report[0]['totalValueCents'] == remainder
    layers = client.get('/accounting/cost-layers').json()
    assert sum(layer['remainingQuantity'] for layer in layers) == 8
    assert sum(layer['remainingValueCents'] for layer in layers) + cogs == 3000


@pytest.mark.asyncio
async def test_fefo_lot_eligibility_and_insufficient_lot_rollback(client):
    product(client)
    config(client, 'FEFO')
    mutate(client, 'receive', 3, 'late', unitCostCents=100, lotNumber='LATE', expirationDate='2035-01-01T00:00:00Z')
    mutate(client, 'receive', 2, 'early', unitCostCents=200, lotNumber='EARLY', expirationDate='2030-01-01T00:00:00Z')
    mutate(client, 'receive', 5, 'expired', unitCostCents=300, lotNumber='EXPIRED', expirationDate='2020-01-01T00:00:00Z')
    mutate(client, 'receive', 5, 'quarantine', unitCostCents=400, lotNumber='QUARANTINE', quarantined=True)
    assert mutate(client, 'dispatch', 3, 'bad-lot', lotNumber='EARLY').status_code == 400
    assert client.get('/inventory').json()[0]['quantity'] == 15
    sale = mutate(client, 'dispatch', 4, 'fefo-sale')
    assert sale.status_code == 200, sale.text
    assert [entry['lotNumber'] for entry in sale.json()['consumedLayers']] == ['EARLY', 'LATE']
    assert sale.json()['costOfGoodsSoldCents'] == 600
    assert mutate(client, 'dispatch', 2, 'oversell-eligible').status_code == 400
    # Expired/quarantined stock still has book value until an explicit writeoff.
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 3600


@pytest.mark.asyncio
async def test_unpriced_stock_can_be_priced_without_changing_quantity(client):
    product(client)
    receipt = mutate(client, 'receive', 3, 'unknown-cost')
    assert receipt.json()['costStatus'] == 'UNPRICED'
    assert client.get('/accounting/valuation-report').status_code == 409
    layer = client.get('/accounting/cost-layers').json()[0]
    payload = {'unitCostCents': 25, 'mutationId': 'price-opening'}
    price = client.post(f"/accounting/cost-layers/{layer['id']}/price", json=payload)
    assert price.status_code == 200, price.text
    assert client.post(f"/accounting/cost-layers/{layer['id']}/price", json=payload).json() == price.json()
    assert client.get('/inventory').json()[0]['quantity'] == 3
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 75
    assert mutate(client, 'dispatch', 1, 'sale').json()['costOfGoodsSoldCents'] == 25


@pytest.mark.asyncio
async def test_zero_cost_is_valid_and_method_change_with_stock_is_rejected(client):
    product(client)
    assert mutate(client, 'receive', 2, 'free', unitCostCents=0).json()['costStatus'] == 'PRICED'
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 0
    assert client.post('/accounting/tenant-config', json={'tenantId': 'tenant-1', 'costingMethod': 'WAC'}).status_code == 409
    for cost in (-1, 1.5, True):
        assert mutate(client, 'receive', 1, 'invalid', unitCostCents=cost).status_code == 422
    assert mutate(client, 'dispatch', 2, 'sell-free').json()['costOfGoodsSoldCents'] == 0
    assert config(client, 'WAC')['costingMethod'] == 'WAC'


@pytest.mark.asyncio
async def test_wac_after_new_receipt_and_rounding_to_zero_stock(client):
    product(client)
    config(client, 'WAC')
    mutate(client, 'receive', 1, 'a', unitCostCents=1)
    mutate(client, 'receive', 2, 'b', unitCostCents=2)
    first = mutate(client, 'dispatch', 1, 'first').json()['costOfGoodsSoldCents']
    assert first == 2
    mutate(client, 'receive', 1, 'c', unitCostCents=4)
    later = [mutate(client, 'dispatch', 1, 'sale-' + str(i)).json()['costOfGoodsSoldCents'] for i in range(3)]
    assert first + sum(later) == 9
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 0


@pytest.mark.asyncio
async def test_fulfillment_recognizes_cogs_only_once(client):
    product(client)
    mutate(client, 'receive', 5, 'a', unitCostCents=50)
    assert 'costOfGoodsSoldCents' not in mutate(client, 'allocate', 3, 'reserve').json()
    sale = mutate(client, 'fulfill-allocation', 3, 'fulfill')
    assert sale.json()['costOfGoodsSoldCents'] == 150
    assert mutate(client, 'fulfill-allocation', 3, 'fulfill').json() == sale.json()
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 100


@pytest.mark.asyncio
async def test_layer_consumption_is_rolled_back_with_outbox_failure(client, db_session):
    product(client)
    mutate(client, 'receive', 5, 'a', unitCostCents=50)
    service = StockService(db_session, 'tenant-1', 'test-user')
    with patch('src.application.stock_service.OutboxEventModel', side_effect=RuntimeError('injected failure')):
        with pytest.raises(RuntimeError, match='injected failure'):
            await service.mutate('dispatch', 'COST-SKU', 'store', 3, 'failed')
    assert client.get('/inventory').json()[0]['quantity'] == 5
    assert client.get('/accounting/cost-layers').json()[0]['remainingQuantity'] == 5
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 250
    assert mutate(client, 'dispatch', 3, 'failed').json()['costOfGoodsSoldCents'] == 150


@pytest.mark.asyncio
async def test_valuation_respects_locations_and_rejects_cross_tenant_config(client):
    variant = product(client)
    mutate(client, 'receive', 2, 'store', unitCostCents=100)
    client.post('/inventory/receive', json={'sku': 'COST-SKU', 'locationId': 'warehouse',
                'quantity': 3, 'unitCostCents': 200, 'mutationId': 'warehouse'})
    assert client.get('/accounting/valuation-report?locationId=store').json()[0]['totalValueCents'] == 200
    assert client.get('/accounting/valuation-report').json()[0]['totalValueCents'] == 800
    assert client.get(f'/accounting/valuation/{variant}?locationId=warehouse').json()['totalCostCents'] == 600
    assert client.get('/accounting/tenant-config/other').status_code == 403
    assert client.post('/accounting/tenant-config', json={'tenantId': 'other', 'costingMethod': 'FIFO'}).status_code == 403
    assert client.get('/accounting/valuation-report?method=unsupported').status_code == 422


@pytest.mark.asyncio
async def test_cost_layers_and_pricing_are_tenant_isolated(client):
    variant = product(client)
    receipt = mutate(client, 'receive', 2, 'receipt')
    layer_id = receipt.json()['costLayerId']
    client.post('/auth/setup', json={'tenantId': 'other', 'orgName': 'Other', 'adminName': 'Admin',
        'adminEmail': 'admin@example.com', 'adminPassword': 'test-password'})
    token = client.post('/auth/login', json={'tenantId': 'other', 'email': 'admin@example.com',
        'password': 'test-password'}).json()['token']
    other = {'Authorization': 'Bearer ' + token}
    assert client.get('/accounting/cost-layers', headers=other).json() == []
    assert client.get('/accounting/valuation-report', headers=other).json() == []
    assert client.get(f'/accounting/valuation/{variant}', headers=other).status_code == 404
    assert client.post(f'/accounting/cost-layers/{layer_id}/price', headers=other,
        json={'unitCostCents': 100, 'mutationId': 'steal-layer'}).status_code == 404
    assert client.get('/accounting/cost-layers').json()[0]['unitCostCents'] is None
