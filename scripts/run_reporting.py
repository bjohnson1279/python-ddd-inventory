"""One bounded tenant pass: schedule, refresh alerts, generate queued exports."""
import asyncio
import os
from sqlalchemy import select
from src.infrastructure.database import async_session
from src.infrastructure.report_alert_models import ReportJob
from src.application.report_service import tenant_context, scheduler_tick, process_job

async def main():
    tenant = os.environ['REPORTING_WORKER_TENANT']
    if not tenant:
        raise ValueError('REPORTING_WORKER_TENANT is required')
    async with async_session() as session:
        await scheduler_tick(session, tenant)
    async with async_session() as session:
        await tenant_context(session, tenant)
        ids = list(await session.scalars(select(ReportJob.id).where(ReportJob.tenant_id == tenant,
            ReportJob.status == 'QUEUED').order_by(ReportJob.created_at, ReportJob.id).limit(100)))
    for identifier in ids:
        async with async_session() as session:
            await process_job(session, tenant, identifier)

if __name__ == '__main__':
    asyncio.run(main())
