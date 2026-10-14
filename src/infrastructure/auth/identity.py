"""Persisted identities and expiring, explicitly HS256-signed access tokens."""
import base64
import hashlib
import hmac
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Request
from sqlalchemy import Column, ForeignKey, String, UniqueConstraint, Integer, Boolean, DateTime
from src.infrastructure.database import Base


class TenantModel(Base):
    __tablename__ = 'python_tenants'
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)


class UserModel(Base):
    __tablename__ = 'python_users'
    __table_args__ = (UniqueConstraint('tenant_id', 'email'),)
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, ForeignKey('python_tenants.id'), nullable=False)
    email = Column(String, nullable=False)
    name = Column(String, nullable=False)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default='ADMIN')
    is_active = Column(Boolean, nullable=False, default=True)
    auth_version = Column(Integer, nullable=False, default=0)
    supplier_id = Column(String, nullable=True)


class LoginSessionModel(Base):
    __tablename__ = 'python_login_sessions'
    id = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey('python_users.id'), nullable=False, index=True)
    tenant_id = Column(String, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked = Column(Boolean, nullable=False, default=False)


class LoginRateModel(Base):
    __tablename__ = 'python_login_limits'
    key = Column(String, primary_key=True)
    attempts = Column(Integer, nullable=False)
    expires_at = Column(Integer, nullable=False)


class SecurityAuditModel(Base):
    __tablename__ = 'python_security_audit'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    actor_id = Column(String, nullable=False)
    target_id = Column(String, nullable=False)
    action = Column(String, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600_000)
    return 'pbkdf2_sha256$600000$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(digest).decode()


def verify_password(password: str, encoded: str) -> bool:
    algorithm, rounds, salt, digest = encoded.split('$')
    if algorithm != 'pbkdf2_sha256':
        return False
    actual = hashlib.pbkdf2_hmac('sha256', password.encode(), base64.b64decode(salt), int(rounds))
    return hmac.compare_digest(actual, base64.b64decode(digest))


def jwt_secret() -> str:
    secret = os.environ.get('JWT_SECRET')
    if not secret:
        raise RuntimeError('JWT_SECRET environment variable is not set')
    return secret


def issue_token(user: UserModel, session_id: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({'sub': user.id, 'tenantId': user.tenant_id, 'roles': [user.role],
                       'jti': session_id or uuid.uuid4().hex, 'ver': user.auth_version,
                       'permissions': [], 'iat': now, 'exp': now + timedelta(hours=1),
                       'iss': 'python-ddd-inventory', 'aud': 'inventory-client'},
                      jwt_secret(), algorithm='HS256')


def decode_token(token: str) -> dict:
    return jwt.decode(token, jwt_secret(), algorithms=['HS256'],
                      issuer='python-ddd-inventory', audience='inventory-client',
                      options={'require': ['sub', 'tenantId', 'jti', 'ver', 'roles', 'iat', 'exp']})


def current_tenant(request: Request) -> str:
    tenant = getattr(request.state, 'tenant_id', None)
    if not tenant:
        raise HTTPException(401, 'Invalid or missing token')
    return tenant
