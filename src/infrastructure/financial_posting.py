"""Posting executes on the producer's existing database connection/transaction."""
import uuid
from datetime import datetime, timezone
from sqlalchemy import select, update, func, event
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from src.domain.financial_accounting import validate_lines, movement_facts, checked_cents
from src.domain.product.layer_costing import utc
from src.infrastructure.financial_models import LedgerStateModel as State, JournalModel as Journal, JournalLineModel as Line, FinancialSourceModel as Source
from src.infrastructure.stock_models import StockMovementModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.messaging.models import OutboxEventModel, allocate_sequence


def database_now(connection):
    return utc(connection.scalar(select(func.clock_timestamp() if connection.dialect.name == 'postgresql' else func.current_timestamp())))


def ensure_state(connection, tenant):
    insert = sqlite_insert if connection.dialect.name == 'sqlite' else pg_insert
    connection.execute(insert(State).values(tenant_id=tenant, active=False, last_number=0, version=0)
        .on_conflict_do_nothing(index_elements=['tenant_id']))
    return connection.execute(select(State.__table__).where(State.tenant_id == tenant)).mappings().one()


def post_journal(connection, tenant, *, date, description, actor, lines, kind='MANUAL',
                 source_id=None, reference=None, reversal_of=None):
    total = validate_lines(lines)
    now, state = database_now(connection), ensure_state(connection, tenant)
    if not state['active']:
        raise ValueError('Activate financial accounting before posting journals')
    if date > now.date() or date < utc(state['activated_at']).date() or state['closed_through'] and date <= state['closed_through']:
        raise ValueError('Journal date is outside the open accounting period')
    number = connection.scalar(update(State).where(State.tenant_id == tenant)
        .values(last_number=State.last_number + 1, version=State.version + 1).returning(State.last_number))
    checked_cents(number)
    identifier = str(uuid.uuid4())
    snapshot = [{'id': str(uuid.uuid4()), **line, 'memo': line.get('memo') or ''} for line in lines]
    connection.execute(Journal.__table__.insert().values(id=identifier, tenant_id=tenant, number=number,
        date=date, posted_at=now, description=description, actor_id=actor, method=state['method'],
        kind=kind, source_id=source_id, reference_id=reference, reversal_of=reversal_of,
        lines=snapshot, line_count=len(snapshot), total_debits=total, total_credits=total))
    connection.execute(Line.__table__.insert(), [{'id': line['id'], 'journal_id': identifier,
        'tenant_id': tenant, 'position': position, 'account_code': line['accountCode'],
        'side': line['type'], 'amount_cents': line['amountCents'], 'memo': line['memo']}
        for position, line in enumerate(snapshot)])
    payload = {'tenantId': tenant, 'journalId': identifier, 'number': number, 'date': date.isoformat(),
        'description': description, 'method': state['method'], 'kind': kind, 'actorId': actor,
        'referenceId': reference, 'sourceId': source_id, 'lines': snapshot}
    key, sequence = allocate_sequence(connection, tenant, 'Journal', identifier)
    event_id = str(uuid.uuid4())
    connection.execute(OutboxEventModel.__table__.insert().values(id=event_id,
        aggregate_type='Journal', aggregate_id=identifier, event_type='JournalEntryPosted', payload=payload,
        created_at=now, status='PENDING', attempts=0, cycle_attempts=0, version=0, stream_key=key, sequence=sequence))
    from src.infrastructure.integration_models import enqueue_deliveries
    enqueue_deliveries(connection, event_id, tenant, 'JournalEntryPosted', payload)
    return identifier


@event.listens_for(StockMovementModel, 'after_insert')
def capture_inventory_posting(mapper, connection, movement):
    state = ensure_state(connection, movement.tenant_id)
    now = database_now(connection)
    result = movement.result
    receipt_value = result.get('receiptValueCents')
    if receipt_value is None and movement.operation == 'receive':
        ids = result.get('costLayerIds') or [result.get('costLayerId')]
        values = list(connection.scalars(select(CostLayerModel.initial_value_cents).where(
            CostLayerModel.tenant_id == movement.tenant_id, CostLayerModel.id.in_(ids))))
        if values and len(values) == len(ids) and all(v is not None for v in values):
            receipt_value = sum(values)
    facts = movement_facts(movement.operation, movement.amount, result, receipt_value)
    source_status = 'PRE_ACTIVATION' if not state['active'] else 'UNPRICED' if facts['unknown'] else 'NO_EFFECT'
    postings = [{'accountCode': account, 'type': side, 'amountCents': amount, 'memo': movement.operation}
        for account, side, amount in facts['postings'] if amount is not None and amount > 0]
    amount = None if facts['unknown'] else sum(p['amountCents'] for p in postings if p['type'] == 'debit')
    snapshot = {'operation': movement.operation, 'quantity': movement.amount, 'sku': movement.sku,
        'locationId': movement.location_id, 'request': movement.request, 'result': result}
    if movement.operation == 'serial-return' and result.get('sourceLayerId'):
        parent = connection.scalar(select(Source.movement_id).where(Source.tenant_id == movement.tenant_id, Source.kind == 'SALE',
            Source.facts['result']['sourceCostLayerId'].as_string() == result['sourceLayerId']))
        if parent:
            snapshot['result'] = {**result, 'sourceMovementId': parent}
    connection.execute(Source.__table__.insert().values(movement_id=movement.id, tenant_id=movement.tenant_id,
        date=now.date(), kind=facts['kind'], status=source_status, facts=snapshot, amount_cents=amount, version=0))
    if state['active'] and not facts['unknown'] and postings:
        identifier = post_journal(connection, movement.tenant_id, date=now.date(), description=movement.operation,
            actor=movement.actor_id, lines=postings, kind='INVENTORY', source_id=movement.id, reference=movement.mutation_id)
        connection.execute(update(Source).where(Source.movement_id == movement.id, Source.tenant_id == movement.tenant_id)
            .values(status='POSTED', journal_id=identifier))
