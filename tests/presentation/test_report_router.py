import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

from src.presentation.main import app

# Create a test client that mocks out the role extraction logic so it passes authorization checks
def get_mock_roles(request):
    return ["ADMIN", "MANAGER"]

@pytest.fixture
def auth_client():
    with patch("src.infrastructure.auth.rbac.get_current_user_roles", return_value=["ADMIN"]):
        with TestClient(app) as c:
            yield c

def test_create_report(auth_client):
    response = auth_client.post(
        "/reports/",
        json={"name": "Test Report", "type": "INVENTORY_VALUATION"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["report"]["name"] == "Test Report"

def test_list_reports(auth_client):
    response = auth_client.get("/reports/")
    assert response.status_code == 200
    assert "reports" in response.json()

def test_execute_report(auth_client):
    response = auth_client.post(
        "/reports/report-123/execute",
        json={"format": "json"}
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "executionId" in response.json()

def test_schedule_report(auth_client):
    response = auth_client.post(
        "/reports/report-123/schedule",
        json={"cronExpression": "0 0 * * *"}
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["schedule"]["cronExpression"] == "0 0 * * *"

def test_get_shared_link_valid(auth_client):
    response = auth_client.get("/reports/shared/valid_token")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["fileUrl"] == "/uploads/reports/report_valid_token.csv"

def test_get_shared_link_expired(auth_client):
    response = auth_client.get("/reports/shared/expired")
    assert response.status_code == 403

def test_get_shared_link_invalid(auth_client):
    response = auth_client.get("/reports/shared/invalid")
    assert response.status_code == 404
