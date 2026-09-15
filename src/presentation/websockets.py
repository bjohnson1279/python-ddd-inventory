from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict
import logging
import json

logger = logging.getLogger(__name__)

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        # Maps tenant_id -> list of active connections
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, tenant_id: str):
        await websocket.accept()
        if tenant_id not in self.active_connections:
            self.active_connections[tenant_id] = []
        self.active_connections[tenant_id].append(websocket)
        logger.info(f"WebSocket connected for tenant {tenant_id}")

    def disconnect(self, websocket: WebSocket, tenant_id: str):
        if tenant_id in self.active_connections:
            self.active_connections[tenant_id].remove(websocket)
            logger.info(f"WebSocket disconnected for tenant {tenant_id}")

    async def broadcast_to_tenant(self, tenant_id: str, message: dict):
        if tenant_id in self.active_connections:
            for connection in self.active_connections[tenant_id]:
                try:
                    await connection.send_text(json.dumps(message))
                except Exception as e:
                    logger.error(f"Failed to send websocket message: {e}")

manager = ConnectionManager()

@router.websocket("/ws/{tenant_id}")
async def websocket_endpoint(websocket: WebSocket, tenant_id: str):
    """
    WebSocket endpoint for Real-Time Collaborative UI sync.
    Broadcasts stock changes, discrepancy updates, and webhook delivery issues.
    """
    await manager.connect(websocket, tenant_id)
    try:
        while True:
            # Keep the connection open and listen for client messages if needed
            data = await websocket.receive_text()
            # For now, we only push from server to client
    except WebSocketDisconnect:
        manager.disconnect(websocket, tenant_id)
