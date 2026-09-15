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
