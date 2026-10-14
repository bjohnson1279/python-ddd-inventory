import pytest

def test_workforce_put_is_allowed_by_browser_preflight(client):
    response = client.options('/api/workforce/profiles/operator', headers={
        'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'PUT',
        'Access-Control-Request-Headers': 'Authorization,Content-Type'})
    assert response.status_code == 200
    assert 'PUT' in response.headers['access-control-allow-methods']


@pytest.mark.asyncio
async def test_analyze_owned_sku_aging(client):
    client.post('/products', json={'sku': 'AGING-SKU', 'name': 'Item', 'price_amount': '99.00'})
    client.post('/inventory/receive', json={'sku': 'AGING-SKU', 'locationId': 'store', 'quantity': 2, 'unitCostCents': 100})
    response = client.get('/api/aging/AGING-SKU?location_id=store&tenant_id=tenant-1')
    assert response.status_code == 200
    assert response.json()['sku'] == 'AGING-SKU'
    assert response.json()['locked_capital_cents'] == 200
    assert client.get('/api/aging/AGING-SKU?location_id=store&tenant_id=other').status_code == 403
    assert client.get('/api/aging/missing?location_id=store').status_code == 404
    assert client.get('/api/aging/AGING-SKU?location_id=store', headers={'Authorization': ''}).status_code == 401
