import pytest
import jwt
from datetime import datetime, timedelta, timezone
from src.infrastructure.auth.identity import jwt_secret


def register(client, tenant):
    response = client.post('/api/auth/setup', headers={'Authorization': ''}, json={
        'tenantId': tenant, 'orgName': tenant, 'adminName': 'Admin',
        'adminEmail': 'admin@example.com', 'adminPassword': 'test-password',
    })
    assert response.status_code == 201, response.text
    response = client.post('/api/auth/login', headers={'Authorization': ''}, json={
        'tenantId': tenant, 'email': 'ADMIN@example.com', 'password': 'test-password',
    })
    assert response.status_code == 200
    return {'Authorization': 'Bearer ' + response.json()['token']}


@pytest.mark.asyncio
async def test_tenant_catalog_isolation_and_duplicate_sku(client):
    other = register(client, 'tenant-2')
    item = {'sku': 'SAME-SKU', 'name': 'Store One', 'price_amount': '12.50'}
    first = client.post('/products', json=item)
    assert first.status_code == 201
    second = client.post('/products', headers=other, json={**item, 'name': 'Store Two'})
    assert second.status_code == 201
    assert client.post('/products', json=item).status_code == 400
    assert client.get('/products/' + first.json()['id'], headers=other).status_code == 404
    assert [p['name'] for p in client.get('/products').json()] == ['Store One']
    assert [p['name'] for p in client.get('/products', headers=other).json()] == ['Store Two']
    assert client.get('/products', headers={**other, 'X-Tenant-ID': 'tenant-1'}).status_code == 403
    assert client.get('/products?tenantId=tenant-1', headers=other).status_code == 403


@pytest.mark.asyncio
async def test_authentication_failures_and_setup_cannot_replace_admin(client):
    assert client.get('/products', headers={'Authorization': ''}).status_code == 401
    assert client.get('/products', headers={'Authorization': 'Bearer garbage'}).status_code == 401
    assert client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': 'admin@example.com',
                                           'password': 'wrong-password'}).status_code == 401
    assert client.post('/auth/setup', json={'tenantId': 'tenant-1', 'orgName': 'Hijack',
        'adminName': 'Attacker', 'adminEmail': 'attacker@example.com',
        'adminPassword': 'test-password'}).status_code == 409
    now = datetime.now(timezone.utc)
    expired = jwt.encode({'sub': 'user', 'tenantId': 'tenant-1', 'roles': ['ADMIN'],
        'iat': now - timedelta(hours=2), 'exp': now - timedelta(hours=1),
        'iss': 'python-ddd-inventory', 'aud': 'inventory-client'}, jwt_secret(), algorithm='HS256')
    assert client.get('/products', headers={'Authorization': 'Bearer ' + expired}).status_code == 401


@pytest.mark.asyncio
async def test_reader_cannot_create_catalog_items(client):
    assert client.post('/users', json={'email': 'reader@example.com', 'role': 'USER',
                                      'password': 'test-password'}).status_code == 201
    login = client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': 'reader@example.com',
                                           'password': 'test-password'})
    headers = {'Authorization': 'Bearer ' + login.json()['token']}
    assert client.get('/products', headers=headers).status_code == 200
    assert client.post('/products', headers=headers, json={
        'sku': 'NEW-SKU', 'name': 'Unauthorized', 'price_amount': '1.00'}).status_code == 403
