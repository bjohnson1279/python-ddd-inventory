import pytest
from httpx import AsyncClient, ASGITransport
import pytest_asyncio
from src.presentation.main import app

@pytest_asyncio.fixture
async def async_client(client): # Depends on conftest 'client' fixture which sets up db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test') as ac:
        yield ac

@pytest.mark.asyncio
async def test_create_plan(async_client):
    response = await async_client.post("/api/cycle-counts/plans", json={
        "tenant_id": "tenant-1",
        "name": "Daily A-Items",
        "abc_classification": "A",
        "frequency_days": 30
    })
    assert response.status_code == 200
    assert response.json()["name"] == "Daily A-Items"

@pytest.mark.asyncio
async def test_schedule_audits(async_client):
    await async_client.post("/api/cycle-counts/plans", json={
        "tenant_id": "tenant-1",
        "name": "Daily A-Items",
        "abc_classification": "A",
        "frequency_days": 30
    })
    response = await async_client.post("/api/cycle-counts/schedule", json={
        "tenant_id": "tenant-1"
    })
    assert response.status_code == 200
    assert response.json()["scheduled"] == 1

@pytest.mark.asyncio
async def test_get_assigned_counts(async_client):
    # Check endpoint resolves and handles line items correctly
    response = await async_client.get("/api/cycle-counts/assigned")
    assert response.status_code == 200
    res_json = response.json()
    assert isinstance(res_json, list)
    for record_dto in res_json:
        assert "record_id" in record_dto
        assert "items" in record_dto
        assert isinstance(record_dto["items"], list)

@pytest.mark.asyncio
async def test_submit_count_matched(async_client):
    # Test submission where variance is 0
    response = await async_client.post("/api/cycle-counts/submit", json={
        "record_id": "rec-123",
        "items": [
            {"sku": "SKU-1", "counted_quantity": 10} # expected is mocked to 10 in our router scaffold
        ]
    })
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["status"] == "COMPLETED"

@pytest.mark.asyncio
async def test_submit_count_variance_flagged(async_client):
    # Test submission where variance is > threshold (expected is 10)
    response = await async_client.post("/api/cycle-counts/submit", json={
        "record_id": "rec-456",
        "items": [
            {"sku": "SKU-2", "counted_quantity": 5} # 50% variance, threshold is 5%
        ]
    })
    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["status"] == "RECOUNT_REQUIRED"

@pytest.mark.asyncio
async def test_offline_sync(async_client):
    # Test batch sync
    response = await async_client.post("/api/cycle-counts/sync", json=[
        {
            "record_id": "rec-sync-1",
            "items": [{"sku": "SKU-3", "counted_quantity": 10}]
        },
        {
            "record_id": "rec-sync-2",
            "items": [{"sku": "SKU-4", "counted_quantity": 12}]
        }
    ])
    assert response.status_code == 200
    assert len(response.json()["results"]) == 2
    assert response.json()["results"][0]["synced"] is True
