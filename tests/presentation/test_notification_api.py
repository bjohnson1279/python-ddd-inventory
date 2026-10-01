import pytest
from fastapi.testclient import TestClient
from src.presentation.main import app

client = TestClient(app)

def test_get_unread_count():
    response = client.get("/api/notifications/inbox/unread?user_id=u1")
    assert response.status_code == 200
    assert "unread_count" in response.json()

def test_mark_read_not_found():
    response = client.post("/api/notifications/invalid_id/read?user_id=u1")
    assert response.status_code == 404

def test_snooze_not_found():
    response = client.post("/api/notifications/invalid_id/snooze?user_id=u1", json={"hours": 24})
    assert response.status_code == 404

def test_escalate_not_found():
    response = client.post("/api/notifications/invalid_id/escalate?user_id=u1", json={"target_manager_id": "m1"})
    assert response.status_code == 404
