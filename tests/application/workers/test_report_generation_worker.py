import json
import pytest
from unittest.mock import AsyncMock
from src.application.workers.report_generation_worker import ReportGenerationWorker

@pytest.mark.asyncio
async def test_missing_execution_is_not_swallowed_or_reported_successful():
    generator = AsyncMock()
    with pytest.raises(ValueError, match='executionId'):
        await ReportGenerationWorker(None, generator).process_event('{}')
    generator.generate_report.assert_not_called()

@pytest.mark.asyncio
async def test_missing_or_foreign_tenant_cannot_invent_a_report_definition():
    generator = AsyncMock()
    with pytest.raises(ValueError, match='matching tenant'):
        await ReportGenerationWorker(None, generator, 'tenant').process_event(json.dumps({'tenantId': 'foreign', 'executionId': 'job'}))
    generator.generate_report.assert_not_called()
