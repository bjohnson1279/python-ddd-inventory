import pytest
from src.presentation.offline_sync import OfflineBufferPayload, OfflineScanEvent, process_bulk_scans_background

@pytest.mark.asyncio
async def test_process_bulk_scans_background():
    scans = [
        OfflineScanEvent(
            timestamp="2025-01-01T12:00:00Z",
            rfid_tag=f"TAG_{i}",
            location_id="LOC_1",
            operator_id="OP_1",
            scan_type="INBOUND"
        )
        for i in range(250)
    ]
    payload = OfflineBufferPayload(
        device_id="DEV_1",
        synced_at="2025-01-01T12:00:00Z",
        scans=scans
    )

    await process_bulk_scans_background(payload, batch_size=50)

def test_sync_offline_buffer_endpoint(client):
    payload = {
        "device_id": "DEV_123",
        "synced_at": "2025-01-01T12:00:00Z",
        "scans": [
            {
                "timestamp": "2025-01-01T12:00:00Z",
                "rfid_tag": "TAG_ABC",
                "location_id": "LOC_1",
                "operator_id": "OP_1",
                "scan_type": "INBOUND"
            }
        ]
    }
    response = client.post("/offline-sync/bulk", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "accepted"
    assert "Queued 1 scans" in data["message"]
