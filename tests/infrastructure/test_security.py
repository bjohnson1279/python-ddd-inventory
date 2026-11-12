"""Unit and integration tests for src.infrastructure.auth.security."""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from src.infrastructure.database import Base
from src.infrastructure.auth.identity import UserModel, LoginSessionModel, LoginRateModel
from src.infrastructure.auth.security import (
    tenant_selectors,
    permission_for,
    require_purchase_cost_permission,
    stock_response,
    authorize_request,
    throttle,
    ROLE_PERMISSIONS,
    PERMISSIONS,
    PUBLIC,
)


# --- Tests for tenant_selectors ---

def test_tenant_selectors_matching_tenant():
    data = {
        "tenantId": "t1",
        "nested": {"tenant_id": "t1", "items": [{"tenantId": "t1"}]},
        "other": 123,
    }
    # Should not raise
    tenant_selectors(data, "t1")


def test_tenant_selectors_mismatch_camel():
    data = {"tenantId": "t2"}
    with pytest.raises(HTTPException) as exc:
        tenant_selectors(data, "t1")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant ID mismatch"


def test_tenant_selectors_mismatch_snake():
    data = {"tenant_id": "t2"}
    with pytest.raises(HTTPException) as exc:
        tenant_selectors(data, "t1")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant ID mismatch"


def test_tenant_selectors_nested_list_mismatch():
    data = {"items": [{"tenant_id": "t1"}, {"nested": [{"tenantId": "mismatch"}]}]}
    with pytest.raises(HTTPException) as exc:
        tenant_selectors(data, "t1")
    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant ID mismatch"


def test_tenant_selectors_primitives_ignored():
    tenant_selectors("just a string", "t1")
    tenant_selectors(12345, "t1")
    tenant_selectors(None, "t1")


# --- Tests for permission_for ---

@pytest.mark.parametrize(
    "path,method,expected_perm",
    [
        ("/workforce/me", "GET", "workforce:self"),
        ("/api/workforce/me", "HEAD", "workforce:self"),
        ("/workforce/shifts", "GET", "workforce:self"),
        ("/workforce/shifts/123", "GET", "workforce:self"),
        ("/workforce/shifts/123/start", "POST", "workforce:self"),
        ("/workforce/shifts/123/complete", "POST", "workforce:self"),
        ("/workforce/shifts/123/activities", "POST", "workforce:self"),
        ("/workforce/other", "POST", "workforce:manage"),
        ("/users", "GET", "users:manage"),
        ("/roles/list", "POST", "users:manage"),
        ("/auth/profile", "GET", "identity:self"),
        ("/outbox", "GET", "outbox:read"),
        ("/outbox/retry", "POST", "outbox:manage"),
        ("/integrations/shopify", "GET", "integrations:read"),
        ("/shopify/sync", "POST", "integrations:manage"),
        ("/serials/price", "POST", "accounting:write"),
        ("/serials", "POST", "serials:configure"),
        ("/serials/write-off", "POST", "returns:write"),
        ("/serials/123", "GET", "serials:read"),
        ("/serials/123", "PUT", "serials:write"),
        ("/accounting/financial/journals", "GET", "finance:read"),
        ("/accounting/financial/activate", "POST", "finance:manage"),
        ("/accounting/chart-of-accounts/close-period", "POST", "finance:manage"),
        ("/accounting/financial/create", "POST", "finance:write"),
        ("/kits", "GET", "kits:read"),
        ("/kits", "POST", "kits:configure"),
        ("/kits/123", "POST", "kits:write"),
        ("/supplier-portal/dashboard", "GET", "supplier:portal"),
        ("/inventory/transfers", "GET", "transfers:read"),
        ("/inventory/transfers", "POST", "transfers:write"),
        ("/uom", "GET", "uom:read"),
        ("/uom/convert", "POST", "uom:read"),
        ("/uom/create", "POST", "uom:write"),
        ("/returns", "GET", "returns:read"),
        ("/returns/create", "POST", "returns:write"),
        ("/suppliers", "GET", "procurement:read"),
        ("/purchase-orders", "POST", "procurement:write"),
        ("/reorder-policies", "PUT", "procurement:write"),
        ("/notifications", "GET", "notifications:self"),
        ("/alerts", "GET", "alerts:read"),
        ("/alerts/create", "POST", "alerts:manage"),
        ("/cycle-counts", "GET", "counts:read"),
        ("/cycle-counts/123", "POST", "counts:submit"),
        ("/cycle-counts/plans", "POST", "counts:manage"),
        ("/cycle-counts/schedule", "POST", "counts:manage"),
        ("/cycle-counts/classify", "POST", "counts:manage"),
        ("/cycle-counts/start", "POST", "counts:manage"),
        ("/cycle-counts/approve", "POST", "counts:manage"),
        ("/cycle-counts/recount", "POST", "counts:manage"),
        ("/cycle-counts/adjust", "POST", "counts:manage"),
        ("/aging/report", "GET", "inventory:read"),
        ("/reports/scheduler/tick", "POST", "alerts:manage"),
        ("/reports/123/execute", "POST", "reports:read"),
        ("/reports", "GET", "reports:read"),
        ("/reports", "POST", "reports:write"),
        ("/accounting/summary", "GET", "accounting:read"),
        ("/accounting/summary", "POST", "accounting:write"),
        ("/inventory/products", "GET", "catalog:read"),
        ("/inventory/products", "POST", "catalog:write"),
        ("/products", "GET", "catalog:read"),
        ("/products", "POST", "catalog:write"),
        ("/graphql", "POST", "catalog:read"),
        ("/barcodes/scan", "POST", "inventory:write"),
        ("/barcodes/create", "GET", "catalog:read"),
        ("/barcodes/create", "POST", "catalog:write"),
        ("/inventory/adjust", "POST", "counts:manage"),
        ("/inventory/items", "GET", "inventory:read"),
        ("/inventory/items", "POST", "inventory:write"),
        ("/offline-sync", "GET", "inventory:read"),
        ("/offline-sync", "POST", "inventory:write"),
        ("/unknown/route", "GET", "identity:self"),
    ],
)
def test_permission_for(path, method, expected_perm):
    assert permission_for(path, method) == expected_perm


# --- Tests for require_purchase_cost_permission ---

def test_require_purchase_cost_permission_none():
    req = MagicMock(spec=Request)
    req.state = MagicMock()
    req.state.permissions = []
    # unit_cost_cents is None: should not raise
    require_purchase_cost_permission(req, None)


def test_require_purchase_cost_permission_permitted():
    req = MagicMock(spec=Request)
    req.state = MagicMock()
    req.state.permissions = ["accounting:write", "catalog:write"]
    require_purchase_cost_permission(req, 100)


def test_require_purchase_cost_permission_denied():
    req = MagicMock(spec=Request)
    req.state = MagicMock()
    req.state.permissions = ["catalog:write"]
    with pytest.raises(HTTPException) as exc:
        require_purchase_cost_permission(req, 100)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Accounting permission is required to supply purchase costs"


# --- Tests for stock_response ---

def test_stock_response_with_accounting_read():
    req = MagicMock(spec=Request)
    req.state = MagicMock()
    req.state.permissions = ["accounting:read"]
    data = {
        "sku": "ITEM-1",
        "quantity": 10,
        "costOfGoodsSoldCents": 500,
        "consumedLayers": [{"id": 1, "costCents": 250}, {"id": 2, "costCents": 250}],
    }
    result = stock_response(req, data)
    assert result == data
    assert result["costOfGoodsSoldCents"] == 500


def test_stock_response_without_accounting_read():
    req = MagicMock(spec=Request)
    req.state = MagicMock()
    req.state.permissions = ["inventory:read"]
    data = {
        "sku": "ITEM-1",
        "quantity": 10,
        "costOfGoodsSoldCents": 500,
        "consumedLayers": [{"id": 1, "costCents": 250}, {"id": 2, "costCents": 250}],
    }
    result = stock_response(req, data)
    assert "costOfGoodsSoldCents" not in result
    assert result["sku"] == "ITEM-1"
    assert result["quantity"] == 10
    assert result["consumedLayers"] == [{"id": 1}, {"id": 2}]


# --- Tests for authorize_request ---

@pytest.mark.asyncio
async def test_authorize_request_webhook_bypass():
    req = MagicMock(spec=Request)
    req.method = "POST"
    req.url = MagicMock()
    req.url.path = "/webhooks/integrations/shopify-1/orders"
    session = AsyncMock(spec=AsyncSession)

    # Webhook path bypass returns early
    res = await authorize_request(req, session)
    assert res is None
    session.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_authorize_request_public_bypass():
    req = MagicMock(spec=Request)
    req.method = "GET"
    req.url = MagicMock()
    req.url.path = "/health"
    session = AsyncMock(spec=AsyncSession)

    res = await authorize_request(req, session)
    assert res is None
    session.scalar.assert_not_called()


@pytest.mark.asyncio
async def test_authorize_request_missing_claims():
    req = MagicMock(spec=Request)
    req.method = "GET"
    req.url = MagicMock()
    req.url.path = "/products"
    req.state = MagicMock()
    del req.state.claims
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(HTTPException) as exc:
        await authorize_request(req, session)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid or missing token"


@pytest.mark.asyncio
async def test_authorize_request_expired_or_revoked_session():
    req = MagicMock(spec=Request)
    req.method = "GET"
    req.url = MagicMock()
    req.url.path = "/products"
    req.state = MagicMock()
    req.state.claims = {"sub": "u1", "tenantId": "t1", "jti": "s1", "ver": 1}

    session = AsyncMock(spec=AsyncSession)
    mock_user = MagicMock(spec=UserModel)
    mock_user.is_active = True
    mock_user.auth_version = 1

    # Session expired in the past with naive datetime to test line 162
    mock_login = MagicMock(spec=LoginSessionModel)
    mock_login.expires_at = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    mock_login.revoked = False

    session.scalar.side_effect = [mock_user, mock_login]

    with pytest.raises(HTTPException) as exc:
        await authorize_request(req, session)
    assert exc.value.status_code == 401
    assert exc.value.detail == "Session is expired or revoked"


@pytest.mark.asyncio
async def test_authorize_request_auth_version_mismatch():
    req = MagicMock(spec=Request)
    req.method = "GET"
    req.url = MagicMock()
    req.url.path = "/products"
    req.state = MagicMock()
    req.state.claims = {"sub": "u1", "tenantId": "t1", "jti": "s1", "ver": 1}

    session = AsyncMock(spec=AsyncSession)
    mock_user = MagicMock(spec=UserModel)
    mock_user.is_active = True
    mock_user.auth_version = 2  # Mismatch

    mock_login = MagicMock(spec=LoginSessionModel)
    mock_login.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_login.revoked = False

    session.scalar.side_effect = [mock_user, mock_login]

    with pytest.raises(HTTPException) as exc:
        await authorize_request(req, session)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_authorize_request_missing_permission():
    req = MagicMock(spec=Request)
    req.method = "DELETE"
    req.url = MagicMock()
    req.url.path = "/users/u2"
    req.state = MagicMock()
    req.state.claims = {"sub": "u1", "tenantId": "t1", "jti": "s1", "ver": 1}

    session = AsyncMock(spec=AsyncSession)
    mock_user = MagicMock(spec=UserModel)
    mock_user.is_active = True
    mock_user.auth_version = 1
    mock_user.role = "VIEWER"  # VIEWER lacks users:manage

    mock_login = MagicMock(spec=LoginSessionModel)
    mock_login.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_login.revoked = False

    session.scalar.side_effect = [mock_user, mock_login]

    with pytest.raises(HTTPException) as exc:
        await authorize_request(req, session)
    assert exc.value.status_code == 403
    assert exc.value.detail == "You do not have permission to perform this action"


@pytest.mark.asyncio
async def test_authorize_request_success_and_query_tenant_mismatch():
    req = MagicMock(spec=Request)
    req.method = "GET"
    req.url = MagicMock()
    req.url.path = "/products"
    req.state = MagicMock()
    req.state.claims = {"sub": "u1", "tenantId": "t1", "jti": "s1", "ver": 1}
    req.query_params = MagicMock()
    req.query_params.getlist.side_effect = lambda k: ["t2"] if k == "tenantId" else []
    req.headers = MagicMock()
    req.headers.getlist.return_value = []
    req.headers.get.return_value = ""

    session = AsyncMock(spec=AsyncSession)
    mock_user = MagicMock(spec=UserModel)
    mock_user.id = "u1"
    mock_user.tenant_id = "t1"
    mock_user.supplier_id = None
    mock_user.is_active = True
    mock_user.auth_version = 1
    mock_user.role = "VIEWER"

    mock_login = MagicMock(spec=LoginSessionModel)
    mock_login.id = "s1"
    mock_login.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_login.revoked = False

    session.scalar.side_effect = [mock_user, mock_login]

    with pytest.raises(HTTPException) as exc:
        await authorize_request(req, session)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant ID mismatch"


@pytest.mark.asyncio
async def test_authorize_request_header_tenant_mismatch():
    req = MagicMock(spec=Request)
    req.method = "GET"
    req.url = MagicMock()
    req.url.path = "/products"
    req.state = MagicMock()
    req.state.claims = {"sub": "u1", "tenantId": "t1", "jti": "s1", "ver": 1}
    req.query_params = MagicMock()
    req.query_params.getlist.return_value = []
    req.headers = MagicMock()
    req.headers.getlist.return_value = ["wrong-tenant"]
    req.headers.get.return_value = ""

    session = AsyncMock(spec=AsyncSession)
    mock_user = MagicMock(spec=UserModel)
    mock_user.id = "u1"
    mock_user.tenant_id = "t1"
    mock_user.supplier_id = None
    mock_user.is_active = True
    mock_user.auth_version = 1
    mock_user.role = "ADMIN"

    mock_login = MagicMock(spec=LoginSessionModel)
    mock_login.id = "s1"
    mock_login.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_login.revoked = False

    session.scalar.side_effect = [mock_user, mock_login]

    with pytest.raises(HTTPException) as exc:
        await authorize_request(req, session)
    assert exc.value.status_code == 403
    assert exc.value.detail == "Tenant ID mismatch"


@pytest.mark.asyncio
async def test_authorize_request_json_body_invalid():
    req = MagicMock(spec=Request)
    req.method = "POST"
    req.url = MagicMock()
    req.url.path = "/products"
    req.state = MagicMock()
    req.state.claims = {"sub": "u1", "tenantId": "t1", "jti": "s1", "ver": 1}
    req.query_params = MagicMock()
    req.query_params.getlist.return_value = []
    req.headers = MagicMock()
    req.headers.getlist.return_value = []
    req.headers.get.return_value = "application/json"
    req.body = AsyncMock(return_value=b"invalid-json")
    req.json = AsyncMock(side_effect=ValueError("Bad JSON"))

    session = AsyncMock(spec=AsyncSession)
    mock_user = MagicMock(spec=UserModel)
    mock_user.id = "u1"
    mock_user.tenant_id = "t1"
    mock_user.supplier_id = None
    mock_user.is_active = True
    mock_user.auth_version = 1
    mock_user.role = "ADMIN"

    mock_login = MagicMock(spec=LoginSessionModel)
    mock_login.id = "s1"
    mock_login.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_login.revoked = False

    session.scalar.side_effect = [mock_user, mock_login]

    with pytest.raises(HTTPException) as exc:
        await authorize_request(req, session)
    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid JSON body"


@pytest.mark.asyncio
async def test_authorize_request_json_body_valid():
    req = MagicMock(spec=Request)
    req.method = "POST"
    req.url = MagicMock()
    req.url.path = "/products"
    req.state = MagicMock()
    req.state.claims = {"sub": "u1", "tenantId": "t1", "jti": "s1", "ver": 1}
    req.query_params = MagicMock()
    req.query_params.getlist.return_value = []
    req.headers = MagicMock()
    req.headers.getlist.return_value = []
    req.headers.get.return_value = "application/json; charset=utf-8"
    req.body = AsyncMock(return_value=b'{"sku": "TEST", "tenant_id": "t1"}')
    req.json = AsyncMock(return_value={"sku": "TEST", "tenant_id": "t1"})

    session = AsyncMock(spec=AsyncSession)
    mock_user = MagicMock(spec=UserModel)
    mock_user.id = "u1"
    mock_user.tenant_id = "t1"
    mock_user.supplier_id = None
    mock_user.is_active = True
    mock_user.auth_version = 1
    mock_user.role = "ADMIN"

    mock_login = MagicMock(spec=LoginSessionModel)
    mock_login.id = "s1"
    mock_login.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    mock_login.revoked = False

    session.scalar.side_effect = [mock_user, mock_login]

    await authorize_request(req, session)
    assert req.state.roles == ["ADMIN"]
    assert req.state.user_id == "u1"
    assert req.state.tenant_id == "t1"


# --- Tests for throttle ---

@pytest.mark.asyncio
async def test_throttle_success_and_rate_limit():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        req = MagicMock(spec=Request)
        req.client = MagicMock()
        req.client.host = "127.0.0.1"

        # Account limit is 10
        for i in range(10):
            await throttle(session, req, account="acc-test", action="login")

        # 11th should raise 429
        with pytest.raises(HTTPException) as exc:
            await throttle(session, req, account="acc-test", action="login")
        assert exc.value.status_code == 429
        assert exc.value.detail == "Too many authentication attempts"
        assert "Retry-After" in exc.value.headers

    await engine.dispose()


@pytest.mark.asyncio
async def test_throttle_ip_limit():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        req = MagicMock(spec=Request)
        req.client = MagicMock()
        req.client.host = "192.168.1.100"

        # Send 30 requests with different accounts to test IP limit (30)
        for i in range(30):
            await throttle(session, req, account=f"user-{i}", action="setup")

        # 31st request should trigger IP throttle (limit 30)
        with pytest.raises(HTTPException) as exc:
            await throttle(session, req, account="user-new", action="setup")
        assert exc.value.status_code == 429
        assert exc.value.detail == "Too many authentication attempts"

    await engine.dispose()
