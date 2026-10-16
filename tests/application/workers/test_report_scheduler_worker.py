import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from src.application.workers.report_scheduler_worker import ReportSchedulerWorker

@pytest.fixture
def worker():
    mock_db = MagicMock()
    mock_db.fetch = AsyncMock(return_value=[])
    mock_db.executemany = AsyncMock()
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
        with patch.object(worker, "tick", new_callable=AsyncMock) as mock_tick:
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
        mock_datetime.now.side_effect = Exception("Test exception in tick")
        with patch("src.application.workers.report_scheduler_worker.logger.error") as mock_logger_error:
            await worker.tick()
            mock_logger_error.assert_called_once()
            assert "Error in ReportSchedulerWorker tick: Test exception in tick" in mock_logger_error.call_args[0][0]

@pytest.mark.asyncio
async def test_tick_batches_database_operations(worker):
    due_schedules = [
        {"id": "sched-1", "cronExpression": "0 0 * * *", "execution_id": "exec-1"},
        {"id": "sched-2", "cronExpression": "0 12 * * *", "execution_id": "exec-2"},
        {"id": "sched-3", "cronExpression": "0 18 * * *", "execution_id": "exec-3"},
    ]
    worker.db.fetch.return_value = due_schedules

    await worker.tick()

    assert worker.db.fetch.call_count == 1
    # Verify executemany was called exactly once for outbox events and once for schedule updates
    assert worker.db.executemany.call_count == 2

    first_call_args = worker.db.executemany.call_args_list[0]
    second_call_args = worker.db.executemany.call_args_list[1]

    assert "INSERT INTO outbox_events" in first_call_args[0][0]
    assert len(first_call_args[0][1]) == 3

    assert "UPDATE report_schedules" in second_call_args[0][0]
    assert len(second_call_args[0][1]) == 3
async def test_tick_requires_tenant_and_persistent_session(worker):
    with pytest.raises(ValueError, match='session and tenant'):
        await worker.tick(None)
