import json
from src.infrastructure.report_alert_models import ReportJob
from src.application.report_service import owned, tenant_context

class ReportGenerationWorker:
    def __init__(self, db_session, report_generator_service, tenant_id=None):
        self.db, self.generator, self.tenant_id = db_session, report_generator_service, tenant_id

    async def process_event(self, event_payload):
        payload = json.loads(event_payload)
        identifier = payload.get('executionId')
        if not isinstance(identifier, str) or not identifier:
            raise ValueError('executionId is required')
        if self.db is None or not self.tenant_id or payload.get('tenantId') != self.tenant_id:
            raise ValueError('Persisted worker session and matching tenant are required')
        await tenant_context(self.db, self.tenant_id)
        job = await owned(self.db, ReportJob, self.tenant_id, identifier)
        return await self.generator.generate_report(job.report_id, job.id, job.format)
