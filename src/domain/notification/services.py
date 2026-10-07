from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional
import uuid

from .entity import (
    Notification, NotificationPreference, NotificationCategory, 
    NotificationSeverity, NotificationChannel, NotificationStatus
)

class NotificationDispatcherService:
    def __init__(self):
        # Mock repositories for outbox and preferences
        self.outbox_events = []
        self.in_app_notifications = []

    def dispatch(
        self,
        tenant_id: str,
        target_users: List[str],
        category: NotificationCategory,
        severity: NotificationSeverity,
        message: str,
        metadata: Dict[str, str],
        preferences: Dict[str, NotificationPreference]
    ) -> None:
        """
        Dispatches a notification to target users based on their preferences.
        """
        for user_id in target_users:
            pref = preferences.get(user_id)
            if pref and pref.is_muted and severity != NotificationSeverity.CRITICAL:
                continue

            channels = pref.channels if pref else [NotificationChannel.IN_APP]

            if NotificationChannel.IN_APP in channels:
                notif = Notification(
                    id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    user_id=user_id,
                    category=category,
                    severity=severity,
                    message=message,
                    metadata=metadata
                )
                self.in_app_notifications.append(notif)
            
            if NotificationChannel.EMAIL in channels:
                self.outbox_events.append({"type": "SEND_EMAIL", "user_id": user_id, "message": message})
                
            if NotificationChannel.SMS in channels:
                self.outbox_events.append({"type": "SEND_SMS", "user_id": user_id, "message": message})


class NotificationInboxService:
    def __init__(self, notifications: List[Notification]):
        self.notifications = notifications
        self._index = {(n.id, n.user_id): n for n in notifications}

    def _get(self, notif_id: str, user_id: str) -> Optional[Notification]:
        return self._index.get((notif_id, user_id))

    def mark_as_read(self, notif_id: str, user_id: str) -> bool:
        n = self._get(notif_id, user_id)
        if not n:
            return False
        n.mark_as_read()
        return True

    def snooze(self, notif_id: str, user_id: str, hours: int) -> bool:
        n = self._get(notif_id, user_id)
        if not n:
            return False
        n.snooze(datetime.now(timezone.utc) + timedelta(hours=hours))
        return True

    def escalate(self, notif_id: str, user_id: str, target_manager_id: str) -> Optional[Notification]:
        n = self._get(notif_id, user_id)
        if not n:
            return None
        
        n.escalate()
        
        # Create a new notification for the manager
        escalated_notif = Notification(
            id=str(uuid.uuid4()),
            tenant_id=n.tenant_id,
            user_id=target_manager_id,
            category=n.category,
            severity=NotificationSeverity.CRITICAL,
            message=f"[ESCALATED from {user_id}] {n.message}",
            metadata=n.metadata
        )
        self.notifications.append(escalated_notif)
        self._index[(escalated_notif.id, escalated_notif.user_id)] = escalated_notif
        return escalated_notif

    def get_unread_count(self, user_id: str) -> int:
        return sum(1 for n in self.notifications if n.user_id == user_id and n.status == NotificationStatus.UNREAD)
