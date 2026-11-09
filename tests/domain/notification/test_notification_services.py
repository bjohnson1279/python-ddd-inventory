from datetime import datetime, timezone, timedelta
import pytest

from src.domain.notification.entity import (
    Notification,
    NotificationPreference,
    NotificationCategory,
    NotificationSeverity,
    NotificationChannel,
    NotificationStatus,
)
from src.domain.notification.services import (
    NotificationDispatcherService,
    NotificationInboxService,
)

def test_notification_entity_lifecycle():
    notif = Notification(
        id="notif-1",
        tenant_id="tenant-1",
        user_id="user-1",
        category=NotificationCategory.INVENTORY_LEVEL,
        severity=NotificationSeverity.INFO,
        message="Stock is low",
    )
    assert notif.status == NotificationStatus.UNREAD
    assert notif.snoozed_until is None

    notif.mark_as_read()
    assert notif.status == NotificationStatus.READ

    future_time = datetime.now(timezone.utc) + timedelta(hours=5)
    notif.snooze(future_time)
    assert notif.status == NotificationStatus.SNOOZED
    assert notif.snoozed_until == future_time

    notif.escalate()
    assert notif.status == NotificationStatus.ESCALATED
    assert notif.severity == NotificationSeverity.CRITICAL

def test_dispatcher_empty_targets_and_default_channels():
    dispatcher = NotificationDispatcherService()
    dispatcher.dispatch(
        tenant_id="tenant-1",
        target_users=[],
        category=NotificationCategory.SYSTEM_ANOMALY,
        severity=NotificationSeverity.INFO,
        message="No target users",
        metadata={"key": "val"},
        preferences={},
    )
    assert len(dispatcher.in_app_notifications) == 0
    assert len(dispatcher.outbox_events) == 0

    dispatcher.dispatch(
        tenant_id="tenant-1",
        target_users=["user-no-pref"],
        category=NotificationCategory.WEBHOOK_FAILURE,
        severity=NotificationSeverity.WARNING,
        message="Default channel fallback",
        metadata={"trace": "error-log"},
        preferences={},
    )
    assert len(dispatcher.in_app_notifications) == 1
    created_notif = dispatcher.in_app_notifications[0]
    assert created_notif.user_id == "user-no-pref"
    assert created_notif.category == NotificationCategory.WEBHOOK_FAILURE
    assert created_notif.severity == NotificationSeverity.WARNING
    assert created_notif.message == "Default channel fallback"
    assert created_notif.metadata == {"trace": "error-log"}
    assert len(dispatcher.outbox_events) == 0

def test_dispatcher_multi_channel_and_muted_edge_cases():
    dispatcher = NotificationDispatcherService()
    pref_all = NotificationPreference(
        id="pref-1",
        tenant_id="tenant-1",
        user_id="user-multi",
        category=NotificationCategory.APPROVAL_REQUIRED,
        channels=[NotificationChannel.IN_APP, NotificationChannel.EMAIL, NotificationChannel.SMS],
        is_muted=False,
    )
    pref_muted = NotificationPreference(
        id="pref-2",
        tenant_id="tenant-1",
        user_id="user-muted",
        category=NotificationCategory.APPROVAL_REQUIRED,
        channels=[NotificationChannel.EMAIL, NotificationChannel.SMS],
        is_muted=True,
    )
    preferences = {"user-multi": pref_all, "user-muted": pref_muted}

    dispatcher.dispatch(
        tenant_id="tenant-1",
        target_users=["user-multi", "user-muted"],
        category=NotificationCategory.APPROVAL_REQUIRED,
        severity=NotificationSeverity.INFO,
        message="Action required",
        metadata={"task_id": "T123"},
        preferences=preferences,
    )

    assert len(dispatcher.in_app_notifications) == 1
    assert dispatcher.in_app_notifications[0].user_id == "user-multi"
    assert len(dispatcher.outbox_events) == 2
    types = [e["type"] for e in dispatcher.outbox_events]
    assert "SEND_EMAIL" in types
    assert "SEND_SMS" in types

    dispatcher.outbox_events.clear()
    dispatcher.in_app_notifications.clear()

    dispatcher.dispatch(
        tenant_id="tenant-1",
        target_users=["user-muted"],
        category=NotificationCategory.APPROVAL_REQUIRED,
        severity=NotificationSeverity.CRITICAL,
        message="Critical override",
        metadata={"urgent": "true"},
        preferences=preferences,
    )

    assert len(dispatcher.in_app_notifications) == 0
    assert len(dispatcher.outbox_events) == 2
    for event in dispatcher.outbox_events:
        assert event["user_id"] == "user-muted"
        assert event["message"] == "Critical override"

def test_inbox_service_empty_and_unknown_queries():
    inbox = NotificationInboxService([])
    assert inbox.get_unread_count("unknown_user") == 0
    assert inbox._get("unknown_id", "unknown_user") is None
    assert inbox.mark_as_read("unknown_id", "unknown_user") is False
    assert inbox.snooze("unknown_id", "unknown_user", hours=4) is False
    assert inbox.escalate("unknown_id", "unknown_user", target_manager_id="mgr-1") is None

def test_inbox_service_mark_as_read_idempotency_and_user_boundary():
    notif_unread = Notification(
        id="n-10",
        tenant_id="t-1",
        user_id="alice",
        category=NotificationCategory.INVENTORY_LEVEL,
        severity=NotificationSeverity.INFO,
        message="Low stock on A",
        status=NotificationStatus.UNREAD,
    )
    notif_already_read = Notification(
        id="n-11",
        tenant_id="t-1",
        user_id="alice",
        category=NotificationCategory.SYSTEM_ANOMALY,
        severity=NotificationSeverity.WARNING,
        message="Anomaly alert",
        status=NotificationStatus.READ,
    )
    inbox = NotificationInboxService([notif_unread, notif_already_read])

    assert inbox.get_unread_count("alice") == 1
    assert inbox.mark_as_read("n-10", "bob") is False
    assert inbox.get_unread_count("alice") == 1

    assert inbox.mark_as_read("n-10", "alice") is True
    assert notif_unread.status == NotificationStatus.READ
    assert inbox.get_unread_count("alice") == 0

    assert inbox.mark_as_read("n-10", "alice") is True
    assert inbox.get_unread_count("alice") == 0

    assert inbox.mark_as_read("n-11", "alice") is True
    assert notif_already_read.status == NotificationStatus.READ
    assert inbox.get_unread_count("alice") == 0

def test_inbox_service_snooze_edge_cases():
    n_unread = Notification(
        id="n-20",
        tenant_id="t-1",
        user_id="alice",
        category=NotificationCategory.SYSTEM_ANOMALY,
        severity=NotificationSeverity.INFO,
        message="System alert",
        status=NotificationStatus.UNREAD,
    )
    n_read = Notification(
        id="n-21",
        tenant_id="t-1",
        user_id="alice",
        category=NotificationCategory.SYSTEM_ANOMALY,
        severity=NotificationSeverity.INFO,
        message="System alert 2",
        status=NotificationStatus.READ,
    )
    inbox = NotificationInboxService([n_unread, n_read])

    assert inbox.snooze("n-20", "bob", hours=2) is False
    assert inbox.get_unread_count("alice") == 1

    before_snooze = datetime.now(timezone.utc)
    assert inbox.snooze("n-20", "alice", hours=5) is True
    assert n_unread.status == NotificationStatus.SNOOZED
    assert n_unread.snoozed_until >= before_snooze + timedelta(hours=4, minutes=59)
    assert inbox.get_unread_count("alice") == 0

    assert inbox.snooze("n-21", "alice", hours=1) is True
    assert n_read.status == NotificationStatus.SNOOZED
    assert inbox.get_unread_count("alice") == 0

def test_inbox_service_escalate_edge_cases_and_indexing():
    n_unread = Notification(
        id="n-30",
        tenant_id="t-1",
        user_id="alice",
        category=NotificationCategory.APPROVAL_REQUIRED,
        severity=NotificationSeverity.WARNING,
        message="PO Approval required",
        metadata={"po_id": "PO-99"},
        status=NotificationStatus.UNREAD,
    )
    inbox = NotificationInboxService([n_unread])

    assert inbox.get_unread_count("alice") == 1
    assert inbox.get_unread_count("manager-1") == 0

    assert inbox.escalate("n-30", "wrong_user", target_manager_id="manager-1") is None

    escalated = inbox.escalate("n-30", "alice", target_manager_id="manager-1")
    assert escalated is not None
    assert n_unread.status == NotificationStatus.ESCALATED
    assert n_unread.severity == NotificationSeverity.CRITICAL
    assert inbox.get_unread_count("alice") == 0

    assert escalated.user_id == "manager-1"
    assert escalated.tenant_id == "t-1"
    assert escalated.severity == NotificationSeverity.CRITICAL
    assert escalated.status == NotificationStatus.UNREAD
    assert escalated.category == NotificationCategory.APPROVAL_REQUIRED
    assert escalated.metadata == {"po_id": "PO-99"}
    assert "[ESCALATED from alice] PO Approval required" == escalated.message
    assert inbox.get_unread_count("manager-1") == 1

    assert inbox._get(escalated.id, "manager-1") is escalated

    escalated_again = inbox.escalate(n_unread.id, "alice", target_manager_id="director-1")
    assert escalated_again is not None
    assert inbox.get_unread_count("alice") == 0
    assert inbox.get_unread_count("director-1") == 1
