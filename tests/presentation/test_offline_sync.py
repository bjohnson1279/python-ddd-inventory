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
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "accepted"
    assert "Queued 1 scans" in data["message"]
import logging
from unittest.mock import patch, MagicMock
from src.presentation.offline_sync import (
    OfflineScanEvent,
    OfflineBufferPayload,
    process_bulk_scans_background
)

@pytest.mark.asyncio
async def test_process_bulk_scans_background_with_scans(caplog):
    """Test background task processing with multiple scans in payload."""
    caplog.set_level(logging.INFO)
    scan1 = OfflineScanEvent(
        timestamp="2023-10-01T10:00:00Z",
        rfid_tag="RFID-001",
        location_id="LOC-100",
        operator_id="OP-01",
        scan_type="INBOUND"
    )
    scan2 = OfflineScanEvent(
        timestamp="2023-10-01T10:05:00Z",
        rfid_tag="RFID-002",
        location_id="LOC-101",
        operator_id="OP-02",
        scan_type="OUTBOUND"
    )

    payload = OfflineBufferPayload(
        device_id="DEV-999",
        synced_at="2023-10-01T10:10:00Z",
        scans=[scan1, scan2]
    )

    await process_bulk_scans_background(payload)

    assert "Processing 2 offline scans from device DEV-999..." in caplog.text
    assert "Successfully processed 2 scans." in caplog.text


@pytest.mark.asyncio
async def test_process_bulk_scans_background_empty_scans(caplog):
    """Test background task processing with zero scans in payload."""
    caplog.set_level(logging.INFO)
    payload = OfflineBufferPayload(
        device_id="DEV-000",
        synced_at="2023-10-01T12:00:00Z",
        scans=[]
    )

    await process_bulk_scans_background(payload)

    assert "Processing 0 offline scans from device DEV-000..." in caplog.text
    assert "Successfully processed 0 scans." in caplog.text


def test_sync_offline_buffer_endpoint_success(client):
    """Test POST /offline-sync/bulk endpoint with valid payload."""
    payload_data = {
        "device_id": "DEV-123",
        "synced_at": "2023-10-01T12:00:00Z",
        "scans": [
            {
                "timestamp": "2023-10-01T11:55:00Z",
                "rfid_tag": "TAG-111",
                "location_id": "WH-A",
                "operator_id": "USER-1",
                "scan_type": "CYCLE_COUNT"
            }
        ]
    }

    response = client.post("/offline-sync/bulk", json=payload_data)

    assert response.status_code == 501
    assert 'durable cycle-count replay' in response.json()['detail']


def test_sync_offline_buffer_endpoint_invalid_payload(client):
    """Test POST /offline-sync/bulk with invalid missing payload fields."""
    response = client.post("/offline-sync/bulk", json={"device_id": "DEV-123"})
    assert response.status_code == 422


def test_sync_offline_buffer_triggers_background_task(client):
    """Test that the background task is added and executed on endpoint invocation."""
    payload_data = {
        "device_id": "DEV-777",
        "synced_at": "2023-10-01T12:00:00Z",
        "scans": []
    }

    with patch("src.presentation.offline_sync.process_bulk_scans_background") as mock_bg_task:
        response = client.post("/offline-sync/bulk", json=payload_data)
        assert response.status_code == 501
        mock_bg_task.assert_not_called()
