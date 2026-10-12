import pytest
from unittest.mock import patch
from src.application.stock_service import StockService


def create_product(client):
    assert client.post('/products', json={'sku': 'STOCK-SKU', 'name': 'Item',
                                        'price_amount': '10.00'}).status_code == 201


def change(client, operation, amount, key, **kwargs):
    return client.post('/api/inventory/' + operation, json={
        'sku': 'STOCK-SKU', 'amount': amount, 'locationId': 'store',
        'mutationId': key, **kwargs,
    })


@pytest.mark.asyncio
async def test_stock_reservation_lifecycle_and_replay(client):
    create_product(client)
    first = change(client, 'receive', 10, 'receipt')
    assert first.status_code == 200
    assert change(client, 'receive', 10, 'receipt').json() == first.json()
    assert change(client, 'receive', 11, 'receipt').status_code == 409
    reserved = change(client, 'allocate', 7, 'reserve')
    assert reserved.json()['available'] == 3
    assert change(client, 'dispatch', 4, 'oversell').status_code == 400
    assert change(client, 'dispatch', 2, 'sale').json()['quantity'] == 8
    assert change(client, 'fulfill-allocation', 5, 'fulfill').json()['quantity'] == 3
    assert change(client, 'release-allocation', 2, 'cancel').json()['allocated'] == 0
    stock = client.get('/inventory').json()
    assert len(stock) == 1 and stock[0]['quantity'] == 3 and stock[0]['version'] == 5
    movements = client.get('/inventory/movements').json()
    assert len(movements) == 5
    assert all(m['actorId'] and m['locationId'] == 'store' for m in movements)


@pytest.mark.asyncio
async def test_stock_validation_and_stale_version(client):
    create_product(client)
    assert change(client, 'receive', 1, 'initial', expectedVersion=0).status_code == 200
    assert change(client, 'dispatch', 1, 'stale', expectedVersion=0).status_code == 409
    for amount in (0, -1, 1.5, True):
        assert change(client, 'receive', amount, 'invalid').status_code == 422
    assert change(client, 'dispatch', 1, 'sale', expectedVersion=1).status_code == 200
    assert change(client, 'dispatch', 1, 'second-sale').status_code == 400
    assert change(client, 'receive', 1, 'wrong-tenant', tenantId='other').status_code == 403
    assert client.get('/inventory', headers={'Authorization': ''}).status_code == 401


@pytest.mark.asyncio
async def test_stock_write_rolls_back_if_outbox_creation_fails(client, db_session):
    create_product(client)
    assert change(client, 'receive', 10, 'receipt').status_code == 200
    service = StockService(db_session, 'tenant-1', 'test-operator')
    with patch('src.application.stock_service.OutboxEventModel', side_effect=RuntimeError('injected failure')):
        with pytest.raises(RuntimeError, match='injected failure'):
            await service.mutate('dispatch', 'STOCK-SKU', 'store', 3, 'failed-sale')
    assert client.get('/inventory').json()[0]['quantity'] == 10
    assert len(client.get('/inventory/movements').json()) == 1
    assert change(client, 'dispatch', 3, 'failed-sale').json()['quantity'] == 7


@pytest.mark.asyncio
async def test_stock_is_scoped_to_authenticated_tenant(client):
    create_product(client)
    assert change(client, 'receive', 10, 'receipt').status_code == 200
    assert client.post('/auth/setup', json={'tenantId': 'other', 'orgName': 'Other',
        'adminName': 'Admin', 'adminEmail': 'admin@example.com',
        'adminPassword': 'test-password'}).status_code == 201
    login = client.post('/auth/login', json={'tenantId': 'other', 'email': 'admin@example.com',
                                           'password': 'test-password'})
    headers = {'Authorization': 'Bearer ' + login.json()['token']}
    assert client.get('/inventory', headers=headers).json() == []
    assert client.get('/inventory/movements', headers=headers).json() == []
    assert client.post('/inventory/dispatch', headers=headers, json={
        'sku': 'STOCK-SKU', 'quantity': 1, 'locationId': 'store'}).status_code == 400
