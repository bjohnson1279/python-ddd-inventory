import pytest


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
