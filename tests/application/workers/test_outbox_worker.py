import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.application.workers.outbox_worker import OutboxWorker
from src.infrastructure.messaging.models import OutboxEventModel

@pytest.fixture
def mock_publisher():
    return AsyncMock()

@pytest.fixture
def worker(mock_publisher):
    return OutboxWorker(publisher=mock_publisher, poll_interval=0.01)

@pytest.mark.asyncio
async def test_process_outbox_success(worker, mock_publisher):
    mock_event1 = MagicMock(spec=OutboxEventModel)
    mock_event1.id = "event-1"
    mock_event1.aggregate_type = "product"
    mock_event1.aggregate_id = "prod-100"
    mock_event1.payload = {"name": "Widget", "qty": 10}

    mock_event2 = MagicMock(spec=OutboxEventModel)
    mock_event2.id = "event-2"
    mock_event2.aggregate_type = "inventory"
    mock_event2.aggregate_id = "inv-200"
    mock_event2.payload = {"sku": "SKU-ABC"}

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_event1, mock_event2]

    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result

    class MockAsyncSessionContext:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("src.application.workers.outbox_worker.async_session", return_value=MockAsyncSessionContext()), \
         patch("src.application.workers.outbox_worker.DistributedCache.invalidate", new_callable=AsyncMock) as mock_invalidate:

        await worker._process_outbox()

        # Check published events
        assert mock_publisher.publish.call_count == 2
        mock_publisher.publish.assert_any_call(
            topic="inventory.product.events",
            key="prod-100",
            message={"name": "Widget", "qty": 10}
        )
        mock_publisher.publish.assert_any_call(
            topic="inventory.inventory.events",
            key="inv-200",
            message={"sku": "SKU-ABC"}
        )

        # Check cache invalidations
        assert mock_invalidate.call_count == 2
        mock_invalidate.assert_any_call("product:prod-100")
        mock_invalidate.assert_any_call("inventory:inv-200")

        # Check DB executions and commit
        assert mock_session.execute.call_count == 2  # Select stmt and Delete stmt
        mock_session.commit.assert_called_once()

@pytest.mark.asyncio
async def test_process_outbox_empty(worker, mock_publisher):
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []

    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_session = AsyncMock()
    mock_session.execute.return_value = mock_result

    class MockAsyncSessionContext:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("src.application.workers.outbox_worker.async_session", return_value=MockAsyncSessionContext()), \
         patch("src.application.workers.outbox_worker.DistributedCache.invalidate", new_callable=AsyncMock) as mock_invalidate:

        await worker._process_outbox()

        mock_publisher.publish.assert_not_called()
        mock_invalidate.assert_not_called()
        mock_session.commit.assert_not_called()

@pytest.mark.asyncio
async def test_start_and_stop_lifecycle(worker):
    call_count = 0

    async def mock_process():
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            await worker.stop()

    with patch.object(worker, "_process_outbox", side_effect=mock_process):
        start_task = asyncio.create_task(worker.start())
        await asyncio.wait_for(start_task, timeout=1.0)

        assert call_count == 2
        assert worker._running is False

@pytest.mark.asyncio
async def test_start_handles_exception(worker):
    call_count = 0

    async def mock_process_with_error():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Database connection error")
        else:
            worker._running = False

    with patch.object(worker, "_process_outbox", side_effect=mock_process_with_error), \
         patch("src.application.workers.outbox_worker.logger.error") as mock_logger_error:
        start_task = asyncio.create_task(worker.start())
        await asyncio.wait_for(start_task, timeout=1.0)

        assert call_count == 2
        mock_logger_error.assert_called_once()
        assert "Database connection error" in mock_logger_error.call_args[0][0]
