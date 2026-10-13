import asyncio
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class ReportSchedulerWorker:
    def __init__(self, db_session=None, tenant_id=None):
        self.db = db_session
        self.tenant_id = tenant_id
        self.is_running = False

    async def start(self) -> None:
        self.is_running = True
        logger.info("ReportSchedulerWorker started")
        while self.is_running:
            await self.tick()
            await asyncio.sleep(60) # check every minute

    async def stop(self) -> None:
        self.is_running = False
        logger.info("ReportSchedulerWorker stopped")

    async def tick(self):
        if self.db is None or not self.tenant_id:
            raise ValueError('Persisted scheduler session and tenant are required')
        from src.application.report_service import scheduler_tick
        return await scheduler_tick(self.db, self.tenant_id)
