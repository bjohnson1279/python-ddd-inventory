import os
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from src.presentation.main import app
from src.presentation.webhooks.woocommerce_router import get_woocommerce_security, get_channel_ingestion_service

client = TestClient(app)

def test_get_woocommerce_security_uses_env_var():
    with patch.dict(os.environ, {"WOOCOMMERCE_API_SECRET": "test_secret_123"}):
        sec = get_woocommerce_security()
        assert sec.api_secret == b"test_secret_123"

def test_woocommerce_webhook_missing_hmac():
    response = client.post(
        "/webhooks/woocommerce/orders/create",
        headers={"X-WC-Webhook-Topic": "order.created"},
        json={}
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing Signature header"}

def test_woocommerce_webhook_unsupported_topic():
    mock_sec = MagicMock()
    mock_sec.validate_hmac.return_value = True
    app.dependency_overrides[get_woocommerce_security] = lambda: mock_sec

    response = client.post(
        "/webhooks/woocommerce/orders/create",
        headers={
            "X-WC-Webhook-Signature": "fake_hmac",
            "X-WC-Webhook-Topic": "product.updated"
        },
        json={}
    )
    
    app.dependency_overrides.clear()
    
    assert response.status_code == 400
    assert response.json() == {"detail": "Unsupported topic"}

def test_woocommerce_webhook_success():
    mock_sec_instance = MagicMock()
    mock_sec_instance.validate_hmac.return_value = True
    
    mock_ingestion_instance = MagicMock()
    import asyncio
    async def mock_ingest(*args, **kwargs):
        pass
    mock_ingestion_instance.ingest_order = mock_ingest
    
    app.dependency_overrides[get_woocommerce_security] = lambda: mock_sec_instance
    app.dependency_overrides[get_channel_ingestion_service] = lambda: mock_ingestion_instance
    
    response = client.post(
        "/webhooks/woocommerce/orders/create",
        headers={
            "X-WC-Webhook-Signature": "fake_hmac",
            "X-WC-Webhook-Topic": "order.created"
        },
        json={
            "id": 1001,
            "line_items": [{"sku": "WC-ITEM", "quantity": 1}]
        }
    )
    
    app.dependency_overrides.clear()
    
    assert response.status_code == 200
    assert response.json() == {"status": "Webhook processed"}
