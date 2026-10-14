from sqlalchemy import Column, String, DateTime, JSON, Integer, BigInteger, Index, ForeignKey, event
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from src.infrastructure.database import Base
from datetime import datetime, timezone
import uuid
import hashlib
import json


def stream_key(tenant, aggregate_type, aggregate_id):
    return hashlib.sha256(json.dumps([tenant, aggregate_type, aggregate_id], separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class OutboxStreamModel(Base):
    __tablename__ = 'python_outbox_streams'
    key = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    aggregate_type = Column(String, nullable=False)
    aggregate_id = Column(String, nullable=False)
    last_sequence = Column(BigInteger, nullable=False)


def allocate_sequence(connection, tenant, aggregate_type, aggregate_id):
    key = stream_key(tenant, aggregate_type, aggregate_id)
    insert = sqlite_insert if connection.dialect.name == 'sqlite' else pg_insert
    table = OutboxStreamModel.__table__
    value = connection.scalar(insert(table).values(key=key, tenant_id=tenant,
        aggregate_type=aggregate_type, aggregate_id=aggregate_id, last_sequence=1)
        .on_conflict_do_update(index_elements=['key'], set_={'last_sequence': table.c.last_sequence + 1})
        .returning(table.c.last_sequence))
    if value > 9_007_199_254_740_991:
        raise ValueError('Outbox sequence exceeds supported limit')
    return key, value

class OutboxEventModel(Base):
    __tablename__ = 'outbox_events'
    __table_args__ = (Index('ix_outbox_delivery_queue', 'status', 'next_attempt_at', 'created_at'),
                      Index('ix_outbox_aggregate_order', 'stream_key', 'sequence', unique=True))

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    aggregate_type = Column(String, nullable=False)
    aggregate_id = Column(String, nullable=False)
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    status = Column(String, nullable=False, default='PENDING', server_default='PENDING')
    attempts = Column(Integer, nullable=False, default=0, server_default='0')
    cycle_attempts = Column(Integer, nullable=False, default=0, server_default='0')
    version = Column(Integer, nullable=False, default=0, server_default='0')
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    lease_token = Column(String, nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    stream_key = Column(String, ForeignKey('python_outbox_streams.key'), nullable=True)
    sequence = Column(BigInteger, nullable=True)


@event.listens_for(OutboxEventModel, 'before_insert')
def assign_event_sequence(mapper, connection, target):
    try:
        json.dumps(target.payload, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Outbox payload must be finite JSON') from exc
    tenant = target.payload.get('tenantId') if isinstance(target.payload, dict) else None
    if isinstance(tenant, str) and tenant and target.aggregate_type and target.aggregate_id:
        # Allocation participates in the producer's existing business transaction.
        # A rollback also rolls back the counter, and per-stream locks serialize
        # inserts across API processes without trusting their wall clocks.
        target.stream_key, target.sequence = allocate_sequence(connection, tenant, target.aggregate_type, target.aggregate_id)
    else:
        target.status, target.last_error = 'DEAD_LETTER', 'UnassignedEvent'
