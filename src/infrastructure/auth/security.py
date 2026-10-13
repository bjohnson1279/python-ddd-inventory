"""Current persisted identity, tenant selector validation and permission policy."""
import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from .identity import UserModel, LoginSessionModel, LoginRateModel, current_tenant, jwt_secret

PERMISSIONS = {
    'identity:self', 'catalog:read', 'catalog:write', 'inventory:read', 'inventory:write',
    'counts:read', 'counts:manage', 'counts:submit', 'accounting:read', 'accounting:write',
    'reports:read', 'reports:write', 'notifications:self', 'users:manage', 'supplier:portal',
    'procurement:read', 'procurement:write',
    'returns:read', 'returns:write',
    'transfers:read', 'transfers:write', 'uom:read', 'uom:write',
    'outbox:read', 'outbox:manage',
    'serials:read', 'serials:write', 'serials:configure', 'kits:read', 'kits:write', 'kits:configure',
    'finance:read', 'finance:write', 'finance:manage',
    'integrations:read', 'integrations:manage',
    'alerts:read', 'alerts:manage',
    'workforce:self', 'workforce:manage',
}
READ = {'identity:self', 'catalog:read', 'inventory:read', 'accounting:read', 'reports:read', 'notifications:self', 'procurement:read', 'returns:read', 'transfers:read', 'uom:read'}
READ |= {'serials:read', 'kits:read'}
READ |= {'alerts:read'}
ROLE_PERMISSIONS = {
    'ADMIN': PERMISSIONS - {'supplier:portal'},
    'MANAGER': PERMISSIONS - {'users:manage', 'supplier:portal'},
    'USER': READ, 'VIEWER': READ,
    'WAREHOUSE_OPERATOR': {'identity:self', 'catalog:read', 'inventory:read', 'inventory:write',
                           'counts:read', 'counts:submit', 'notifications:self', 'transfers:read', 'transfers:write', 'uom:read'},
    'ACCOUNTANT': READ | {'accounting:write'},
    'SUPPLIER_USER': {'identity:self', 'supplier:portal'},
}
ROLE_PERMISSIONS['WAREHOUSE_OPERATOR'] |= {'serials:read', 'serials:write', 'kits:read', 'kits:write'}
ROLE_PERMISSIONS['WAREHOUSE_OPERATOR'] |= {'alerts:read'}
ROLE_PERMISSIONS['WAREHOUSE_OPERATOR'] |= {'workforce:self'}
ROLE_PERMISSIONS['ACCOUNTANT'] |= {'finance:read', 'finance:write', 'finance:manage'}
ROLE_PERMISSIONS['ACCOUNTANT'] |= {'reports:write'}
PUBLIC = {('GET', '/health'), ('GET', '/api/health'), ('POST', '/auth/setup'), ('POST', '/auth/login'),
          ('POST', '/api/auth/setup'), ('POST', '/api/auth/login'),
          ('POST', '/webhooks/shopify/orders/create'), ('POST', '/webhooks/woocommerce/orders/create'),
          ('POST', '/webhooks/amazon/sns/notifications')}


def tenant_selectors(value, tenant):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ('tenantId', 'tenant_id') and item != tenant:
                raise HTTPException(403, 'Tenant ID mismatch')
            tenant_selectors(item, tenant)
    elif isinstance(value, list):
        for item in value:
            tenant_selectors(item, tenant)


def permission_for(path, method):
    path = path.removeprefix('/api') if path.startswith('/api/') else path
    read = method in ('GET', 'HEAD')
    if path.startswith('/workforce'):
        if path in ('/workforce/me', '/workforce/shifts') and read or '/shifts/' in path and (read or path.endswith(('/start', '/complete', '/activities'))):
            return 'workforce:self'
        return 'workforce:manage'
    if path.startswith('/users') or path.startswith('/roles'):
        return 'users:manage'
    if path.startswith('/auth/'):
        return 'identity:self'
    if path.startswith('/outbox'):
        return 'outbox:read' if read else 'outbox:manage'
    if path.startswith(('/integrations', '/shopify')):
        return 'integrations:read' if read else 'integrations:manage'
    if path.startswith('/serials'):
        if path.endswith('/price'):
            return 'accounting:write'
        if path == '/serials' and not read:
            return 'serials:configure'
        if path.endswith('/write-off'):
            return 'returns:write'
        return 'serials:read' if read else 'serials:write'
    if path.startswith(('/accounting/financial', '/accounting/journals', '/accounting/ledger', '/accounting/chart-of-accounts',
        '/accounting/sources', '/accounting/documents', '/accounting/trial-balance', '/accounting/general-ledger', '/accounting/income-statement', '/accounting/balance-sheet')):
        if path.endswith(('/activate', '/close-period')):
            return 'finance:manage'
        return 'finance:read' if read else 'finance:write'
    if path.startswith('/kits'):
        return 'kits:read' if read else 'kits:configure' if path == '/kits' else 'kits:write'
    if path.startswith('/supplier-portal'):
        return 'supplier:portal'
    if path.startswith('/inventory/transfers'):
        return 'transfers:read' if read else 'transfers:write'
    if path.startswith('/uom'):
        return 'uom:read' if read or path.endswith('/convert') else 'uom:write'
    if path.startswith('/returns'):
        return 'returns:read' if read else 'returns:write'
    if path.startswith(('/suppliers', '/purchase-orders', '/reorder-policies')):
        return 'procurement:read' if read else 'procurement:write'
    if path.startswith('/notifications'):
        return 'notifications:self'
    if path.startswith('/alerts'):
        return 'alerts:read' if read else 'alerts:manage'
    if path.startswith('/cycle-counts'):
        if path.endswith(('/plans', '/schedule', '/classify', '/start', '/approve', '/recount', '/adjust')):
            return 'counts:manage'
        return 'counts:read' if read else 'counts:submit'
    if path.startswith('/reports') or path.startswith('/aging'):
        if path == '/aging/report':
            return 'inventory:read'
        if path == '/reports/scheduler/tick':
            return 'alerts:manage'
        if path.startswith('/reports') and path.endswith('/execute'):
            return 'reports:read'
        return 'reports:read' if read else 'reports:write'
    if path.startswith('/accounting'):
        return 'accounting:read' if read else 'accounting:write'
    if path.startswith('/inventory/products') or path.startswith('/products') or path.startswith('/graphql'):
        return 'catalog:read' if read or path.startswith('/graphql') else 'catalog:write'
    if path.startswith('/barcodes'):
        return 'inventory:write' if path.endswith('/scan') else ('catalog:read' if read else 'catalog:write')
    if path.startswith('/inventory') or path.startswith('/offline-sync'):
        if path.endswith('/adjust'):
            return 'counts:manage'
        return 'inventory:read' if read else 'inventory:write'
    return 'identity:self'


def require_purchase_cost_permission(request, unit_cost_cents):
    if unit_cost_cents is not None and 'accounting:write' not in request.state.permissions:
        raise HTTPException(403, 'Accounting permission is required to supply purchase costs')


def stock_response(request, result):
    if 'accounting:read' in request.state.permissions:
        return result
    masked = {key: value for key, value in result.items() if key != 'costOfGoodsSoldCents'}
    if 'consumedLayers' in masked:
        masked['consumedLayers'] = [{key: value for key, value in layer.items() if key != 'costCents'}
                                    for layer in masked['consumedLayers']]
    return masked


async def authorize_request(request: Request, session: AsyncSession = Depends(get_db_session)):
    if request.method == 'POST' and request.url.path.startswith('/webhooks/integrations/') and request.url.path.endswith('/orders'):
        return  # Connection-specific HMAC is enforced by the webhook handler.
    if (request.method, request.url.path) in PUBLIC:
        return
    claims = getattr(request.state, 'claims', None)
    if not claims:
        raise HTTPException(401, 'Invalid or missing token')
    user = await session.scalar(select(UserModel).where(
        UserModel.id == claims['sub'], UserModel.tenant_id == claims['tenantId']))
    login = await session.scalar(select(LoginSessionModel).where(
        LoginSessionModel.id == claims['jti'], LoginSessionModel.user_id == claims['sub'],
        LoginSessionModel.tenant_id == claims['tenantId'], LoginSessionModel.revoked.is_(False)))
    expiry = login.expires_at if login else None
    if expiry and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if not user or not user.is_active or user.auth_version != claims['ver'] or not login or expiry <= datetime.now(timezone.utc):
        raise HTTPException(401, 'Session is expired or revoked')
    permissions = ROLE_PERMISSIONS.get(user.role, set())
    if permission_for(request.url.path, request.method) not in permissions:
        raise HTTPException(403, 'You do not have permission to perform this action')
    roles = [user.role]
    if user.role in ('VIEWER', 'ACCOUNTANT'):
        roles.append('USER')  # Compatibility with existing read-only route guards.
    request.state.roles = roles
    request.state.permissions = sorted(permissions)
    request.state.user_id = user.id
    request.state.tenant_id = user.tenant_id
    request.state.supplier_id = user.supplier_id
    request.state.session_id = login.id
    for key in ('tenantId', 'tenant_id'):
        for supplied in request.query_params.getlist(key):
            if supplied != user.tenant_id:
                raise HTTPException(403, 'Tenant ID mismatch')
    for supplied in request.headers.getlist('x-tenant-id'):
        if supplied != user.tenant_id:
            raise HTTPException(403, 'Tenant ID mismatch')
    if request.method in ('POST', 'PUT', 'PATCH') and request.headers.get('content-type', '').split(';')[0] == 'application/json':
        if await request.body():
            try:
                body = await request.json()
            except ValueError:
                raise HTTPException(422, 'Invalid JSON body')
            tenant_selectors(body, user.tenant_id)


async def throttle(session, request, account, action='login'):
    now = int(time.time())
    window = 300
    bucket = now // window
    expires = (bucket + 1) * window
    ip = request.client.host if request.client else 'unknown'
    # Never trust forwarded IP headers without a configured proxy trust boundary.
    subjects = [(f'{action}:ip:{ip}', 30), (f'{action}:account:{account}', 10)]
    insert = sqlite_insert if session.bind.dialect.name == 'sqlite' else pg_insert
    exceeded = False
    await session.execute(delete(LoginRateModel).where(LoginRateModel.expires_at <= now))
    for subject, limit in subjects:
        digest = hmac.new(jwt_secret().encode(), subject.encode(), hashlib.sha256).hexdigest()
        key = f'{bucket}:{digest}'
        count = await session.scalar(insert(LoginRateModel).values(key=key, attempts=1, expires_at=expires)
            .on_conflict_do_update(index_elements=['key'], set_={'attempts': LoginRateModel.attempts + 1})
            .returning(LoginRateModel.attempts))
        exceeded |= count > limit
    await session.commit()
    if exceeded:
        raise HTTPException(429, 'Too many authentication attempts', headers={'Retry-After': str(expires - now)})
