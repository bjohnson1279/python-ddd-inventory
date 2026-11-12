import os
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta, timezone
import uuid
import pytest
from fastapi.testclient import TestClient
import jwt

from src.presentation.main import app, build_event_delivery, shutdown_event
from src.infrastructure.auth.identity import jwt_secret


@pytest.fixture(autouse=True)
def reset_app_state():
    # Save original state
    orig_enabled = getattr(app.state, "outbox_enabled", False)
    orig_worker = getattr(app.state, "outbox_worker", None)
    app.state.outbox_enabled = False
    app.state.outbox_worker = None
    yield
    app.state.outbox_enabled = orig_enabled
    app.state.outbox_worker = orig_worker


@pytest.fixture
def test_client():
    return TestClient(app)


def make_test_token(tenant_id="tenant-xyz", user_id="user-123", roles=None, ver=1, permissions=None):
    now = datetime.now(timezone.utc)
    payload = {
        'sub': user_id,
        'tenantId': tenant_id,
        'roles': roles if roles is not None else ['ADMIN'],
        'jti': uuid.uuid4().hex,
        'ver': ver,
        'permissions': permissions if permissions is not None else [],
        'iat': now,
        'exp': now + timedelta(hours=1),
        'iss': 'python-ddd-inventory',
        'aud': 'inventory-client',
    }
    return jwt.encode(payload, jwt_secret(), algorithm='HS256')


def test_health_check_outbox_disabled(test_client):
    app.state.outbox_enabled = False
    app.state.outbox_worker = None
    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["eventDelivery"]["enabled"] is False
    assert data["eventDelivery"]["running"] is False
    assert data["eventDelivery"]["pollingError"] is None


def test_health_check_outbox_enabled_and_healthy(test_client):
    app.state.outbox_enabled = True
    worker = MagicMock()
    worker.running = True
    worker.last_poll_error = None
    app.state.outbox_worker = worker

    response = test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["eventDelivery"]["enabled"] is True
    assert data["eventDelivery"]["running"] is True
    assert data["eventDelivery"]["pollingError"] is None


def test_health_check_outbox_enabled_and_worker_stopped(test_client):
    app.state.outbox_enabled = True
    worker = MagicMock()
    worker.running = False
    worker.last_poll_error = None
    app.state.outbox_worker = worker

    response = test_client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["eventDelivery"]["enabled"] is True
    assert data["eventDelivery"]["running"] is False


def test_health_check_outbox_enabled_with_poll_error(test_client):
    app.state.outbox_enabled = True
    worker = MagicMock()
    worker.running = True
    worker.last_poll_error = "Connection lost to kafka broker"
    app.state.outbox_worker = worker

    response = test_client.get("/health")
    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "degraded"
    assert data["eventDelivery"]["enabled"] is True
    assert data["eventDelivery"]["running"] is True
    assert data["eventDelivery"]["pollingError"] == "Connection lost to kafka broker"


def test_build_event_delivery(monkeypatch):
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    monkeypatch.setenv("OUTBOX_PUBLISH_TIMEOUT_SECONDS", "4.5")
    monkeypatch.setenv("OUTBOX_LEASE_SECONDS", "15.0")
    publisher, worker = build_event_delivery()
    assert publisher.timeout == 4.5
    assert worker.publisher is publisher
    assert worker.settings.publish_timeout == 4.5


def test_security_headers_standard_endpoint(test_client):
    response = test_client.get("/health")
    headers = response.headers
    assert headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert headers["Content-Security-Policy"] == "default-src 'self'"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-XSS-Protection"] == "1; mode=block"
    assert headers["Cache-Control"] == "no-store"


def test_security_headers_exempt_docs_endpoint(test_client):
    response = test_client.get("/docs")
    headers = response.headers
    assert headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
    assert "Content-Security-Policy" not in headers


def test_authenticate_middleware_no_auth_header(test_client):
    response = test_client.get("/health")
    assert response.status_code == 200


def test_authenticate_middleware_invalid_scheme(test_client):
    response = test_client.get("/health", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired token"}


def test_authenticate_middleware_invalid_jwt(test_client):
    response = test_client.get("/health", headers={"Authorization": "Bearer invalid.token.value"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or expired token"}


def test_authenticate_middleware_invalid_claims(test_client):
    secret = os.getenv("JWT_SECRET", "test-secret")
    # Missing tenantId
    token = jwt.encode({"sub": "user1", "roles": ["ADMIN"], "jti": "j1", "ver": 1}, secret, algorithm="HS256")
    res = test_client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401

    # Empty tenantId
    token = jwt.encode({"tenantId": "", "sub": "user1", "roles": ["ADMIN"], "jti": "j1", "ver": 1}, secret, algorithm="HS256")
    res = test_client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401

    # Roles not a list
    token = jwt.encode({"tenantId": "t1", "sub": "user1", "roles": "ADMIN", "jti": "j1", "ver": 1}, secret, algorithm="HS256")
    res = test_client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401

    # ver not int
    token = jwt.encode({"tenantId": "t1", "sub": "user1", "roles": ["ADMIN"], "jti": "j1", "ver": "1"}, secret, algorithm="HS256")
    res = test_client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_authenticate_middleware_valid_jwt_sets_state(test_client):
    token = make_test_token(
        tenant_id="tenant-xyz",
        user_id="user-123",
        roles=["ADMIN"],
        permissions=["read", "write"],
    )
    response = test_client.get("/health", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_authenticate_middleware_tenant_mismatch(test_client):
    token = make_test_token(
        tenant_id="tenant-xyz",
        user_id="user-123",
        roles=["ADMIN"],
    )
    # Header mismatch
    res = test_client.get("/health", headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "other-tenant"})
    assert res.status_code == 403
    assert res.json() == {"detail": "Tenant ID mismatch"}

    # Query param mismatch
    res = test_client.get("/health?tenantId=other-tenant", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403
    assert res.json() == {"detail": "Tenant ID mismatch"}

    res = test_client.get("/health?tenant_id=other-tenant", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 403
    assert res.json() == {"detail": "Tenant ID mismatch"}


def test_authenticate_middleware_tenant_match(test_client):
    token = make_test_token(
        tenant_id="tenant-xyz",
        user_id="user-123",
        roles=["ADMIN"],
    )
    res = test_client.get("/health?tenantId=tenant-xyz", headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "tenant-xyz"})
    assert res.status_code == 200


def test_cors_headers(test_client):
    response = test_client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


@pytest.mark.asyncio
async def test_shutdown_event():
    with patch("src.presentation.main.worker.stop", new_callable=AsyncMock) as mock_worker_stop, \
         patch("src.presentation.main.publisher.stop", new_callable=AsyncMock) as mock_pub_stop:
        await shutdown_event()
        mock_worker_stop.assert_awaited_once()
        mock_pub_stop.assert_awaited_once()
