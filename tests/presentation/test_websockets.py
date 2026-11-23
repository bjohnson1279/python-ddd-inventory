import json
from types import SimpleNamespace
import pytest
from starlette.websockets import WebSocketDisconnect

from src.infrastructure.auth.identity import issue_token
from src.presentation.websockets import ConnectionManager, _extract_ws_claims, manager


class _FakeWebSocket:
    def __init__(self, headers=None, query_params=None, fail_send=False):
        self.headers = headers or {}
        self.query_params = query_params or {}
        self.accepted = False
        self.closed_code = None
        self.sent = []
        self.fail_send = fail_send

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000):
        self.closed_code = code

    async def send_text(self, message: str):
        if self.fail_send:
            raise RuntimeError("socket closed")
        self.sent.append(message)


def _token(tenant_id="tenant-ws", roles=None):
    role = roles[0] if roles else "ADMIN"
    user = SimpleNamespace(id="user-1", tenant_id=tenant_id, role=role, auth_version=1)
    return issue_token(user, session_id="sess-1")


@pytest.mark.asyncio
async def test_connection_manager_connect_broadcast_disconnect_and_error_isolation():
    cm = ConnectionManager()
    ws_ok = _FakeWebSocket()
    ws_bad = _FakeWebSocket(fail_send=True)

    await cm.connect(ws_ok, "t1")
    await cm.connect(ws_bad, "t1")
    assert ws_ok.accepted is True
    assert len(cm.active_connections["t1"]) == 2

    await cm.broadcast_to_tenant("t1", {"event": "STOCK_RECEIVED", "qty": 5})
    assert ws_ok.sent == [json.dumps({"event": "STOCK_RECEIVED", "qty": 5})]

    # Broadcast to unknown tenant is a safe no-op
    await cm.broadcast_to_tenant("unknown", {"event": "NONE"})

    cm.disconnect(ws_ok, "t1")
    assert len(cm.active_connections["t1"]) == 1
    cm.disconnect(ws_bad, "t1")
    assert "t1" not in cm.active_connections

    # Disconnecting an unknown socket is a safe no-op
    cm.disconnect(ws_ok, "t1")


def test_extract_ws_claims_header_query_and_invalid():
    tok = _token("tenant-a", ["MANAGER"])
    ws_hdr = _FakeWebSocket(headers={"authorization": f"Bearer {tok}"})
    claims = _extract_ws_claims(ws_hdr)
    assert claims is not None
    assert claims["tenantId"] == "tenant-a"
    assert claims["roles"] == ["MANAGER"]

    ws_qp = _FakeWebSocket(query_params={"token": tok})
    assert _extract_ws_claims(ws_qp)["tenantId"] == "tenant-a"

    assert _extract_ws_claims(_FakeWebSocket()) is None
    assert _extract_ws_claims(_FakeWebSocket(headers={"authorization": "Basic abc"})) is None
    assert _extract_ws_claims(_FakeWebSocket(headers={"authorization": "Bearer invalid.jwt.token"})) is None


def test_websocket_endpoint_rejects_unauthenticated_and_tenant_mismatch(client):
    # Missing token -> closed with 1008
    with client.websocket_connect("/ws/tenant-ws") as ws:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 1008

    # Tenant mismatch -> closed with 1008
    wrong_tenant_tok = _token("other-tenant", ["ADMIN"])
    with client.websocket_connect(f"/ws/tenant-ws?token={wrong_tenant_tok}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 1008

    # Forbidden role -> closed with 1008
    bad_role_tok = _token("tenant-ws", ["SUPPLIER_USER"])
    with client.websocket_connect(f"/ws/tenant-ws?token={bad_role_tok}") as ws:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            ws.receive_text()
        assert exc_info.value.code == 1008


def test_websocket_endpoint_authenticated_connect_and_disconnect(client):
    tok = _token("tenant-ws", ["ADMIN"])
    with client.websocket_connect("/ws/tenant-ws", headers={"Authorization": f"Bearer {tok}"}) as ws:
        assert "tenant-ws" in manager.active_connections
        ws.send_text("ping")
    assert "tenant-ws" not in manager.active_connections
