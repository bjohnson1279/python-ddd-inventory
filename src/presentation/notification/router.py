from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_, and_, exists
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.owned_feature_models import NotificationModel
from src.infrastructure.auth.identity import current_tenant, UserModel
from src.infrastructure.auth.rbac import requires_roles
from src.infrastructure.report_alert_models import AlertNotice
from src.infrastructure.financial_posting import database_now
from src.domain.product.layer_costing import utc
from src.application.costing_service import locked_config
from src.infrastructure.retail_alerts import notify

router = APIRouter(prefix='/notifications', tags=['notifications'])


def recipient(request, user_id):
    if request.query_params.get('userId') not in (None, request.state.user_id):
        raise HTTPException(403, 'Notification recipient mismatch')
    if user_id is not None and user_id != request.state.user_id:
        raise HTTPException(403, 'Notification recipient mismatch')
    return request.state.user_id


def unresolved(tenant):
    return ~exists(select(AlertNotice.id).where(AlertNotice.tenant_id == tenant,
        AlertNotice.notification_id == NotificationModel.id, AlertNotice.resolved.is_(True)))


@router.get('')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def inbox(request: Request, user_id: str | None = None, includeResolved: bool = False,
                limit: int = 100, offset: int = 0, session: AsyncSession = Depends(get_db_session)):
    if not 1 <= limit <= 1000 or offset < 0:
        raise HTTPException(422, 'Invalid inbox page')
    owner, tenant = recipient(request, user_id), current_tenant(request)
    query = select(NotificationModel).where(NotificationModel.tenant_id == tenant, NotificationModel.user_id == owner)
    if not includeResolved:
        query = query.where(unresolved(tenant))
    rows = list(await session.scalars(query.order_by(NotificationModel.created_at.desc(), NotificationModel.id).limit(limit).offset(offset)))
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    resolved = set(await session.scalars(select(AlertNotice.notification_id).where(AlertNotice.tenant_id == tenant, AlertNotice.resolved.is_(True))))
    return [{'id': n.id, 'title': 'Retail notification', 'message': n.message, 'link': n.link, 'createdAt': utc(n.created_at).isoformat(),
        'status': 'RESOLVED' if n.id in resolved else 'UNREAD' if n.status == 'SNOOZED' and n.snoozed_until and utc(n.snoozed_until) <= now else n.status,
        'isRead': n.status in ('READ', 'ESCALATED') or n.id in resolved,
        'snoozedUntil': utc(n.snoozed_until).isoformat() if n.snoozed_until else None} for n in rows]


async def owned_notification(session, request, notification_id, user_id):
    owner = recipient(request, user_id)
    notification = await session.scalar(select(NotificationModel).where(
        NotificationModel.id == notification_id, NotificationModel.tenant_id == current_tenant(request),
        NotificationModel.user_id == owner))
    if not notification:
        raise HTTPException(404, 'Notification not found')
    return notification


class SnoozeRequest(BaseModel):
    hours: int = Field(ge=1, le=168, strict=True)


class EscalateRequest(BaseModel):
    target_manager_id: str


@router.get('/managers')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def managers(request: Request, session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(UserModel).where(UserModel.tenant_id == current_tenant(request),
        UserModel.is_active.is_(True), UserModel.role.in_(['ADMIN', 'MANAGER']), UserModel.id != request.state.user_id).order_by(UserModel.name, UserModel.id))
    return [{'id': r.id, 'name': r.name, 'role': r.role} for r in rows]


@router.get('/inbox/unread')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def get_unread_count(request: Request, user_id: str | None = None, session: AsyncSession = Depends(get_db_session)):
    owner = recipient(request, user_id)
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    count = await session.scalar(select(func.count()).select_from(NotificationModel).where(
        NotificationModel.tenant_id == current_tenant(request), NotificationModel.user_id == owner,
        unresolved(current_tenant(request)), or_(NotificationModel.status == 'UNREAD',
            and_(NotificationModel.status == 'SNOOZED', NotificationModel.snoozed_until <= now))))
    return {'unread_count': count}


@router.post('/{notification_id}/read')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def mark_read(notification_id: str, request: Request, user_id: str | None = None,
                    session: AsyncSession = Depends(get_db_session)):
    await locked_config(session, current_tenant(request))
    notification = await owned_notification(session, request, notification_id, user_id)
    notification.status = 'READ'
    await session.commit()
    return {'status': 'success'}


@router.post('/{notification_id}/snooze')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def snooze(notification_id: str, req_body: SnoozeRequest, request: Request, user_id: str | None = None,
                 session: AsyncSession = Depends(get_db_session)):
    await locked_config(session, current_tenant(request))
    notification = await owned_notification(session, request, notification_id, user_id)
    notification.status = 'SNOOZED'
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    notification.snoozed_until = now + timedelta(hours=req_body.hours)
    await session.commit()
    return {'status': 'success'}


@router.post('/{notification_id}/escalate')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def escalate(notification_id: str, req_body: EscalateRequest, request: Request, user_id: str | None = None,
                   session: AsyncSession = Depends(get_db_session)):
    await locked_config(session, current_tenant(request))
    notification = await owned_notification(session, request, notification_id, user_id)
    manager = await session.scalar(select(UserModel).where(UserModel.id == req_body.target_manager_id,
        UserModel.tenant_id == current_tenant(request), UserModel.is_active.is_(True), UserModel.role.in_(['ADMIN', 'MANAGER'])))
    if not manager:
        raise HTTPException(404, 'Manager not found')
    if manager.id == request.state.user_id:
        raise HTTPException(400, 'Choose another manager')
    if notification.status == 'ESCALATED':
        raise HTTPException(409, 'Notification is already escalated')
    notification.status = 'ESCALATED'
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    target_id = await session.run_sync(lambda sync: notify(sync.connection(), current_tenant(request), manager.id,
        '[ESCALATED] ' + notification.message, now, link=notification.link))
    link = await session.scalar(select(AlertNotice).where(AlertNotice.tenant_id == current_tenant(request), AlertNotice.notification_id == notification.id))
    if link:
        if link.resolved:
            await session.rollback()
            raise HTTPException(409, 'Alert was already resolved')
        session.add(AlertNotice(tenant_id=current_tenant(request), condition_id=link.condition_id, episode=link.episode,
            notification_id=target_id, resolved=False))
    await session.commit()
    return {'status': 'success', 'escalated_notification_id': target_id}
