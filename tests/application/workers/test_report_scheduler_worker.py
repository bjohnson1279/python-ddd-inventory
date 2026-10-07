import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from src.application.workers.report_scheduler_worker import ReportSchedulerWorker

@pytest.fixture
def worker():
    mock_db = MagicMock()
    return ReportSchedulerWorker(db_session=mock_db)

def test_init(worker):
    assert worker.db is not None
    assert worker.is_running is False

@pytest.mark.asyncio
async def test_start_and_loop(worker):
    tick_count = 0

    async def mock_sleep(delay):
        nonlocal tick_count
        tick_count += 1
        if tick_count >= 2:
            await worker.stop()

    with patch("asyncio.sleep", side_effect=mock_sleep):
        with patch.object(worker, "tick", wraps=worker.tick) as mock_tick:
            await worker.start()
            assert mock_tick.call_count == 2
            assert worker.is_running is False

@pytest.mark.asyncio
async def test_stop(worker):
    worker.is_running = True
    await worker.stop()
    assert worker.is_running is False

@pytest.mark.asyncio
async def test_tick_handles_exception(worker):
    with patch("src.application.workers.report_scheduler_worker.datetime") as mock_datetime:
        mock_datetime.utcnow.side_effect = Exception("Test exception in tick")
        with patch("src.application.workers.report_scheduler_worker.logger.error") as mock_logger_error:
            await worker.tick()
            mock_logger_error.assert_called_once()
            assert "Error in ReportSchedulerWorker tick: Test exception in tick" in mock_logger_error.call_args[0][0]
