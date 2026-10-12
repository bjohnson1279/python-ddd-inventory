from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import TenantModel, UserModel, password_hash, verify_password, issue_token

router = APIRouter(prefix='/auth', tags=['authentication'])


class SetupDTO(BaseModel):
    tenantId: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    orgName: str = Field(min_length=1, max_length=200)
    adminName: str = Field(min_length=1, max_length=200)
    adminEmail: str = Field(min_length=3, max_length=254)
    adminPassword: str = Field(min_length=8, max_length=1024)


class LoginDTO(BaseModel):
    tenantId: str
    email: str
    password: str = Field(max_length=1024)


@router.post('/setup', status_code=201)
async def setup(data: SetupDTO, session: AsyncSession = Depends(get_db_session)):
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
async def login(data: LoginDTO, session: AsyncSession = Depends(get_db_session)):
    user = await session.scalar(select(UserModel).where(
        UserModel.tenant_id == data.tenantId, UserModel.email == data.email.strip().lower()))
    # Perform the same expensive hash verification for unknown identities.
    encoded = user.password_hash if user else await run_in_threadpool(password_hash, 'unusable-password')
    valid = await run_in_threadpool(verify_password, data.password, encoded)
    if not user or not valid:
        raise HTTPException(401, 'Invalid credentials')
    return {'token': issue_token(user)}
