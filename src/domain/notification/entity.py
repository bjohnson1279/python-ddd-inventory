from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Dict, Any

class NotificationCategory(Enum):
    INVENTORY_LEVEL = "INVENTORY_LEVEL"
    SYSTEM_ANOMALY = "SYSTEM_ANOMALY"
    WEBHOOK_FAILURE = "WEBHOOK_FAILURE"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"

class NotificationSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

class NotificationStatus(Enum):
    UNREAD = "UNREAD"
    READ = "READ"
    SNOOZED = "SNOOZED"
    ESCALATED = "ESCALATED"

class NotificationChannel(Enum):
    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    SMS = "SMS"
    WEBHOOK = "WEBHOOK"

@dataclass
class Notification:
    id: str
    tenant_id: str
    user_id: str
    category: NotificationCategory
    severity: NotificationSeverity
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    status: NotificationStatus = NotificationStatus.UNREAD
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    snoozed_until: Optional[datetime] = None

    def mark_as_read(self) -> None:
        self.status = NotificationStatus.READ

    def snooze(self, until: datetime) -> None:
        self.status = NotificationStatus.SNOOZED
        self.snoozed_until = until

    def escalate(self) -> None:
        self.status = NotificationStatus.ESCALATED
        self.severity = NotificationSeverity.CRITICAL

@dataclass
class NotificationPreference:
    id: str
    tenant_id: str
    user_id: str
    category: NotificationCategory
    channels: List[NotificationChannel] = field(default_factory=lambda: [NotificationChannel.IN_APP])
    is_muted: bool = False
