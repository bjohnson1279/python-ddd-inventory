import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException, Request

from src.infrastructure.auth.identity import (
    TenantModel,
    UserModel,
    LoginSessionModel,
    LoginRateModel,
    SecurityAuditModel,
    password_hash,
    verify_password,
    jwt_secret,
    issue_token,
    decode_token,
    current_tenant,
)


# ==========================================
# Password Hashing and Verification Tests
# ==========================================

def test_password_hash_format():
    hashed = password_hash("my-secret-password")
    parts = hashed.split("$")
    assert len(parts) == 4
    assert parts[0] == "pbkdf2_sha256"
    assert parts[1] == "600000"
    assert len(parts[2]) > 0  # salt base64
    assert len(parts[3]) > 0  # digest base64


def test_password_hash_generates_unique_salts():
    hash1 = password_hash("same-password")
    hash2 = password_hash("same-password")
    assert hash1 != hash2


def test_verify_password_success():
    pwd = "CorrectHorseBatteryStaple123!"
    encoded = password_hash(pwd)
    assert verify_password(pwd, encoded) is True


def test_verify_password_wrong_password():
    encoded = password_hash("valid-password")
    assert verify_password("wrong-password", encoded) is False


def test_verify_password_unsupported_algorithm():
    encoded = "bcrypt$600000$c2FsdA==$ZGlnZXN0"
    assert verify_password("password", encoded) is False


def test_verify_password_invalid_format():
    with pytest.raises(ValueError):
        verify_password("password", "invalid_hash_string")


# ==========================================
# JWT Secret Tests
# ==========================================

def test_jwt_secret_success(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "super-secret-key-12345678901234567890")
    assert jwt_secret() == "super-secret-key-12345678901234567890"


def test_jwt_secret_missing(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET environment variable is not set"):
        jwt_secret()


# ==========================================
# Token Issuance and Decoding Tests
# ==========================================

TEST_SECRET = "super-secret-test-jwt-key-minimum-32-chars-long!"


@pytest.fixture
def sample_user():
    user = UserModel()
    user.id = "user-123"
    user.tenant_id = "tenant-abc"
    user.role = "MANAGER"
    user.auth_version = 2
    return user


def test_issue_and_decode_token_default_session(sample_user, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    token = issue_token(sample_user)
    assert isinstance(token, str)

    payload = decode_token(token)
    assert payload["sub"] == sample_user.id
    assert payload["tenantId"] == sample_user.tenant_id
    assert payload["roles"] == ["MANAGER"]
    assert payload["ver"] == 2
    assert payload["permissions"] == []
    assert payload["iss"] == "python-ddd-inventory"
    assert payload["aud"] == "inventory-client"
    assert "jti" in payload
    assert "iat" in payload
    assert "exp" in payload


def test_issue_token_with_custom_session_id(sample_user, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    custom_jti = "session-custom-999"
    token = issue_token(sample_user, session_id=custom_jti)
    payload = decode_token(token)
    assert payload["jti"] == custom_jti


def test_decode_token_expired(sample_user, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    now = datetime.now(timezone.utc) - timedelta(hours=2)
    # create an already expired token
    expired_token = jwt.encode(
        {
            "sub": sample_user.id,
            "tenantId": sample_user.tenant_id,
            "roles": [sample_user.role],
            "jti": "jti-expired",
            "ver": sample_user.auth_version,
            "permissions": [],
            "iat": now,
            "exp": now + timedelta(minutes=30),
            "iss": "python-ddd-inventory",
            "aud": "inventory-client",
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired_token)


def test_decode_token_invalid_signature(sample_user, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    token = issue_token(sample_user)
    monkeypatch.setenv("JWT_SECRET", "another-different-super-secret-key-32-chars!")
    with pytest.raises(jwt.InvalidSignatureError):
        decode_token(token)


def test_decode_token_invalid_audience(sample_user, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {
            "sub": sample_user.id,
            "tenantId": sample_user.tenant_id,
            "roles": [sample_user.role],
            "jti": "jti-1",
            "ver": 1,
            "permissions": [],
            "iat": now,
            "exp": now + timedelta(hours=1),
            "iss": "python-ddd-inventory",
            "aud": "wrong-client",
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(jwt.InvalidAudienceError):
        decode_token(token)


def test_decode_token_missing_required_claim(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", TEST_SECRET)
    now = datetime.now(timezone.utc)
    # Missing 'ver' claim
    token = jwt.encode(
        {
            "sub": "u-1",
            "tenantId": "t-1",
            "roles": ["ADMIN"],
            "jti": "jti-1",
            "iat": now,
            "exp": now + timedelta(hours=1),
            "iss": "python-ddd-inventory",
            "aud": "inventory-client",
        },
        TEST_SECRET,
        algorithm="HS256",
    )
    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_token(token)


# ==========================================
# Current Tenant Request Helper Tests
# ==========================================

def test_current_tenant_success():
    request = MagicMock(spec=Request)
    request.state.tenant_id = "tenant-xyz-123"
    assert current_tenant(request) == "tenant-xyz-123"


def test_current_tenant_missing():
    request = MagicMock(spec=Request)
    request.state = MagicMock(spec=[])  # no tenant_id attribute
    with pytest.raises(HTTPException) as exc_info:
        current_tenant(request)
    assert exc_info.value.status_code == 401
    assert "Invalid or missing token" in exc_info.value.detail


def test_current_tenant_none():
    request = MagicMock(spec=Request)
    request.state.tenant_id = None
    with pytest.raises(HTTPException) as exc_info:
        current_tenant(request)
    assert exc_info.value.status_code == 401


# ==========================================
# SQLAlchemy Identity Models Tests
# ==========================================

def test_tenant_model():
    tenant = TenantModel(id="tenant-1", name="Acme Corp")
    assert tenant.id == "tenant-1"
    assert tenant.name == "Acme Corp"
    assert TenantModel.__tablename__ == "python_tenants"


def test_user_model_columns_and_defaults():
    user = UserModel(
        tenant_id="tenant-1",
        email="test@example.com",
        name="Tester",
        password_hash="hash123",
    )
    assert UserModel.__tablename__ == "python_users"
    assert user.tenant_id == "tenant-1"
    assert user.email == "test@example.com"
    assert user.name == "Tester"
    assert user.password_hash == "hash123"
    assert user.supplier_id is None

    # Test column defaults
    assert UserModel.role.default.arg == "ADMIN"
    assert UserModel.is_active.default.arg is True
    assert UserModel.auth_version.default.arg == 0
    # CallableColumnDefault wraps the scalar default lambda, passing context
    default_id = UserModel.id.default.arg(None)
    assert isinstance(uuid.UUID(default_id), uuid.UUID)


def test_login_session_model():
    expires = datetime.now(timezone.utc) + timedelta(days=1)
    session = LoginSessionModel(
        id="session-1",
        user_id="user-1",
        tenant_id="tenant-1",
        expires_at=expires,
    )
    assert session.id == "session-1"
    assert session.user_id == "user-1"
    assert session.tenant_id == "tenant-1"
    assert session.expires_at == expires
    assert LoginSessionModel.revoked.default.arg is False
    assert LoginSessionModel.__tablename__ == "python_login_sessions"


def test_login_rate_model():
    rate = LoginRateModel(
        key="rate-key-1",
        attempts=3,
        expires_at=1700000000,
    )
    assert rate.key == "rate-key-1"
    assert rate.attempts == 3
    assert rate.expires_at == 1700000000
    assert LoginRateModel.__tablename__ == "python_login_limits"


def test_security_audit_model():
    audit = SecurityAuditModel(
        tenant_id="tenant-1",
        actor_id="actor-1",
        target_id="target-1",
        action="LOGIN_ATTEMPT",
    )
    assert SecurityAuditModel.__tablename__ == "python_security_audit"
    assert audit.tenant_id == "tenant-1"
    assert audit.actor_id == "actor-1"
    assert audit.target_id == "target-1"
    assert audit.action == "LOGIN_ATTEMPT"

    # CallableColumnDefault wraps the scalar default lambda, passing context
    default_id = SecurityAuditModel.id.default.arg(None)
    assert isinstance(uuid.UUID(default_id), uuid.UUID)
    default_occurred_at = SecurityAuditModel.occurred_at.default.arg(None)
    assert isinstance(default_occurred_at, datetime)
