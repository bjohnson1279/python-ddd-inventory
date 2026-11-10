import pytest
from unittest.mock import AsyncMock
from fastapi import FastAPI
from starlette.testclient import TestClient
from src.presentation.websockets import ConnectionManager, router, manager
from src.infrastructure.auth.rbac import get_current_user_roles

@pytest.mark.asyncio
async def test_connection_manager_connect():
    cm = ConnectionManager()
    ws = AsyncMock()
    await cm.connect(ws, "tenant-1")
    ws.accept.assert_awaited_once()
    assert "tenant-1" in cm.active_connections
    assert ws in cm.active_connections["tenant-1"]

@pytest.mark.asyncio
async def test_connection_manager_disconnect():
    cm = ConnectionManager()
    ws = AsyncMock()
    await cm.connect(ws, "tenant-1")
    cm.disconnect(ws, "tenant-1")
    assert ws not in cm.active_connections["tenant-1"]
    # Disconnecting unknown tenant does not raise error
    cm.disconnect(ws, "tenant-unknown")

@pytest.mark.asyncio
async def test_connection_manager_broadcast_to_tenant():
    cm = ConnectionManager()
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    await cm.connect(ws1, "tenant-1")
    await cm.connect(ws2, "tenant-1")

    payload = {"type": "stock_updated", "sku": "SKU-1"}
    await cm.broadcast_to_tenant("tenant-1", payload)

    expected_msg = '{"type": "stock_updated", "sku": "SKU-1"}'
    ws1.send_text.assert_awaited_once_with(expected_msg)
    ws2.send_text.assert_awaited_once_with(expected_msg)

@pytest.mark.asyncio
async def test_connection_manager_broadcast_handles_send_error():
    cm = ConnectionManager()
    ws_failing = AsyncMock()
    ws_failing.send_text.side_effect = Exception("Connection lost")
    ws_ok = AsyncMock()
    await cm.connect(ws_failing, "tenant-1")
    await cm.connect(ws_ok, "tenant-1")

    # Should not raise exception
    await cm.broadcast_to_tenant("tenant-1", {"event": "test"})
    ws_failing.send_text.assert_awaited_once()
    ws_ok.send_text.assert_awaited_once()

@pytest.mark.asyncio
async def test_connection_manager_broadcast_empty_or_missing_tenant():
    cm = ConnectionManager()
    # No connections for tenant, should not raise
    await cm.broadcast_to_tenant("tenant-nonexistent", {"event": "test"})
    cm.active_connections["tenant-empty"] = []
    await cm.broadcast_to_tenant("tenant-empty", {"event": "test"})

def test_websocket_endpoint_unauthorized_empty_roles():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_roles] = lambda: []

    client = TestClient(app)
    with client.websocket_connect("/ws/tenant-1") as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close"
        assert msg["code"] == 1008

def test_websocket_endpoint_unauthorized_invalid_role():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_roles] = lambda: ["GUEST"]

    client = TestClient(app)
    with client.websocket_connect("/ws/tenant-1") as ws:
        msg = ws.receive()
        assert msg["type"] == "websocket.close"
        assert msg["code"] == 1008

def test_websocket_endpoint_authorized_admin():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_roles] = lambda: ["ADMIN"]

    client = TestClient(app)
    with client.websocket_connect("/ws/tenant-test") as ws:
        assert "tenant-test" in manager.active_connections
        assert len(manager.active_connections["tenant-test"]) == 1
        ws.send_text("ping")
    assert len(manager.active_connections["tenant-test"]) == 0

def test_websocket_endpoint_authorized_user():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_roles] = lambda: ["USER"]

    client = TestClient(app)
    with client.websocket_connect("/ws/tenant-user") as ws:
        assert "tenant-user" in manager.active_connections
        assert len(manager.active_connections["tenant-user"]) == 1
    assert len(manager.active_connections["tenant-user"]) == 0
