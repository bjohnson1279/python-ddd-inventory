"""Durable connections, inbound receipts and independent destination deliveries."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, BigInteger, Boolean, JSON, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, event, select, update
from src.infrastructure.database import Base
from src.infrastructure.messaging.models import OutboxEventModel


class IntegrationConnection(Base):
    __tablename__ = 'python_integration_connections'
    __table_args__ = (UniqueConstraint('secret_ref'), UniqueConstraint('secret_digest'), CheckConstraint("provider IN ('SHOPIFY','WOOCOMMERCE','EVENT_SINK')"),
        CheckConstraint('version >= 0 AND last_number >= 0'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, ForeignKey('python_tenants.id'), nullable=False, index=True)
    name = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    origin = Column(String, nullable=True)
    location_id = Column(String, nullable=False)
    secret_ref = Column(String, nullable=False)
    secret_digest = Column(String, nullable=False)
    endpoint_ref = Column(String, nullable=True)
    topics = Column(JSON, nullable=False, default=list)
    active = Column(Boolean, nullable=False, default=True)
    version = Column(Integer, nullable=False, default=0)
    last_number = Column(BigInteger, nullable=False, default=0)


class IntegrationOrder(Base):
    __tablename__ = 'python_integration_orders'
    __table_args__ = (UniqueConstraint('connection_id', 'external_id'), UniqueConstraint('connection_id', 'delivery_id'),
        CheckConstraint("status IN ('RESERVED','FULFILLED','CANCELLED')"), CheckConstraint('version >= 0'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    connection_id = Column(String, ForeignKey('python_integration_connections.id'), nullable=False)
    external_id = Column(String, nullable=False)
    delivery_id = Column(String, nullable=False)
    request = Column(JSON, nullable=False)
    result = Column(JSON, nullable=False)
    status = Column(String, nullable=False, default='RESERVED')
    version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


class IntegrationReceipt(Base):
    __tablename__ = 'python_integration_receipts'
    __table_args__ = (UniqueConstraint('connection_id', 'delivery_id'),)
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    connection_id = Column(String, ForeignKey('python_integration_connections.id'), nullable=False)
    delivery_id = Column(String, nullable=False)
    order_id = Column(String, ForeignKey('python_integration_orders.id'), nullable=False)


class IntegrationDelivery(Base):
    __tablename__ = 'python_integration_deliveries'
    __table_args__ = (UniqueConstraint('connection_id', 'event_id'), UniqueConstraint('connection_id', 'number'),
        CheckConstraint("status IN ('PENDING','SENDING','DELIVERED','FAILED')"), CheckConstraint('number > 0 AND attempts >= 0 AND cycle_attempts >= 0 AND version >= 0'))
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    connection_id = Column(String, ForeignKey('python_integration_connections.id'), nullable=False)
    event_id = Column(String, ForeignKey('outbox_events.id'), nullable=False)
    number = Column(BigInteger, nullable=False)
    payload = Column(JSON, nullable=False)
    endpoint_ref = Column(String, nullable=False)
    secret_ref = Column(String, nullable=False)
    status = Column(String, nullable=False, default='PENDING')
    attempts = Column(Integer, nullable=False, default=0)
    cycle_attempts = Column(Integer, nullable=False, default=0)
    version = Column(Integer, nullable=False, default=0)
    token = Column(String, nullable=True)
    lease_until = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(String, nullable=True)
    external_id = Column(String, nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)


def enqueue_deliveries(connection, event_id, tenant, event_type, payload):
    """Called inside the producer transaction, also for Core financial inserts."""
    if event_type not in ('StockChanged', 'JournalEntryPosted'):
        return
    rows = connection.execute(select(IntegrationConnection.__table__).where(
        IntegrationConnection.tenant_id == tenant, IntegrationConnection.provider == 'EVENT_SINK',
        IntegrationConnection.active.is_(True))).mappings()
    for destination in rows:
        if event_type not in destination['topics']:
            continue
        number = connection.scalar(update(IntegrationConnection).where(IntegrationConnection.id == destination['id'],
            IntegrationConnection.tenant_id == tenant).values(last_number=IntegrationConnection.last_number + 1).returning(IntegrationConnection.last_number))
        connection.execute(IntegrationDelivery.__table__.insert().values(id=str(uuid.uuid4()), tenant_id=tenant,
            connection_id=destination['id'], event_id=event_id, number=number,
            payload={'eventId': event_id, 'tenantId': tenant, 'type': event_type, 'data': payload},
            endpoint_ref=destination['endpoint_ref'], secret_ref=destination['secret_ref'], status='PENDING', attempts=0, cycle_attempts=0, version=0))


@event.listens_for(OutboxEventModel, 'after_insert')
def capture_delivery(mapper, connection, row):
    tenant = row.payload.get('tenantId') if isinstance(row.payload, dict) else None
    if tenant:
        enqueue_deliveries(connection, row.id, tenant, row.event_type, row.payload)
