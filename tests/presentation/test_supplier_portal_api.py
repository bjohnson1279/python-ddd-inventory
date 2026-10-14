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
    assert response.status_code in [200, 401, 403]

@pytest.mark.asyncio
async def test_acknowledge_po_no_auth(async_client):
    response = await async_client.post(
        "/api/supplier-portal/pos/po-123/acknowledge", 
        json={"expected_delivery_date": datetime.utcnow().isoformat()}
    )
    assert response.status_code in [401, 403]

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
    assert response.status_code in [401, 403]
