import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from src.application.workers.webhook_worker import WebhookDeliveryEngine
from datetime import datetime, timezone, timedelta

@pytest.fixture
def engine():
    return WebhookDeliveryEngine("test-secret-key")

@pytest.mark.asyncio
async def test_webhook_delivery_concurrent(engine):
    # Enqueue multiple webhooks
    with patch("socket.getaddrinfo") as mock_getaddrinfo:
        # Mock DNS resolution to return a valid public IP
        mock_getaddrinfo.return_value = [(2, 1, 6, '', ('93.184.216.34', 443))]

        for i in range(5):
            engine.enqueue_webhook(f"https://example.com/webhook{i}", {"event": "test", "id": i})

    # Assert queue has 5 items
    assert len(engine._queue) == 5

    # Mock httpx.AsyncClient
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client.post.return_value = mock_response

    # Mock asyncio.sleep to break the infinite loop after one iteration
    async def side_effect(delay):
        engine._running = False

    with patch("httpx.AsyncClient", return_value=mock_client):
        # We also need to patch the async context manager behavior of AsyncClient
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = False

        with patch("asyncio.sleep", side_effect=side_effect):
            await engine.start()

    # Verify that post was called 5 times
    assert mock_client.post.call_count == 5

    # Verify that the queue is empty
    assert len(engine._queue) == 0
