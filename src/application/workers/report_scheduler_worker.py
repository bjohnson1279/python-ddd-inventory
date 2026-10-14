import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

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

    async def tick(self) -> None:
        try:
            now = datetime.now(timezone.utc)
            due_schedules = []
            if self.db and hasattr(self.db, "fetch"):
                due_schedules = await self.db.fetch("SELECT * FROM report_schedules WHERE nextRunAt <= ?", now)

            # Performance Optimization: Batch parameter collection to eliminate N+1 database queries.
            # Instead of executing individual INSERT and UPDATE statements per schedule inside the loop,
            # collect all outbox event payloads and schedule updates, then perform bulk executemany operations outside the loop.
            outbox_events = []
            schedule_updates = []

            for schedule in due_schedules:
                new_exec_id = schedule.get("execution_id", str(uuid.uuid4()))
                payload = json.dumps({'executionId': new_exec_id})
                outbox_events.append(('ReportExecutionRequested', payload))

                # Update next run at using cron string
                # next_run_at = calculate_next_cron(schedule['cronExpression'])
                next_run_at = schedule.get("nextRunAt", now)
                schedule_updates.append((next_run_at, schedule['id']))

            if outbox_events and self.db and hasattr(self.db, "executemany"):
                await self.db.executemany(
                    "INSERT INTO outbox_events (event_name, payload) VALUES (?, ?)",
                    outbox_events
                )

            if schedule_updates and self.db and hasattr(self.db, "executemany"):
                await self.db.executemany(
                    "UPDATE report_schedules SET nextRunAt = ? WHERE id = ?",
                    schedule_updates
                )
        except Exception as e:
            logger.error(f"Error in ReportSchedulerWorker tick: {e}")
    async def tick(self):
        if self.db is None or not self.tenant_id:
            raise ValueError('Persisted scheduler session and tenant are required')
        from src.application.report_service import scheduler_tick
        return await scheduler_tick(self.db, self.tenant_id)
