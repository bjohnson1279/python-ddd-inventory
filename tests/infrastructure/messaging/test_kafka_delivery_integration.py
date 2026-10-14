import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.infrastructure.database import Base
from src.infrastructure.auth.identity import TenantModel
from src.infrastructure.messaging.models import OutboxEventModel as Event
from src.infrastructure.messaging.kafka_publisher import KafkaPublisher
from src.infrastructure.messaging.settings import OutboxSettings
from src.application.workers.outbox_worker import OutboxWorker


@pytest.mark.asyncio
async def test_real_kafka_acknowledgment_headers_order_and_published_retention(tmp_path):
    broker = os.getenv('OUTBOX_KAFKA_TEST_BOOTSTRAP_SERVERS')
    if not broker:
        pytest.skip('Set OUTBOX_KAFKA_TEST_BOOTSTRAP_SERVERS to a disposable local Kafka broker')
    if not re.fullmatch(r'(127\.0\.0\.1|localhost):\d{2,5}', broker):
        raise ValueError('Kafka integration tests require a disposable local broker')
    from aiokafka import AIOKafkaConsumer
    from aiokafka.admin import AIOKafkaAdminClient, NewTopic
    prefix = 'event_test_' + uuid.uuid4().hex
    topic = prefix + '.Stock.events'
    admin = AIOKafkaAdminClient(bootstrap_servers=broker)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])
    finally:
        await admin.close()
    engine = create_async_engine('sqlite+aiosqlite:///' + (tmp_path / 'kafka.sqlite').as_posix())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with sessions() as session:
        session.add(TenantModel(id='synthetic-store', name='Synthetic test store'))
        for identifier in ('event-1', 'event-2'):
            session.add(Event(id=identifier, aggregate_type='Stock', aggregate_id='stock', event_type='StockChanged',
                payload={'tenantId': 'synthetic-store', 'quantity': 2}, created_at=datetime.now(timezone.utc)))
        await session.commit()
    publisher = KafkaPublisher(broker, timeout=20)
    consumer = AIOKafkaConsumer(topic, bootstrap_servers=broker, group_id=prefix, auto_offset_reset='earliest', enable_auto_commit=False)
    try:
        await publisher.start()
        await consumer.start()
        worker = OutboxWorker(publisher, session_factory=sessions, settings=OutboxSettings(topic_prefix=prefix))
        assert await worker._process_outbox() == 2
        records = [await asyncio.wait_for(consumer.getone(), 20) for _ in range(2)]
        values = [json.loads(record.value) for record in records]
        assert [value['eventId'] for value in values] == ['event-1', 'event-2']
        assert [value['sequence'] for value in values] == [1, 2]
        assert all(value['schemaVersion'] == 1 and value['tenantId'] == 'synthetic-store' for value in values)
        assert all(json.loads(record.key) == ['synthetic-store', 'Stock', 'stock'] for record in records)
        assert dict(records[0].headers)['event-id'] == b'event-1'
        assert records[1].offset > records[0].offset
        async with sessions() as session:
            assert (await session.get(Event, 'event-1')).status == 'PUBLISHED'
            assert (await session.get(Event, 'event-2')).status == 'PUBLISHED'
        assert await worker._process_outbox() == 0
    finally:
        await consumer.stop()
        await publisher.stop()
        await engine.dispose()
