import pytest
from fastapi.testclient import TestClient
from src.presentation.main import app

client = TestClient(app)

def test_analyze_sku_aging():
    response = client.get("/api/aging/SKU1?location_id=L1&tenant_id=T1")
    assert response.status_code == 200
    data = response.json()
    assert data["sku"] == "SKU1"
    assert "is_dead_stock" in data
    assert "aging_bucket" in data
    assert "potential_scrap_emissions_kg" in data
