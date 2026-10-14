import pytest
from httpx import AsyncClient, ASGITransport
import pytest_asyncio
from src.presentation.main import app
from src.infrastructure.cycle_count.models import CycleCountRecordModel, CycleCountLineItemModel


@pytest_asyncio.fixture(autouse=True)
async def seeded_records(db_session):
    for record_id, sku in [('rec-123', 'SKU-1'), ('rec-456', 'SKU-2'),
                           ('rec-sync-1', 'SKU-3'), ('rec-sync-2', 'SKU-4')]:
        db_session.add(CycleCountRecordModel(id=record_id, tenant_id='tenant-1', name='Count',
                                            status='PENDING', abc_classification='A'))
        db_session.add(CycleCountLineItemModel(record_id=record_id, sku=sku, expected_quantity=10))
    await db_session.commit()

@pytest_asyncio.fixture
async def async_client(client): # Depends on conftest 'client' fixture which sets up db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url='http://test', headers=dict(client.headers)) as ac:
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
    await async_client.post('/products', json={'sku': 'SCHEDULE', 'name': 'Item', 'price_amount': '1.00'})
    await async_client.post('/inventory/receive', json={'sku': 'SCHEDULE', 'quantity': 10})
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
    repeated = await async_client.post('/api/cycle-counts/schedule', json={'tenant_id': 'tenant-1'})
    assert repeated.json()['scheduled'] == 0
    assert response.json()['audits'][0]['items'][0]['expected_quantity'] == 10

@pytest.mark.asyncio
async def test_get_assigned_counts(async_client):
    # Just checking the endpoint resolves properly without failure
    response = await async_client.get("/api/cycle-counts/assigned")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

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
    assert response.json()["status"] == "SUBMITTED"
    assert response.json()['requiresReview'] is True

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
    assert response.json()["success"] is True
    assert response.json()["status"] == "SUBMITTED"
    assert response.json()['varianceFlagged'] is True

@pytest.mark.asyncio
async def test_offline_sync(async_client):
    # Test batch sync
    response = await async_client.post("/api/cycle-counts/sync", json=[
        {
            "record_id": "rec-sync-1",
            "revision": 0, "mutation_id": "offline-1",
            "items": [{"sku": "SKU-3", "counted_quantity": 10}]
        },
        {
            "record_id": "rec-sync-2",
            "revision": 0, "mutation_id": "offline-2",
            "items": [{"sku": "SKU-4", "counted_quantity": 12}]
        }
    ])
    assert response.status_code == 200
    assert len(response.json()['results']) == 2
