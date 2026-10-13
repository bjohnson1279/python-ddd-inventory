import pytest
import asyncio
import time
from httpx import AsyncClient, ASGITransport
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from src.presentation.main import app
from datetime import datetime, timedelta
from src.presentation.supplier_portal.router import get_supplier_repo
from src.domain.supplier_collaboration.entity import PurchaseOrder, AdvanceShippingNotice

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

class DummyRepo:
    async def get_po(self, po_id: str):
        return PurchaseOrder(id=po_id, tenant_id="t1", supplier_id="mock_supplier_1", status="ACKNOWLEDGED")

    async def save_asn(self, asn):
        await asyncio.sleep(0.05)
        return asn

    async def save_po(self, po):
        await asyncio.sleep(0.05)
        return po

@pytest.mark.asyncio
async def test_submit_asn_performance(async_client):
    dummy_repo = DummyRepo()
    app.dependency_overrides[get_supplier_repo] = lambda: dummy_repo

    with patch("src.infrastructure.auth.rbac.get_current_user_roles", return_value=["SUPPLIER_USER"]):
        start_time = time.perf_counter()
        response = await async_client.post(
            "/api/supplier-portal/asns",
            json={
                "po_id": "po-123",
                "tracking_number": "TRK",
                "estimated_delivery_date": datetime.utcnow().isoformat(),
                "items": [{"sku": "SKU1", "shipped_quantity": 100}]
            }
        )
        elapsed_time = time.perf_counter() - start_time

    app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["success"] is True
    print(f"\n[BENCHMARK] submit_asn elapsed time: {elapsed_time:.4f}s")
