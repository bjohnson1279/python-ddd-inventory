import pytest
from fastapi.testclient import TestClient

from src.presentation.main import app

client = TestClient(app)

def test_create_report():
    response = client.post(
        "/reports/",
        json={"name": "Test Report", "type": "INVENTORY_VALUATION"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["report"]["name"] == "Test Report"

def test_list_reports():
    response = client.get("/reports/")
    assert response.status_code == 200
    assert "reports" in response.json()

def test_execute_report():
    response = client.post(
        "/reports/report-123/execute",
        json={"format": "json"}
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "executionId" in response.json()

def test_schedule_report():
    response = client.post(
        "/reports/report-123/schedule",
        json={"cronExpression": "0 0 * * *"}
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["schedule"]["cronExpression"] == "0 0 * * *"

def test_get_shared_link_valid():
    response = client.get("/reports/shared/valid_token")
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["fileUrl"] == "/uploads/reports/report_valid_token.csv"

def test_get_shared_link_expired():
    response = client.get("/reports/shared/expired")
    assert response.status_code == 403

def test_get_shared_link_invalid():
    response = client.get("/reports/shared/invalid")
    assert response.status_code == 404
