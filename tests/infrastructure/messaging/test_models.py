import pytest
import hashlib
import json
from unittest.mock import MagicMock
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.infrastructure.database import Base
from src.infrastructure.messaging.models import (
    stream_key,
    allocate_sequence,
    OutboxStreamModel,
    OutboxEventModel,
)


def test_stream_key_deterministic():
    expected_payload = json.dumps(["tenant1", "Order", "order-123"], separators=(',', ':'), ensure_ascii=False).encode()
    expected_hash = hashlib.sha256(expected_payload).hexdigest()
    assert stream_key("tenant1", "Order", "order-123") == expected_hash


def test_stream_key_unicode():
    key = stream_key("tenant-café", "Order-📦", "id-🚀")
    expected_payload = json.dumps(["tenant-café", "Order-📦", "id-🚀"], separators=(',', ':'), ensure_ascii=False).encode()
    assert key == hashlib.sha256(expected_payload).hexdigest()


def test_allocate_sequence_sqlite():
    conn = MagicMock()
    conn.dialect.name = 'sqlite'
    conn.scalar.return_value = 1
    key, val = allocate_sequence(conn, "t1", "Order", "123")
    assert key == stream_key("t1", "Order", "123")
    assert val == 1
    assert conn.scalar.called


def test_allocate_sequence_postgres():
    conn = MagicMock()
    conn.dialect.name = 'postgresql'
    conn.scalar.return_value = 42
    key, val = allocate_sequence(conn, "t1", "Order", "123")
    assert key == stream_key("t1", "Order", "123")
    assert val == 42
    assert conn.scalar.called


def test_allocate_sequence_exceeds_limit():
    conn = MagicMock()
    conn.dialect.name = 'sqlite'
    conn.scalar.return_value = 9_007_199_254_740_992
    with pytest.raises(ValueError, match="Outbox sequence exceeds supported limit"):
        allocate_sequence(conn, "t1", "Order", "123")


def test_outbox_stream_model_attributes():
    stream = OutboxStreamModel(
        key="test-key",
        tenant_id="t1",
        aggregate_type="Order",
        aggregate_id="123",
        last_sequence=5,
    )
    assert stream.key == "test-key"
    assert stream.tenant_id == "t1"
    assert stream.aggregate_type == "Order"
    assert stream.aggregate_id == "123"
    assert stream.last_sequence == 5


def test_outbox_event_model_attributes():
    event = OutboxEventModel(
        aggregate_type="Order",
        aggregate_id="123",
        event_type="OrderCreated",
        payload={"tenantId": "t1"},
        status="PENDING",
        attempts=0,
        cycle_attempts=0,
        version=0,
    )
    assert event.aggregate_type == "Order"
    assert event.aggregate_id == "123"
    assert event.event_type == "OrderCreated"
    assert event.payload == {"tenantId": "t1"}
    assert event.status == "PENDING"
    assert event.attempts == 0
    assert event.cycle_attempts == 0
    assert event.version == 0


@pytest.mark.asyncio
async def test_assign_event_sequence_success():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        evt1 = OutboxEventModel(
            aggregate_type="Order",
            aggregate_id="100",
            event_type="OrderCreated",
            payload={"tenantId": "store-1", "amount": 50},
        )
        session.add(evt1)
        await session.commit()
        await session.refresh(evt1)

        assert evt1.stream_key == stream_key("store-1", "Order", "100")
        assert evt1.sequence == 1
        assert evt1.status == "PENDING"
        assert evt1.last_error is None

        evt2 = OutboxEventModel(
            aggregate_type="Order",
            aggregate_id="100",
            event_type="OrderUpdated",
            payload={"tenantId": "store-1", "amount": 75},
        )
        session.add(evt2)
        await session.commit()
        await session.refresh(evt2)

        assert evt2.stream_key == stream_key("store-1", "Order", "100")
        assert evt2.sequence == 2
        assert evt2.status == "PENDING"

    await engine.dispose()


@pytest.mark.asyncio
async def test_assign_event_sequence_invalid_json():
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        evt = OutboxEventModel(
            aggregate_type="Order",
            aggregate_id="100",
            event_type="OrderCreated",
            payload={"tenantId": "store-1", "invalid_num": float('nan')},
        )
        session.add(evt)
        with pytest.raises(ValueError, match="Outbox payload must be finite JSON"):
            await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,agg_type,agg_id", [
    (None, "Order", "100"),
    ("not-a-dict", "Order", "100"),
    ({}, "Order", "100"),
    ({"tenantId": None}, "Order", "100"),
    ({"tenantId": 123}, "Order", "100"),
    ({"tenantId": ""}, "Order", "100"),
    ({"tenantId": "store-1"}, "", "100"),
    ({"tenantId": "store-1"}, "Order", ""),
])
async def test_assign_event_sequence_unassigned_dead_letter(payload, agg_type, agg_id):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async with sessions() as session:
        evt = OutboxEventModel(
            aggregate_type=agg_type,
            aggregate_id=agg_id,
            event_type="TestEvent",
            payload=payload,
        )
        session.add(evt)
        await session.commit()
        await session.refresh(evt)

        assert evt.status == "DEAD_LETTER"
        assert evt.last_error == "UnassignedEvent"
        assert evt.stream_key is None
        assert evt.sequence is None

    await engine.dispose()
