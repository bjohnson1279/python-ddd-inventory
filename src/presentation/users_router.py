import secrets
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import UserModel, TenantModel, LoginSessionModel, SecurityAuditModel, current_tenant, password_hash, verify_password
from src.infrastructure.auth.security import ROLE_PERMISSIONS, PERMISSIONS

router = APIRouter(tags=['users and sessions'])


def user_dto(user):
    return {'id': user.id, 'userId': user.id, 'tenantId': user.tenant_id, 'email': user.email,
            'name': user.name, 'role': user.role, 'isActive': user.is_active, 'supplierId': user.supplier_id}


class InviteDTO(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(default='', max_length=200)
    role: str = 'USER'
    password: str | None = Field(default=None, min_length=8, max_length=1024)
    supplierId: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator('role')
    @classmethod
    def validate_role(cls, value):
        if value.upper() not in ROLE_PERMISSIONS:
            raise ValueError('Unknown role')
        return value.upper()

    @model_validator(mode='after')
    def supplier_binding(self):
        if self.role == 'SUPPLIER_USER' and not self.supplierId:
            raise ValueError('Supplier users require an administrator-assigned supplierId')
        if self.role != 'SUPPLIER_USER' and self.supplierId:
            raise ValueError('Only supplier users can have supplierId')
        return self


class RoleDTO(BaseModel):
    role: str
    supplierId: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator('role')
    @classmethod
    def validate_role(cls, value):
        return InviteDTO.validate_role(value)


class ActiveDTO(BaseModel):
    isActive: bool


async def admin_lock(session, request):
    tenant = current_tenant(request)
    await session.execute(update(TenantModel).where(TenantModel.id == tenant).values(name=TenantModel.name))
    actor = await session.scalar(select(UserModel).where(UserModel.id == request.state.user_id,
        UserModel.tenant_id == tenant).execution_options(populate_existing=True))
    if not actor or not actor.is_active or actor.role != 'ADMIN':
        raise HTTPException(403, 'Administrator access required')
    return tenant


async def owned_user(session, tenant, user_id):
    user = await session.scalar(select(UserModel).where(UserModel.id == user_id, UserModel.tenant_id == tenant))
    if not user:
        raise HTTPException(404, 'User not found')
    return user


async def keep_admin(session, user, removing):
    if removing and user.is_active and user.role == 'ADMIN':
        admins = list(await session.scalars(select(UserModel.id).where(
            UserModel.tenant_id == user.tenant_id, UserModel.role == 'ADMIN', UserModel.is_active.is_(True))))
        if len(admins) <= 1:
            raise HTTPException(409, 'Cannot remove the last active administrator')


async def revoke_and_audit(session, request, user, action):
    user.auth_version += 1
    await session.execute(update(LoginSessionModel).where(LoginSessionModel.tenant_id == user.tenant_id,
        LoginSessionModel.user_id == user.id).values(revoked=True))
    session.add(SecurityAuditModel(tenant_id=user.tenant_id, actor_id=request.state.user_id,
                                  target_id=user.id, action=action))


@router.get('/users')
async def users(request: Request, session: AsyncSession = Depends(get_db_session)):
    return {'users': [user_dto(user) for user in await session.scalars(select(UserModel).where(
        UserModel.tenant_id == current_tenant(request)).order_by(UserModel.email))]}


@router.post('/users', status_code=201)
async def invite(data: InviteDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = await admin_lock(session, request)
    generated = data.password is None
    password = data.password or secrets.token_urlsafe(20)
    hashed = await run_in_threadpool(password_hash, password)
    user = UserModel(tenant_id=tenant, email=data.email.strip().lower(), name=data.name or data.email,
                    role=data.role, supplier_id=data.supplierId, password_hash=hashed)
    session.add(user)
    try:
        await session.flush()
        session.add(SecurityAuditModel(tenant_id=tenant, actor_id=request.state.user_id,
                                      target_id=user.id, action='user-created'))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, 'User already exists')
    result = user_dto(user)
    if generated:
        result['temporaryPassword'] = password
    return result


@router.patch('/users/{user_id}/role')
async def change_role(user_id: str, data: RoleDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = await admin_lock(session, request)
    user = await owned_user(session, tenant, user_id)
    if (data.role == 'SUPPLIER_USER') != bool(data.supplierId):
        raise HTTPException(422, 'Supplier binding must match the selected role')
    await keep_admin(session, user, data.role != 'ADMIN')
    user.role, user.supplier_id = data.role, data.supplierId
    await revoke_and_audit(session, request, user, 'role-changed')
    await session.commit()
    return user_dto(user)


@router.patch('/users/{user_id}/status')
async def change_status(user_id: str, data: ActiveDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = await admin_lock(session, request)
    user = await owned_user(session, tenant, user_id)
    await keep_admin(session, user, not data.isActive)
    user.is_active = data.isActive
    await revoke_and_audit(session, request, user, 'user-enabled' if data.isActive else 'user-disabled')
    await session.commit()
    return user_dto(user)


@router.post('/users/{user_id}/revoke-sessions')
async def revoke_user(user_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = await admin_lock(session, request)
    user = await owned_user(session, tenant, user_id)
    await revoke_and_audit(session, request, user, 'sessions-revoked')
    await session.commit()
    return {'success': True}


@router.get('/roles/permissions')
async def permissions():
    return [permission_dto(name) for name in sorted(PERMISSIONS)]


def permission_dto(name):
    resource, action = name.split(':', 1)
    return {'id': name, 'resource': resource, 'action': action}


@router.get('/roles')
async def roles(request: Request):
    return [{'id': name, 'name': name, 'isCustom': False, 'tenantId': current_tenant(request),
             'permissions': [permission_dto(permission) for permission in sorted(grants)]}
            for name, grants in ROLE_PERMISSIONS.items()]


@router.get('/auth/me')
async def me(request: Request, session: AsyncSession = Depends(get_db_session)):
    return user_dto(await owned_user(session, current_tenant(request), request.state.user_id))


@router.post('/auth/logout')
async def logout(request: Request, session: AsyncSession = Depends(get_db_session)):
    await session.execute(update(LoginSessionModel).where(LoginSessionModel.id == request.state.session_id,
        LoginSessionModel.tenant_id == current_tenant(request)).values(revoked=True))
    await session.commit()
    return {'success': True}


class PasswordDTO(BaseModel):
    currentPassword: str = Field(min_length=1, max_length=1024)
    newPassword: str = Field(min_length=8, max_length=1024)


@router.post('/auth/password')
async def change_password(data: PasswordDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    user = await owned_user(session, current_tenant(request), request.state.user_id)
    if not await run_in_threadpool(verify_password, data.currentPassword, user.password_hash):
        raise HTTPException(401, 'Invalid current password')
    user.password_hash = await run_in_threadpool(password_hash, data.newPassword)
    await revoke_and_audit(session, request, user, 'password-changed')
    await session.commit()
    return {'success': True, 'loginRequired': True}
