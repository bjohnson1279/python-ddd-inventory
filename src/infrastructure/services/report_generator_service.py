from src.application.report_service import owned, process_job, tenant_context
from src.infrastructure.report_alert_models import ReportJob

class ReportGeneratorService:
    def __init__(self, db_session=None, tenant_id=None):
        self.db_session, self.tenant_id = db_session, tenant_id

    async def generate_report(self, report_definition_id, execution_id, format_str):
        if self.db_session is None or not self.tenant_id:
            raise ValueError('Persisted report session and tenant are required')
        if format_str not in ('csv', 'json'):
            raise ValueError('Only CSV and JSON report exports are supported')
        await tenant_context(self.db_session, self.tenant_id)
        job = await owned(self.db_session, ReportJob, self.tenant_id, execution_id)
        if job.report_id != report_definition_id or job.format != format_str:
            raise ValueError('Report execution does not match the requested definition/format')
        result = await process_job(self.db_session, self.tenant_id, execution_id)
        if result['status'] != 'COMPLETED':
            raise ValueError('Report export was not completed')
        return result['fileUrl']
