import pytest
import json
from unittest.mock import AsyncMock, MagicMock
from src.application.workers.report_generation_worker import ReportGenerationWorker

@pytest.fixture
def worker():
    mock_generator = AsyncMock()
    mock_generator.generate_report.return_value = "/uploads/reports/test.csv"
    
    return ReportGenerationWorker(
        db_session=None,
        report_generator_service=mock_generator
    )

@pytest.mark.asyncio
async def test_process_event_success(worker):
    payload = json.dumps({"executionId": "exec-1"})
    
    await worker.process_event(payload)
    
    worker.generator.generate_report.assert_called_once_with(
        report_definition_id="mock-def-123",
        execution_id="exec-1",
        format_str="csv"
    )

@pytest.mark.asyncio
async def test_process_event_missing_execution_id(worker):
    payload = json.dumps({})
    
    # Should catch the error and log it without crashing
    await worker.process_event(payload)
    
    worker.generator.generate_report.assert_not_called()
