from fastapi import APIRouter, Depends, HTTPException, Request
import os
import hmac
import uuid
from datetime import datetime, timezone, timedelta
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import TenantModel, UserModel, LoginSessionModel, password_hash, verify_password, issue_token
from src.infrastructure.auth.security import throttle

router = APIRouter(prefix='/auth', tags=['authentication'])


class SetupDTO(BaseModel):
    tenantId: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    orgName: str = Field(min_length=1, max_length=200)
    adminName: str = Field(min_length=1, max_length=200)
    adminEmail: str = Field(min_length=3, max_length=254)
    adminPassword: str = Field(min_length=8, max_length=1024)


class LoginDTO(BaseModel):
    tenantId: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


@router.post('/setup', status_code=201)
async def setup(data: SetupDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    public = os.getenv('ALLOW_PUBLIC_SIGNUP', 'false').lower() == 'true'
    if not public:
        secret = os.getenv('SETUP_TOKEN', '')
        if not secret or not hmac.compare_digest(request.headers.get('X-Setup-Token', ''), secret):
            raise HTTPException(403, 'Tenant registration is disabled')
    await throttle(session, request, data.tenantId, 'setup')
    if await session.get(TenantModel, data.tenantId):
        raise HTTPException(409, 'Tenant already exists')
    tenant = TenantModel(id=data.tenantId, name=data.orgName)
    hashed = await run_in_threadpool(password_hash, data.adminPassword)
    user = UserModel(tenant_id=data.tenantId, email=data.adminEmail.strip().lower(),
                     name=data.adminName, password_hash=hashed, role='ADMIN')
    try:
        session.add(tenant)
        await session.flush()
        session.add(user)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, 'Tenant already exists')
    return {'tenantId': tenant.id, 'userId': user.id}


@router.post('/login')
async def login(data: LoginDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    await throttle(session, request, data.tenantId + ':' + data.email.strip().lower())
    user = await session.scalar(select(UserModel).where(
        UserModel.tenant_id == data.tenantId, UserModel.email == data.email.strip().lower()))
    # Perform the same expensive hash verification for unknown identities.
    encoded = user.password_hash if user else 'pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='
    valid = await run_in_threadpool(verify_password, data.password, encoded)
    if not user or not user.is_active or not valid:
        raise HTTPException(401, 'Invalid credentials')
    session_id = uuid.uuid4().hex
    token = issue_token(user, session_id)
    session.add(LoginSessionModel(id=session_id, user_id=user.id, tenant_id=user.tenant_id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1), revoked=False))
    await session.commit()
    return {'token': token}
