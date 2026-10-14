"""Run a dedicated delivery worker: python -m scripts.run_outbox_worker."""
import asyncio
import logging
import signal
from src.infrastructure.messaging.kafka_publisher import KafkaPublisher
from src.infrastructure.messaging.settings import OutboxSettings
from src.application.workers.outbox_worker import OutboxWorker
from src.infrastructure.database import engine


async def main():
    settings = OutboxSettings.from_env()
    publisher = KafkaPublisher.from_env(timeout=settings.publish_timeout)
    worker = OutboxWorker(publisher, settings=settings)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, lambda *_: loop.call_soon_threadsafe(stopped.set))
    try:
        await publisher.start()
        await worker.start()
        await stopped.wait()
    finally:
        try:
            await worker.stop()
        finally:
            try:
                await publisher.stop()
            finally:
                await engine.dispose()
                for signum, handler in previous.items():
                    signal.signal(signum, handler)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except Exception as exc:
        logging.error('Event delivery stopped (%s)', type(exc).__name__)
        raise SystemExit(1)
