import hashlib
import json
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, String, Integer, Float, Boolean, JSON, DateTime, select
from src.infrastructure.database import Base


class ComplianceLedgerEntryModel(Base):
    __tablename__ = 'compliance_ledgers'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    sequence_number = Column(Integer, nullable=False)
    event_type = Column(String, nullable=False)
    payload = Column(String, nullable=False)
    timestamp = Column(String, nullable=False)
    previous_hash = Column(String, nullable=False)
    hash = Column(String, nullable=False)


class RfidTagModel(Base):
    __tablename__ = 'python_rfid_tags'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    epc = Column(String, nullable=False)
    sku = Column(String, nullable=False)
    serial_number = Column(String, nullable=True)
    status = Column(String, nullable=False, default='ACTIVE')


class WarehouseLocationModel(Base):
    __tablename__ = 'python_warehouse_locations'
    id = Column(String, primary_key=True)
    tenant_id = Column(String, nullable=False, index=True)
    warehouse_id = Column(String, nullable=False)
    zone = Column(String, nullable=False, default='A')
    aisle = Column(String, nullable=False, default='01')
    rack = Column(String, nullable=False, default='01')
    shelf = Column(String, nullable=False, default='01')
    bin = Column(String, nullable=False, default='01')
    max_weight_grams = Column(Integer, nullable=False, default=100000)
    max_volume_cubic_meters = Column(Float, nullable=False, default=100.0)


class WebhookSubscriptionModel(Base):
    __tablename__ = 'python_webhook_subscriptions'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    target_url = Column(String, nullable=False)
    secret = Column(String, nullable=False)
    event_types = Column(JSON, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)


class WebhookDeliveryModel(Base):
    __tablename__ = 'python_webhook_deliveries'
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, nullable=False, index=True)
    subscription_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    status = Column(String, nullable=False, default='FAILED')
    attempts = Column(Integer, nullable=False, default=1)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))


_CACHE_STATE: dict[str, dict] = {}


def _tenant_cache(tenant_id: str) -> dict:
    if tenant_id not in _CACHE_STATE:
        _CACHE_STATE[tenant_id] = {'hits': 0, 'misses': 0, 'invalidations': 0, 'keys': set()}
    return _CACHE_STATE[tenant_id]


def clear_tenant_cache(tenant_id: str) -> None:
    _CACHE_STATE[tenant_id] = {'hits': 0, 'misses': 0, 'invalidations': 0, 'keys': set()}


def record_cache_read(tenant_id: str, cache_key: str = 'inventory:list') -> None:
    state = _tenant_cache(tenant_id)
    if cache_key in state['keys']:
        state['hits'] += 1
    else:
        state['misses'] += 1
        state['keys'].add(cache_key)


def record_cache_invalidation(tenant_id: str) -> None:
    state = _tenant_cache(tenant_id)
    state['invalidations'] += 1
    state['keys'].clear()


def get_tenant_cache_stats(tenant_id: str) -> dict:
    state = _tenant_cache(tenant_id)
    total = state['hits'] + state['misses']
    return {
        'hits': state['hits'],
        'misses': state['misses'],
        'hitRatio': round(state['hits'] / total, 4) if total else 0.0,
        'invalidations': state['invalidations'],
        'activeKeysCount': len(state['keys']),
    }


def compute_ledger_hash(tenant_id: str, sequence_number: int, event_type: str,
                        timestamp: str, previous_hash: str, payload_str: str) -> str:
    raw = f'{tenant_id}|{sequence_number}|{event_type}|{timestamp}|{previous_hash}|{payload_str}'
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


async def record_stock_compliance_and_webhooks(session, tenant_id: str, actor_id: str,
                                               operation: str, sku: str, location_id: str,
                                               amount: int, result: dict) -> None:
    record_cache_invalidation(tenant_id)
    event_map = {
        'receive': 'StockReceived',
        'dispatch': 'StockDispatched',
        'allocate': 'StockAllocated',
        'release-allocation': 'StockAllocationReleased',
        'fulfill-allocation': 'StockAllocationFulfilled',
    }
    event_type = event_map.get(operation, 'StockChanged')
    with session.no_autoflush:
        last = await session.scalar(
            select(ComplianceLedgerEntryModel)
            .where(ComplianceLedgerEntryModel.tenant_id == tenant_id)
            .order_by(ComplianceLedgerEntryModel.sequence_number.desc())
            .limit(1)
        )
        subscriptions = list(await session.scalars(
            select(WebhookSubscriptionModel).where(
                WebhookSubscriptionModel.tenant_id == tenant_id,
                WebhookSubscriptionModel.is_active.is_(True),
            )
        ))
    seq = (last.sequence_number + 1) if last else 1
    prev_hash = last.hash if last else ('0' * 64)
    now = datetime.now(timezone.utc)
    if last and last.timestamp:
        try:
            last_dt = datetime.fromisoformat(last.timestamp.replace('Z', '+00:00')).astimezone(timezone.utc)
            if now <= last_dt:
                now = last_dt + timedelta(milliseconds=1)
        except ValueError:
            pass
    ts_str = now.isoformat().replace('+00:00', 'Z')
    payload_obj = {
        'sku': sku,
        'locationId': location_id,
        'quantity': amount,
        'operation': operation,
        'actorId': actor_id,
        'newQuantity': result.get('quantity'),
    }
    payload_str = json.dumps(payload_obj, separators=(',', ':'), sort_keys=True)
    digest = compute_ledger_hash(tenant_id, seq, event_type, ts_str, prev_hash, payload_str)
    session.add(ComplianceLedgerEntryModel(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        sequence_number=seq,
        event_type=event_type,
        payload=payload_str,
        timestamp=ts_str,
        previous_hash=prev_hash,
        hash=digest,
    ))

    matching_events = {event_type, 'StockChanged', f'stock.{operation}'}
    if operation == 'receive':
        matching_events.add('stock.received')
    for sub in subscriptions:
        sub_events = sub.event_types if isinstance(sub.event_types, list) else []
        matched = next((ev for ev in sub_events if ev in matching_events), None)
        if matched or not sub_events:
            session.add(WebhookDeliveryModel(
                id=str(uuid.uuid4()),
                tenant_id=tenant_id,
                subscription_id=sub.id,
                event_type=matched or event_type,
                status='FAILED',
                attempts=1,
                payload=payload_obj,
            ))
