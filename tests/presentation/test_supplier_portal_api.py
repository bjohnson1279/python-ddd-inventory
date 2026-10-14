import pytest
from httpx import AsyncClient, ASGITransport
import pytest_asyncio
from src.presentation.main import app
from datetime import datetime, timedelta

@pytest_asyncio.fixture
async def async_client(): 
    # Notice we don't mock the DB or auth closely here, just asserting routing & payload structure
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as ac:
        yield ac

@pytest.mark.asyncio
async def test_list_supplier_pos(async_client):
    response = await async_client.get("/api/supplier-portal/pos")
    
    # Expect 401 Unauthorized because test client does not have SUPPLIER_USER role automatically in this stub
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_supplier_asn_persists_order_and_notice_once(client, db_session):
    from tests.presentation.test_procurement_api import setup, sent, ok
    from src.infrastructure.procurement_models import ASNModel, PurchaseOrderModel
    from sqlalchemy import select, func
    supplier, order = setup(client)
    sent(client, order)
    user = ok(client.post('/users', json={'email': 'supplier-merge@example.com', 'role': 'SUPPLIER_USER',
        'supplierId': supplier['id'], 'password': 'test-password'}), 201)
    token = ok(client.post('/auth/login', json={'tenantId': 'tenant-1', 'email': user['email'], 'password': 'test-password'}))['token']
    headers = {'Authorization': 'Bearer ' + token}
    ok(client.post('/api/supplier-portal/pos/'+order['id']+'/acknowledge', headers=headers,
        json={'expected_delivery_date': '2030-01-01T00:00:00Z', 'mutationId': 'ack-merge'}))
    body = {'po_id': order['id'], 'tracking_number': 'TRACK', 'estimated_delivery_date': '2030-01-01T00:00:00Z',
        'items': [{'sku': 'A', 'shipped_quantity': 10}], 'mutationId': 'asn-merge'}
    first = ok(client.post('/api/supplier-portal/asns', headers=headers, json=body))
    assert ok(client.post('/api/supplier-portal/asns', headers=headers, json=body)) == first
    assert await db_session.scalar(select(func.count()).select_from(ASNModel)) == 1
    row = await db_session.get(PurchaseOrderModel, order['id'])
    assert row.status == 'SHIPPED'

@pytest.mark.asyncio
async def test_acknowledge_po_no_auth(async_client):
    response = await async_client.post(
        "/api/supplier-portal/pos/po-123/acknowledge", 
        json={"expected_delivery_date": datetime.utcnow().isoformat()}
    )
    assert response.status_code == 401

@pytest.mark.asyncio
async def test_submit_asn_no_auth(async_client):
    response = await async_client.post(
        "/api/supplier-portal/asns", 
        json={
            "po_id": "po-123",
            "tracking_number": "TRK",
            "estimated_delivery_date": datetime.utcnow().isoformat(),
            "items": [{"sku": "SKU1", "shipped_quantity": 100}]
        }
    )
    assert response.status_code == 401
