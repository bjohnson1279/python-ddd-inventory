import pytest
import json
from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from src.presentation.main import app
from src.presentation.webhooks.amazon_router import get_amazon_security, get_channel_ingestion_service

client = TestClient(app)

def test_amazon_webhook_missing_sns_header():
    response = client.post("/webhooks/amazon/sns/notifications", json={})
    assert response.status_code == 400
    assert response.json() == {"detail": "Missing SNS Message Type header"}

def test_amazon_webhook_subscription_confirmation():
    response = client.post(
        "/webhooks/amazon/sns/notifications",
        headers={"x-amz-sns-message-type": "SubscriptionConfirmation"},
        json={"SubscribeURL": "https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription"}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "Subscription confirmed manually"}

def test_amazon_webhook_invalid_signature():
    mock_sec = MagicMock()
    mock_sec.validate_sns_signature.return_value = False
    
    app.dependency_overrides[get_amazon_security] = lambda: mock_sec
    
    response = client.post(
        "/webhooks/amazon/sns/notifications",
        headers={"x-amz-sns-message-type": "Notification"},
        json={"Signature": "invalid_sig", "Message": "{}"}
    )
    
    app.dependency_overrides.clear()
    
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid SNS signature"}

def test_amazon_webhook_success():
    mock_sec = MagicMock()
    mock_sec.validate_sns_signature.return_value = True
    
    mock_ingestion = MagicMock()
    import asyncio
    async def mock_ingest(*args, **kwargs):
        pass
    mock_ingestion.ingest_order = mock_ingest
    
    app.dependency_overrides[get_amazon_security] = lambda: mock_sec
    app.dependency_overrides[get_channel_ingestion_service] = lambda: mock_ingestion
    
    message_payload = {
        "NotificationType": "ORDER_CHANGE",
        "Payload": {
            "OrderChangeNotification": {
                "AmazonOrderId": "111-2222222-3333333",
                "OrderItems": [{"sku": "AMZ-ITEM", "quantity": 1}]
            }
        }
    }
    
    response = client.post(
        "/webhooks/amazon/sns/notifications",
        headers={"x-amz-sns-message-type": "Notification"},
        json={
            "Signature": "valid_sig",
            "Message": json.dumps(message_payload)
        }
    )
    
    app.dependency_overrides.clear()
    
    assert response.status_code == 200
    assert response.json() == {"status": "Webhook processed"}
