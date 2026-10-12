from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.owned_feature_models import NotificationModel
from src.infrastructure.auth.identity import current_tenant, UserModel

router = APIRouter(prefix='/api/notifications', tags=['notifications'])


def recipient(request, user_id):
    if user_id is not None and user_id != request.state.user_id:
        raise HTTPException(403, 'Notification recipient mismatch')
    return request.state.user_id


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


@router.get('/inbox/unread')
async def get_unread_count(request: Request, user_id: str | None = None, session: AsyncSession = Depends(get_db_session)):
    owner = recipient(request, user_id)
    count = await session.scalar(select(func.count()).select_from(NotificationModel).where(
        NotificationModel.tenant_id == current_tenant(request), NotificationModel.user_id == owner,
        NotificationModel.status == 'UNREAD'))
    return {'unread_count': count}


@router.post('/{notification_id}/read')
async def mark_read(notification_id: str, request: Request, user_id: str | None = None,
                    session: AsyncSession = Depends(get_db_session)):
    notification = await owned_notification(session, request, notification_id, user_id)
    notification.status = 'READ'
    await session.commit()
    return {'status': 'success'}


@router.post('/{notification_id}/snooze')
async def snooze(notification_id: str, req_body: SnoozeRequest, request: Request, user_id: str | None = None,
                 session: AsyncSession = Depends(get_db_session)):
    notification = await owned_notification(session, request, notification_id, user_id)
    notification.status = 'SNOOZED'
    notification.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=req_body.hours)
    await session.commit()
    return {'status': 'success'}


@router.post('/{notification_id}/escalate')
async def escalate(notification_id: str, req_body: EscalateRequest, request: Request, user_id: str | None = None,
                   session: AsyncSession = Depends(get_db_session)):
    notification = await owned_notification(session, request, notification_id, user_id)
    manager = await session.scalar(select(UserModel).where(UserModel.id == req_body.target_manager_id,
        UserModel.tenant_id == current_tenant(request), UserModel.is_active.is_(True), UserModel.role.in_(['ADMIN', 'MANAGER'])))
    if not manager:
        raise HTTPException(404, 'Manager not found')
    if notification.status == 'ESCALATED':
        raise HTTPException(409, 'Notification is already escalated')
    notification.status = 'ESCALATED'
    target = NotificationModel(tenant_id=current_tenant(request), user_id=manager.id,
                              message='[ESCALATED] ' + notification.message, status='UNREAD')
    session.add(target)
    await session.commit()
    return {'status': 'success', 'escalated_notification_id': target.id}
