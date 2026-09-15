import pytest
from httpx import AsyncClient, ASGITransport
import pytest_asyncio
from src.presentation.main import app

@pytest_asyncio.fixture
async def async_client(client): # Ensure client override runs
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as ac:
        yield ac

@pytest.mark.asyncio
async def test_create_product_api_success(async_client):
    response = await async_client.post('/products', json={
        'sku': 'TEST-SKU',
        'name': 'Test Item',
        'price_amount': '20.50'
    })
    assert response.status_code == 201
    data = response.json()
    assert data['sku'] == 'TEST-SKU'
    assert data['price_amount'] == '20.50'
    assert 'id' in data

@pytest.mark.asyncio
async def test_create_product_api_bad_sku(async_client):
    response = await async_client.post('/products', json={
        'sku': 'invalid sku!!',
        'name': 'Test Item',
        'price_amount': '20.50'
    })
    assert response.status_code == 400

@pytest.mark.asyncio
async def test_get_product_not_found_api(async_client):
    response = await async_client.get('/products/non-existent-id')
    assert response.status_code == 404

