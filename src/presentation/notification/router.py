from fastapi import APIRouter, HTTPException, Depends, Request
from src.infrastructure.auth.rbac import requires_roles
from pydantic import BaseModel
from typing import List, Optional

# Mock injection
from src.domain.notification.services import NotificationInboxService

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# In a real application, this would be injected via dependency injection
mock_inbox = NotificationInboxService([])

class SnoozeRequest(BaseModel):
    hours: int

class EscalateRequest(BaseModel):
    target_manager_id: str

@router.get("/inbox/unread")
@requires_roles(['USER', 'MANAGER', 'ADMIN'])
async def get_unread_count(request: Request, user_id: str):
    return {"unread_count": mock_inbox.get_unread_count(user_id)}

@router.post("/{notification_id}/read")
@requires_roles(['USER', 'MANAGER', 'ADMIN'])
async def mark_read(request: Request, notification_id: str, user_id: str):
    success = mock_inbox.mark_as_read(notification_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}

@router.post("/{notification_id}/snooze")
@requires_roles(['USER', 'MANAGER', 'ADMIN'])
async def snooze(request: Request, notification_id: str, user_id: str, req_body: SnoozeRequest):
    success = mock_inbox.snooze(notification_id, user_id, req_body.hours)
    if not success:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success"}

@router.post("/{notification_id}/escalate")
@requires_roles(['USER', 'MANAGER', 'ADMIN'])
async def escalate(request: Request, notification_id: str, user_id: str, req_body: EscalateRequest):
    escalated = mock_inbox.escalate(notification_id, user_id, req_body.target_manager_id)
    if not escalated:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"status": "success", "escalated_notification_id": escalated.id}
