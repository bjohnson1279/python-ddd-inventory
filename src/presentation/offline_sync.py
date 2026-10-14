from fastapi import APIRouter, BackgroundTasks, Request, HTTPException
from pydantic import BaseModel
from src.infrastructure.auth.rbac import requires_roles
from typing import List
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/offline-sync", tags=["Offline Sync"])

class OfflineScanEvent(BaseModel):
    timestamp: str
    rfid_tag: str
    location_id: str
    operator_id: str
    scan_type: str # INBOUND, OUTBOUND, CYCLE_COUNT

class OfflineBufferPayload(BaseModel):
    device_id: str
    synced_at: str
    scans: List[OfflineScanEvent]

def process_bulk_scans_background(payload: OfflineBufferPayload):
    """Background task to process thousands of RFID/Barcode events."""
    logger.info(f"Processing {len(payload.scans)} offline scans from device {payload.device_id}...")
    for scan in payload.scans:
        # In a real system, this would translate the RFID to an SKU/Serial 
        # and execute the corresponding domain commands (e.g. adjust inventory)
        pass
    logger.info(f"Successfully processed {len(payload.scans)} scans.")

@router.post("/bulk")
@requires_roles(['ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR'])
async def sync_offline_buffer(payload: OfflineBufferPayload, request: Request, background_tasks: BackgroundTasks):
    """
    Endpoint for PWA / IoT devices to upload bulk scans collected during network dead zones.
    """
    # The legacy helper neither persists nor applies scans. Do not acknowledge
    # inventory work that has not actually been queued.
    raise HTTPException(501, 'Offline RFID ingestion is unavailable; use the durable cycle-count replay API')
