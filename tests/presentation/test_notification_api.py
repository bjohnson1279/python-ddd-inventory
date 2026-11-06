from datetime import datetime, timezone, timedelta
import pytest
from src.infrastructure.owned_feature_models import NotificationModel
from src.infrastructure.report_alert_models import AlertCondition, AlertNotice
from src.infrastructure.financial_posting import database_now


def create_user(client, role='USER', email='staff@example.com', name='Staff Member'):
    created = client.post('/users', json={'email': email, 'role': role, 'password': 'test-password', 'name': name})
    assert created.status_code == 201, created.text
    response = client.post('/auth/login', headers={'Authorization': ''}, json={
        'tenantId': 'tenant-1', 'email': email, 'password': 'test-password'})
    assert response.status_code == 200, response.text
    return created.json(), {'Authorization': 'Bearer ' + response.json()['token']}


@pytest.mark.asyncio
async def test_get_own_unread_count(client):
    response = client.get('/api/notifications/inbox/unread')
    assert response.status_code == 200
    assert response.json()['unread_count'] == 0


@pytest.mark.asyncio
async def test_unknown_notification_actions_return_not_found(client):
    assert client.post('/api/notifications/invalid/read').status_code == 404
    assert client.post('/api/notifications/invalid/snooze', json={'hours': 24}).status_code == 404
    assert client.post('/api/notifications/invalid/escalate', json={'target_manager_id': 'missing'}).status_code == 404


@pytest.mark.asyncio
async def test_other_recipient_cannot_be_selected(client):
    assert client.get('/api/notifications/inbox/unread?user_id=other-user').status_code == 403
    assert client.get('/api/notifications?user_id=other-user').status_code == 403
    assert client.get('/api/notifications?userId=other-user').status_code == 403


@pytest.mark.asyncio
async def test_inbox_pagination_and_validation(client, db_session):
    admin_id = client.get('/auth/me').json()['id']
    now = datetime.now(timezone.utc)

    # Insert test notifications
    for i in range(5):
        db_session.add(NotificationModel(
            id=f'notif-{i}',
            tenant_id='tenant-1',
            user_id=admin_id,
            message=f'Message {i}',
            link=f'/link/{i}',
            status='UNREAD',
            created_at=now - timedelta(minutes=i)
        ))
    await db_session.commit()

    # Pagination bounds validation
    assert client.get('/api/notifications?limit=0').status_code == 422
    assert client.get('/api/notifications?limit=1001').status_code == 422
    assert client.get('/api/notifications?offset=-1').status_code == 422

    # Verify limit and offset
    page1 = client.get('/api/notifications?limit=2&offset=0')
    assert page1.status_code == 200
    rows1 = page1.json()
    assert len(rows1) == 2
    assert rows1[0]['id'] == 'notif-0'
    assert rows1[1]['id'] == 'notif-1'

    page2 = client.get('/api/notifications?limit=2&offset=2')
    assert page2.status_code == 200
    rows2 = page2.json()
    assert len(rows2) == 2
    assert rows2[0]['id'] == 'notif-2'
    assert rows2[1]['id'] == 'notif-3'


@pytest.mark.asyncio
async def test_notification_read_lifecycle(client, db_session):
    admin_id = client.get('/auth/me').json()['id']
    notif = NotificationModel(
        id='lifecycle-read',
        tenant_id='tenant-1',
        user_id=admin_id,
        message='Test read lifecycle',
        status='UNREAD'
    )
    db_session.add(notif)
    await db_session.commit()

    # Initially unread count is 1
    unread = client.get('/api/notifications/inbox/unread').json()
    assert unread['unread_count'] == 1

    # Mark as read with recipient check
    assert client.post('/api/notifications/lifecycle-read/read?user_id=other-user').status_code == 403
    read_resp = client.post('/api/notifications/lifecycle-read/read')
    assert read_resp.status_code == 200
    assert read_resp.json() == {'status': 'success'}

    # Unread count decrements to 0
    unread = client.get('/api/notifications/inbox/unread').json()
    assert unread['unread_count'] == 0

    # In inbox, status is READ and isRead is True
    inbox = client.get('/api/notifications').json()
    target = next(n for n in inbox if n['id'] == 'lifecycle-read')
    assert target['status'] == 'READ'
    assert target['isRead'] is True


@pytest.mark.asyncio
async def test_notification_snooze_lifecycle_and_boundaries(client, db_session):
    admin_id = client.get('/auth/me').json()['id']
    now = await db_session.run_sync(lambda sync: database_now(sync.connection()))

    notif_active = NotificationModel(
        id='snooze-active',
        tenant_id='tenant-1',
        user_id=admin_id,
        message='Snooze Active',
        status='UNREAD'
    )
    notif_expired = NotificationModel(
        id='snooze-expired',
        tenant_id='tenant-1',
        user_id=admin_id,
        message='Snooze Expired',
        status='SNOOZED',
        snoozed_until=now - timedelta(hours=1)
    )
    db_session.add(notif_active)
    db_session.add(notif_expired)
    await db_session.commit()

    # Boundary validation on snooze hours: ge=1, le=168
    assert client.post('/api/notifications/snooze-active/snooze', json={'hours': 0}).status_code == 422
    assert client.post('/api/notifications/snooze-active/snooze', json={'hours': 169}).status_code == 422
    assert client.post('/api/notifications/snooze-active/snooze', json={'hours': -5}).status_code == 422

    # Snooze for valid boundary 1 hour
    snooze_resp = client.post('/api/notifications/snooze-active/snooze', json={'hours': 1})
    assert snooze_resp.status_code == 200
    assert snooze_resp.json() == {'status': 'success'}

    # Unread count: active snooze is excluded, expired snooze is counted as unread
    unread = client.get('/api/notifications/inbox/unread').json()
    assert unread['unread_count'] == 1  # only snooze-expired

    # In inbox, snooze-expired displays status UNREAD, while snooze-active displays SNOOZED
    inbox = client.get('/api/notifications').json()
    active_row = next(n for n in inbox if n['id'] == 'snooze-active')
    expired_row = next(n for n in inbox if n['id'] == 'snooze-expired')

    assert active_row['status'] == 'SNOOZED'
    assert active_row['isRead'] is False
    assert active_row['snoozedUntil'] is not None

    assert expired_row['status'] == 'UNREAD'
    assert expired_row['isRead'] is False


@pytest.mark.asyncio
async def test_managers_list_and_escalate_workflow(client, db_session):
    admin_id = client.get('/auth/me').json()['id']

    # Create manager and regular user
    mgr, mgr_headers = create_user(client, role='MANAGER', email='mgr1@example.com', name='Alice Manager')
    emp, emp_headers = create_user(client, role='USER', email='emp1@example.com', name='Bob Employee')

    # Manager list should include Alice Manager, but exclude admin (self) and Bob (regular user)
    mgr_list_resp = client.get('/api/notifications/managers')
    assert mgr_list_resp.status_code == 200
    managers = mgr_list_resp.json()
    mgr_ids = [m['id'] for m in managers]
    assert mgr['userId'] in mgr_ids
    assert admin_id not in mgr_ids
    assert emp['userId'] not in mgr_ids

    # Create notification for employee
    notif = NotificationModel(
        id='emp-notif-1',
        tenant_id='tenant-1',
        user_id=emp['userId'],
        message='Urgent replenishment needed',
        link='/stock/SKU-1',
        status='UNREAD'
    )
    db_session.add(notif)
    await db_session.commit()

    # Employee escalates: cannot escalate to non-existent manager
    assert client.post('/api/notifications/emp-notif-1/escalate', headers=emp_headers,
                       json={'target_manager_id': 'non-existent'}).status_code == 404

    # Employee cannot escalate to non-manager (e.g. self)
    assert client.post('/api/notifications/emp-notif-1/escalate', headers=emp_headers,
                       json={'target_manager_id': emp['userId']}).status_code == 404

    # Manager cannot self-escalate their own notification to themselves
    mgr_notif = NotificationModel(
        id='mgr-notif-1',
        tenant_id='tenant-1',
        user_id=mgr['userId'],
        message='Manager notice',
        status='UNREAD'
    )
    db_session.add(mgr_notif)
    await db_session.commit()
    self_esc = client.post('/api/notifications/mgr-notif-1/escalate', headers=mgr_headers,
                           json={'target_manager_id': mgr['userId']})
    assert self_esc.status_code == 400
    assert 'Choose another manager' in self_esc.json()['detail']

    # Employee escalates to Alice Manager: success
    esc_resp = client.post('/api/notifications/emp-notif-1/escalate', headers=emp_headers,
                           json={'target_manager_id': mgr['userId']})
    assert esc_resp.status_code == 200
    result = esc_resp.json()
    assert result['status'] == 'success'
    escalated_id = result['escalated_notification_id']
    assert escalated_id is not None

    # Original notification status is ESCALATED and isRead is True
    emp_inbox = client.get('/api/notifications', headers=emp_headers).json()
    original = next(n for n in emp_inbox if n['id'] == 'emp-notif-1')
    assert original['status'] == 'ESCALATED'
    assert original['isRead'] is True

    # Re-escalating an already escalated notification returns 409 Conflict
    re_esc = client.post('/api/notifications/emp-notif-1/escalate', headers=emp_headers,
                         json={'target_manager_id': admin_id})
    assert re_esc.status_code == 409
    assert 'already escalated' in re_esc.json()['detail']

    # Manager Alice receives the escalated notification with prefix
    mgr_inbox = client.get('/api/notifications', headers=mgr_headers).json()
    target_mgr_notif = next(n for n in mgr_inbox if n['id'] == escalated_id)
    assert target_mgr_notif['message'].startswith('[ESCALATED] ')
    assert 'Urgent replenishment needed' in target_mgr_notif['message']
    assert target_mgr_notif['link'] == '/stock/SKU-1'


@pytest.mark.asyncio
async def test_resolved_alert_notice_inbox_and_escalate_conflict(client, db_session):
    admin_id = client.get('/auth/me').json()['id']
    mgr, _ = create_user(client, role='MANAGER', email='mgr2@example.com', name='Manager Two')
    now = datetime.now(timezone.utc)

    # Setup alert condition and notice
    condition = AlertCondition(
        id='cond-1',
        tenant_id='tenant-1',
        key='LOW_STOCK_SKU_1',
        kind='LOW_STOCK',
        active=False,
        episode=1,
        facts={'sku': 'SKU-1'},
        evaluated_at=now
    )
    notif = NotificationModel(
        id='alert-notif-1',
        tenant_id='tenant-1',
        user_id=admin_id,
        message='Low stock on SKU-1',
        status='UNREAD'
    )
    notice = AlertNotice(
        id='notice-1',
        tenant_id='tenant-1',
        condition_id='cond-1',
        episode=1,
        notification_id='alert-notif-1',
        resolved=True
    )
    db_session.add(condition)
    db_session.add(notif)
    db_session.add(notice)
    await db_session.commit()

    # By default, resolved notices are excluded from inbox
    inbox_default = client.get('/api/notifications').json()
    assert not any(n['id'] == 'alert-notif-1' for n in inbox_default)

    # When includeResolved=True, resolved notice is included with status RESOLVED and isRead True
    inbox_resolved = client.get('/api/notifications?includeResolved=true').json()
    target = next(n for n in inbox_resolved if n['id'] == 'alert-notif-1')
    assert target['status'] == 'RESOLVED'
    assert target['isRead'] is True

    # Resolved notices are not counted in unread_count
    unread = client.get('/api/notifications/inbox/unread').json()
    assert unread['unread_count'] == 0

    # Escalating a notification whose underlying alert is already resolved raises 409 Conflict
    esc_resolved = client.post('/api/notifications/alert-notif-1/escalate',
                               json={'target_manager_id': mgr['userId']})
    assert esc_resolved.status_code == 409
    assert 'already resolved' in esc_resolved.json()['detail']
