import asyncio
import logging
from sqlalchemy import delete
from sqlalchemy.future import select
from src.infrastructure.database import async_session
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.messaging.kafka_publisher import KafkaPublisher
from src.infrastructure.cache import DistributedCache

logger = logging.getLogger(__name__)

class OutboxWorker:
    def __init__(self, publisher: KafkaPublisher, poll_interval: float = 5.0):
        self.publisher = publisher
        self.poll_interval = poll_interval
        self._running = False

    async def start(self):
        self._running = True
        logger.info("Outbox worker started.")
        while self._running:
            try:
                await self._process_outbox()
            except Exception as e:
                logger.error(f"Error processing outbox: {e}")
            await asyncio.sleep(self.poll_interval)

    async def stop(self):
        self._running = False
        logger.info("Outbox worker stopped.")

    async def _process_outbox(self):
        async with async_session() as session:
            # In a real app we'd use SELECT FOR UPDATE SKIP LOCKED
            stmt = select(OutboxEventModel).order_by(OutboxEventModel.created_at.asc()).limit(50)
            result = await session.execute(stmt)
            events = result.scalars().all()

            if not events:
                return

            event_ids = []
            for event in events:
                # IMPORTANT: Keep network I/O sequential to preserve event ordering.
                await self.publisher.publish(
                    topic=f"inventory.{event.aggregate_type}.events",
                    key=event.aggregate_id,
                    message=event.payload
                )
                
                # Invalidate tier-2 cache sequentially as well
                await DistributedCache.invalidate(f"{event.aggregate_type}:{event.aggregate_id}")
                
                event_ids.append(event.id)
            
            # Perform bulk delete to eliminate N+1 query issue
            delete_stmt = delete(OutboxEventModel).where(OutboxEventModel.id.in_(event_ids))
            await session.execute(delete_stmt)
            await session.commit()
