import pytest
from src.domain.notification.entity import (
    Notification, NotificationPreference, NotificationCategory, 
    NotificationSeverity, NotificationChannel, NotificationStatus
)
from src.domain.notification.services import NotificationDispatcherService, NotificationInboxService

def test_dispatcher_routes_based_on_preferences():
    dispatcher = NotificationDispatcherService()
    
    prefs = {
        "user1": NotificationPreference(id="p1", tenant_id="t1", user_id="user1", category=NotificationCategory.INVENTORY_LEVEL, channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL]),
        "user2": NotificationPreference(id="p2", tenant_id="t1", user_id="user2", category=NotificationCategory.INVENTORY_LEVEL, channels=[NotificationChannel.SMS], is_muted=True)
    }

    dispatcher.dispatch(
        tenant_id="t1",
        target_users=["user1", "user2"],
        category=NotificationCategory.INVENTORY_LEVEL,
        severity=NotificationSeverity.WARNING,
        message="Low stock on SKU A",
        metadata={},
        preferences=prefs
    )

    # user1 should have in-app and email
    assert len(dispatcher.in_app_notifications) == 1
    assert dispatcher.in_app_notifications[0].user_id == "user1"
    
    # user2 is muted for WARNING
    outbox_emails = [e for e in dispatcher.outbox_events if e["type"] == "SEND_EMAIL"]
    outbox_sms = [e for e in dispatcher.outbox_events if e["type"] == "SEND_SMS"]
    assert len(outbox_emails) == 1
    assert len(outbox_sms) == 0

def test_dispatcher_overrides_mute_for_critical():
    dispatcher = NotificationDispatcherService()
    prefs = {
        "user2": NotificationPreference(id="p2", tenant_id="t1", user_id="user2", category=NotificationCategory.INVENTORY_LEVEL, channels=[NotificationChannel.SMS], is_muted=True)
    }

    dispatcher.dispatch(
        tenant_id="t1",
        target_users=["user2"],
        category=NotificationCategory.INVENTORY_LEVEL,
        severity=NotificationSeverity.CRITICAL,
        message="System failure",
        metadata={},
        preferences=prefs
    )

    outbox_sms = [e for e in dispatcher.outbox_events if e["type"] == "SEND_SMS"]
    assert len(outbox_sms) == 1

def test_inbox_service_interactions():
    notif = Notification(
        id="n1", tenant_id="t1", user_id="u1", category=NotificationCategory.SYSTEM_ANOMALY,
        severity=NotificationSeverity.INFO, message="Test"
    )
    inbox = NotificationInboxService([notif])

    assert inbox.get_unread_count("u1") == 1
    
    inbox.mark_as_read("n1", "u1")
    assert inbox.get_unread_count("u1") == 0
    assert notif.status == NotificationStatus.READ

    inbox.snooze("n1", "u1", 24)
    assert notif.status == NotificationStatus.SNOOZED
    assert notif.snoozed_until is not None

def test_escalate():
    notif = Notification(
        id="n1", tenant_id="t1", user_id="u1", category=NotificationCategory.SYSTEM_ANOMALY,
        severity=NotificationSeverity.INFO, message="Test"
    )
    inbox = NotificationInboxService([notif])
    
    escalated = inbox.escalate("n1", "u1", "manager1")
    assert escalated is not None
    assert notif.status == NotificationStatus.ESCALATED
    
    assert escalated.user_id == "manager1"
    assert escalated.severity == NotificationSeverity.CRITICAL
    assert "[ESCALATED from u1]" in escalated.message
