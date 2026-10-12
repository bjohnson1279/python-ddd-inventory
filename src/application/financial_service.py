import uuid
from datetime import date
from sqlalchemy import select, update, func, or_, and_
from sqlalchemy.exc import IntegrityError
from src.application.costing_service import locked_config
from src.application.stock_service import StockConflict
from src.domain.financial_accounting import ACCOUNTS, checked_cents, pair
from src.domain.product.layer_costing import utc
from src.infrastructure.financial_models import LedgerStateModel as State, JournalModel as Journal, JournalLineModel as Line, FinancialSourceModel as Source, FinancialDocumentModel as Document, DocumentSourceModel as Link, SettlementModel as Settlement, FinancialCreditModel as Credit
from src.infrastructure.financial_posting import ensure_state, post_journal, database_now
from src.infrastructure.transfer_models import InventoryActionModel as Action, TransferModel
from src.infrastructure.stock_models import StockModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.serial_kit_models import SerialModel
from src.infrastructure.procurement_models import SupplierModel, PurchaseOrderModel


def journal_dto(row):
    return {'id': row.id, 'tenantId': row.tenant_id, 'number': row.number, 'date': row.date.isoformat(),
        'description': row.description, 'referenceId': row.reference_id, 'method': row.method.lower(),
        'actorId': row.actor_id, 'kind': row.kind, 'sourceId': row.source_id, 'reversalOf': row.reversal_of,
        'postedAt': utc(row.posted_at).isoformat(), 'totalDebitsCents': row.total_debits, 'totalCreditsCents': row.total_credits,
        'lines': [{**line, 'account': {'code': line['accountCode'], 'name': ACCOUNTS[line['accountCode']][0],
            'category': ACCOUNTS[line['accountCode']][1]}} for line in row.lines]}


def source_dto(row):
    return {'id': row.movement_id, 'kind': row.kind, 'status': row.status, 'date': row.date.isoformat(),
        'amountCents': row.amount_cents, 'journalId': row.journal_id, 'version': row.version, 'facts': row.facts}


def document_dto(row):
    net = row.amount_cents - row.credited_cents
    paid = row.settled_cents - row.refunded_cents
    outstanding, refund = max(0, net - paid), max(0, paid - net)
    return {'id': row.id, 'kind': row.kind, 'reference': row.reference, 'counterparty': row.counterparty,
        'supplierId': row.supplier_id, 'date': row.date.isoformat(), 'method': row.method,
        'amountCents': row.amount_cents, 'settledCents': row.settled_cents,
        'creditedCents': row.credited_cents, 'refundedCents': row.refunded_cents, 'netAmountCents': net,
        'outstandingCents': outstanding, 'refundDueCents': refund,
        'status': 'OPEN' if outstanding else 'REFUND_DUE' if refund else 'SETTLED', 'version': row.version, 'journalId': row.journal_id}


async def inventory_snapshot(session, tenant):
    stocks = list(await session.scalars(select(StockModel).where(StockModel.tenant_id == tenant)))
    layers = list(await session.scalars(select(CostLayerModel).where(CostLayerModel.tenant_id == tenant, CostLayerModel.remaining_quantity > 0)))
    grouped = {}
    for layer in layers:
        grouped.setdefault(layer.stock_id, []).append(layer)
    on_hand, transit, problems = 0, 0, []
    for stock in stocks:
        owned = grouped.get(stock.id, [])
        if sum(l.remaining_quantity for l in owned) != stock.quantity or any(l.remaining_value_cents is None for l in owned):
            problems.append({'sku': stock.sku, 'locationId': stock.location_id, 'reason': 'Unpriced or inconsistent on-hand stock'})
        else:
            on_hand += sum(l.remaining_value_cents for l in owned)
    for transfer in await session.scalars(select(TransferModel).where(TransferModel.tenant_id == tenant, TransferModel.status == 'DISPATCHED')):
        for item in transfer.items:
            values = [batch['costCents'] for batch in item['batches']]
            if not values or any(v is None for v in values):
                problems.append({'sku': item['sku'], 'reason': 'Unpriced or inconsistent transfer'})
            else:
                transit += sum(values)
    for serial in await session.scalars(select(SerialModel).where(SerialModel.tenant_id == tenant, SerialModel.status == 'transferred')):
        if serial.cost_cents is None:
            problems.append({'sku': serial.sku, 'reason': 'Unpriced serial in transit'})
        else:
            transit += serial.cost_cents
    checked_cents(on_hand + transit)
    return {'onHandValueCents': on_hand if not problems else None, 'inTransitValueCents': transit if not problems else None,
        'complete': not problems, 'problems': problems}


class FinancialService:
    def __init__(self, session, tenant, actor):
        self.session, self.tenant, self.actor = session, tenant, actor

    async def owned(self, model, identifier):
        column = model.movement_id if model is Source else model.id
        row = await self.session.scalar(select(model).where(column == identifier, model.tenant_id == self.tenant))
        if row is None:
            raise LookupError('Accounting record not found')
        return row

    async def state(self):
        await self.session.run_sync(lambda session: ensure_state(session.connection(), self.tenant))
        return await self.session.scalar(select(State).where(State.tenant_id == self.tenant).execution_options(populate_existing=True))

    async def status(self):
        state = await self.session.scalar(select(State).where(State.tenant_id == self.tenant))
        pending = await self.session.scalar(select(func.count()).select_from(Source).where(Source.tenant_id == self.tenant, Source.status == 'UNPRICED'))
        return {'active': bool(state and state.active), 'version': state.version if state else 0,
            'method': state.method if state else None, 'lastJournalNumber': state.last_number if state else 0,
            'closedThrough': state.closed_through.isoformat() if state and state.closed_through else None,
            'activatedAt': utc(state.activated_at).isoformat() if state and state.activated_at else None, 'unresolvedSources': pending}

    async def execute(self, operation, payload, key):
        try:
            config = await locked_config(self.session, self.tenant)
            self.config, self.current = config, await self.state()
            for field, model in (('journalId', Journal), ('sourceId', Source), ('documentId', Document)):
                if payload.get(field):
                    await self.owned(model, payload[field])
            fingerprint = {'operation': operation, **payload}
            prior = await self.session.scalar(select(Action).where(Action.tenant_id == self.tenant, Action.mutation_id == 'finance:' + key))
            if prior:
                if prior.actor_id != self.actor or prior.request != fingerprint:
                    raise StockConflict('Mutation ID was already used for another accounting request')
                result = prior.result
                await self.session.rollback()
                return result
            if operation != 'activate' and not self.current.active:
                raise StockConflict('Activate financial accounting first')
            result = await getattr(self, operation)(payload)
            self.session.add(Action(tenant_id=self.tenant, mutation_id='finance:' + key, actor_id=self.actor, request=fingerprint, result=result))
            await self.session.commit()
            return result
        except IntegrityError as exc:
            await self.session.rollback()
            raise StockConflict('Concurrent or duplicate accounting command; repeat the original mutation ID') from exc
        except Exception:
            await self.session.rollback()
            raise

    async def post(self, data, kind, *, source_id=None, reversal_of=None):
        identifier = await self.session.run_sync(lambda session: post_journal(session.connection(), self.tenant,
            date=date.fromisoformat(data['date']), description=data['description'], actor=self.actor,
            lines=data['lines'], kind=kind, source_id=source_id, reference=data.get('referenceId'), reversal_of=reversal_of))
        return journal_dto(await self.owned(Journal, identifier))

    async def post_pairs(self, postings, date_string, description, kind, reference):
        lines = [{'accountCode': account, 'type': side, 'amountCents': amount, 'memo': description}
            for account, side, amount in postings if amount > 0]
        if not lines:
            return None
        result = await self.post({'date': date_string, 'description': description, 'referenceId': reference, 'lines': lines}, kind)
        return result['id']

    async def activate(self, data):
        if self.config.currency != 'USD' or self.config.accounting_method not in ('CASH', 'ACCRUAL'):
            raise StockConflict('Financial accounting requires USD and a supported accounting method')
        if self.current.active or self.current.version != data['expectedVersion']:
            raise StockConflict('Financial accounting is already active or its version changed')
        snapshot = await inventory_snapshot(self.session, self.tenant)
        if not snapshot['complete']:
            raise StockConflict('Price and reconcile on-hand/in-transit stock before activating accounting')
        now = await self.session.run_sync(lambda session: database_now(session.connection()))
        self.current.active, self.current.activated_at, self.current.method = True, now, self.config.accounting_method
        self.current.version += 1
        await self.session.flush()
        postings = [('1200', 'debit', snapshot['onHandValueCents']), ('1210', 'debit', snapshot['inTransitValueCents']),
            ('3000', 'credit', snapshot['onHandValueCents'] + snapshot['inTransitValueCents'])]
        journal = await self.post_pairs(postings, now.date().isoformat(), data['reason'], 'OPENING', 'inventory-opening')
        await self.session.refresh(self.current)
        return {**await self.status(), 'openingJournalId': journal, 'openingInventory': snapshot}

    async def create_journal(self, data):
        if data['method'].upper() != self.current.method:
            raise StockConflict('Journal method must match the active financial accounting method')
        return await self.post(data, 'MANUAL')

    async def reverse(self, data):
        original = await self.owned(Journal, data['journalId'])
        if date.fromisoformat(data['date']) < original.date:
            raise ValueError('Reversal cannot precede the original journal')
        if original.kind != 'MANUAL':
            raise StockConflict('Use the business workflow to correct inventory, document or settlement journals')
        if await self.session.scalar(select(Journal.id).where(Journal.tenant_id == self.tenant, Journal.reversal_of == original.id)):
            raise StockConflict('Journal has already been reversed')
        lines = [{k: v for k, v in line.items() if k != 'id'} for line in original.lines]
        for line in lines:
            line['type'] = 'credit' if line['type'] == 'debit' else 'debit'
        return await self.post({**data, 'description': data['reason'], 'referenceId': original.id, 'lines': lines}, 'REVERSAL', reversal_of=original.id)

    async def resolve(self, data):
        source = await self.owned(Source, data['sourceId'])
        if date.fromisoformat(data['date']) < source.date:
            raise ValueError('Resolution cannot precede its stock source')
        if source.status != 'UNPRICED' or source.version != data['expectedVersion']:
            raise StockConflict('Only a current unresolved financial source can be resolved')
        result = await self.post({**data, 'description': data['reason']}, 'SOURCE_RESOLUTION', source_id=source.movement_id)
        source.status, source.journal_id, source.version = 'RESOLVED', result['id'], source.version + 1
        credits = sum(l['amountCents'] for l in data['lines'] if l['accountCode'] == '2100' and l['type'] == 'credit')
        debits = sum(l['amountCents'] for l in data['lines'] if l['accountCode'] == '2100' and l['type'] == 'debit')
        source.amount_cents = credits - debits if source.kind == 'RECEIPT' else result['totalDebitsCents']
        if source.kind == 'RECEIPT' and source.amount_cents <= 0:
            raise ValueError('Receipt resolution must establish a positive goods-received liability')
        return {**source_dto(source), 'journal': result}

    async def create_document(self, data):
        ids = data['sourceIds']
        if len(ids) != len(set(ids)):
            raise ValueError('Each stock source must occur once')
        sources = [await self.owned(Source, identifier) for identifier in ids]
        expected = 'SALE' if data['kind'] == 'SALE' else 'RECEIPT'
        if any(s.kind != expected or s.status == 'PRE_ACTIVATION' for s in sources):
            raise StockConflict('Commercial documents require current, matching stock sources')
        if await self.session.scalar(select(Link.source_id).where(Link.tenant_id == self.tenant, Link.source_id.in_(ids)).limit(1)):
            raise StockConflict('A stock source has already been assigned to a commercial document')
        if data['kind'] == 'PURCHASE':
            if any(s.status == 'UNPRICED' or s.amount_cents is None for s in sources) or sum(s.amount_cents for s in sources) != data['amountCents']:
                raise StockConflict('Purchase invoice must match the recorded receipt liability')
            supplier = await self.owned(SupplierModel, data['supplierId']) if data.get('supplierId') else None
            if supplier is None or not supplier.is_active:
                raise ValueError('Purchase invoice requires an active owned supplier')
            for source in sources:
                reference = source.facts['result'].get('reference')
                if isinstance(reference, dict):
                    reference = reference.get('purchaseOrderId')
                po = await self.session.scalar(select(PurchaseOrderModel).where(PurchaseOrderModel.tenant_id == self.tenant, PurchaseOrderModel.id == reference)) if reference else None
                if po and po.supplier_id != supplier.id:
                    raise StockConflict('Receipt purchase order belongs to another supplier')
        elif data.get('supplierId'):
            raise ValueError('Sales documents do not accept supplier IDs')
        now = await self.session.run_sync(lambda session: database_now(session.connection()))
        effective = date.fromisoformat(data['date'])
        if any(s.date > effective for s in sources):
            raise ValueError('Document posting cannot precede its linked stock sources')
        if effective > now.date() or effective < utc(self.current.activated_at).date() or self.current.closed_through and effective <= self.current.closed_through:
            raise ValueError('Document date is outside the open accounting period')
        row = Document(id=str(uuid.uuid4()), tenant_id=self.tenant, kind=data['kind'], reference=data['reference'],
            counterparty=data['counterparty'], supplier_id=data.get('supplierId'), date=effective,
            method=self.current.method, amount_cents=data['amountCents'], settled_cents=0, credited_cents=0, refunded_cents=0, version=0)
        if self.current.method == 'ACCRUAL':
            accounts = ('1100', '4000') if row.kind == 'SALE' else ('2100', '2000')
            row.journal_id = await self.post_pairs(pair(*accounts, row.amount_cents), data['date'], 'Commercial document ' + row.reference, 'DOCUMENT', row.id)
        self.session.add(row)
        await self.session.flush()
        self.session.add_all([Link(tenant_id=self.tenant, source_id=source.movement_id, document_id=row.id) for source in sources])
        return {**document_dto(row), 'sourceIds': ids}

    async def settle(self, data):
        row = await self.owned(Document, data['documentId'])
        if date.fromisoformat(data['date']) < row.date:
            raise ValueError('Settlement cannot precede the commercial document')
        if row.version != data['expectedVersion'] or data['amountCents'] > document_dto(row)['outstandingCents']:
            raise StockConflict('Document version changed or settlement exceeds the outstanding amount')
        accounts = ('1000', '1100' if row.method == 'ACCRUAL' else '4000') if row.kind == 'SALE' else (
            '2000' if row.method == 'ACCRUAL' else '2100', '1000')
        identifier = await self.post_pairs(pair(*accounts, data['amountCents']), data['date'], data['reason'], 'SETTLEMENT', data['reference'])
        self.session.add(Settlement(tenant_id=self.tenant, document_id=row.id, reference=data['reference'],
            date=date.fromisoformat(data['date']), amount_cents=data['amountCents'], journal_id=identifier))
        row.settled_cents += data['amountCents']
        row.version += 1
        return {**document_dto(row), 'settlementJournalId': identifier}

    async def apply_credit(self, data):
        row = await self.owned(Document, data['documentId'])
        source = await self.owned(Source, data['sourceId'])
        expected = 'CUSTOMER_RETURN' if row.kind == 'SALE' else 'SUPPLIER_RETURN'
        if row.version != data['expectedVersion'] or source.kind != expected or source.status == 'PRE_ACTIVATION':
            raise StockConflict('Credit requires a current document and a matching recorded return')
        parent = source.facts['result'].get('sourceMovementId')
        if not parent or not await self.session.scalar(select(Link.source_id).where(Link.tenant_id == self.tenant,
            Link.source_id == parent, Link.document_id == row.id)):
            raise StockConflict('Return does not belong to this commercial document')
        effective = date.fromisoformat(data['date'])
        now = await self.session.run_sync(lambda session: database_now(session.connection()))
        if effective < max(source.date, row.date, utc(self.current.activated_at).date()) or effective > now.date() or self.current.closed_through and effective <= self.current.closed_through:
            raise ValueError('Credit date is outside its source and open accounting period')
        if data['amountCents'] > row.amount_cents - row.credited_cents:
            raise StockConflict('Credit exceeds the original document balance')
        postings = []
        if row.kind == 'PURCHASE':
            if source.status == 'UNPRICED' or not source.journal_id:
                raise StockConflict('Resolve the supplier return claim before applying its credit')
            journal = await self.owned(Journal, source.journal_id)
            claim = sum(l['amountCents'] * (1 if l['type'] == 'debit' else -1) for l in journal.lines if l['accountCode'] == '1300')
            used = await self.session.scalar(select(func.coalesce(func.sum(Credit.amount_cents), 0)).where(Credit.tenant_id == self.tenant, Credit.source_id == source.movement_id))
            if data['amountCents'] > claim - used:
                raise StockConflict('Credit exceeds the documented supplier return receivable')
            postings = pair('2000' if row.method == 'ACCRUAL' else '2100', '1300', data['amountCents'])
        elif row.method == 'ACCRUAL':
            postings = pair('4100', '1100', data['amountCents'])
        identifier = await self.post_pairs(postings, data['date'], data['reason'], 'CREDIT', data['reference'])
        self.session.add(Credit(tenant_id=self.tenant, document_id=row.id, source_id=source.movement_id,
            reference=data['reference'], date=effective, amount_cents=data['amountCents'], journal_id=identifier))
        row.credited_cents += data['amountCents']
        row.version += 1
        return {**document_dto(row), 'creditJournalId': identifier}

    async def refund(self, data):
        row = await self.owned(Document, data['documentId'])
        latest = await self.session.scalar(select(func.max(Credit.date)).where(Credit.tenant_id == self.tenant, Credit.document_id == row.id))
        if latest is None or date.fromisoformat(data['date']) < latest:
            raise ValueError('Refund must follow a recorded credit')
        if row.version != data['expectedVersion'] or data['amountCents'] > document_dto(row)['refundDueCents']:
            raise StockConflict('Document version changed or refund exceeds the approved refund balance')
        accounts = ('1100' if row.method == 'ACCRUAL' else '4100', '1000') if row.kind == 'SALE' else (
            '1000', '2000' if row.method == 'ACCRUAL' else '2100')
        identifier = await self.post_pairs(pair(*accounts, data['amountCents']), data['date'], data['reason'], 'REFUND', data['reference'])
        self.session.add(Settlement(tenant_id=self.tenant, document_id=row.id, reference=data['reference'], direction='REFUND',
            date=date.fromisoformat(data['date']), amount_cents=data['amountCents'], journal_id=identifier))
        row.refunded_cents += data['amountCents']
        row.version += 1
        return {**document_dto(row), 'refundJournalId': identifier}

    async def close_period(self, data):
        cutoff = date.fromisoformat(data['closedThrough'])
        now = await self.session.run_sync(lambda session: database_now(session.connection()))
        if self.current.version != data['expectedVersion'] or self.current.closed_through and cutoff <= self.current.closed_through:
            raise StockConflict('Accounting period/version is stale')
        if cutoff >= now.date() or cutoff < utc(self.current.activated_at).date():
            raise ValueError('Close only a completed day within the active accounting period')
        if (await self.quality(cutoff))['unresolvedSources']:
            raise StockConflict('Resolve unpriced financial sources before closing the period')
        await self.balances(end=cutoff)
        self.current.closed_through = cutoff
        self.current.version += 1
        await self.session.flush()
        return await self.status()

    async def balances(self, start=None, end=None):
        state = await self.session.scalar(select(State).where(State.tenant_id == self.tenant))
        if not state or not state.active:
            raise StockConflict('Activate financial accounting before requesting financial reports')
        if start and end and start > end:
            raise ValueError('Report start date must not exceed its end date')
        beginning = utc(state.activated_at).date()
        now = await self.session.run_sync(lambda session: database_now(session.connection()))
        if start and (start < beginning or start > now.date()) or end and (end < beginning or end > now.date()):
            raise ValueError('Report dates must be within the active accounting history')
        conditions = [Journal.tenant_id == self.tenant]
        if start:
            conditions.append(Journal.date >= start)
        if end:
            conditions.append(Journal.date <= end)
        invalid = select(Journal.id).outerjoin(Line, Line.journal_id == Journal.id).where(*conditions)
        invalid = invalid.group_by(Journal.id, Journal.line_count).having(func.count(Line.id) != Journal.line_count).limit(1)
        if await self.session.scalar(invalid):
            raise StockConflict('Posted journal is missing its immutable lines; operator repair is required')
        totals = {code: {'debitCents': 0, 'creditCents': 0} for code in ACCOUNTS}
        stream = await self.session.stream_scalars(select(Line).join(Journal, Journal.id == Line.journal_id)
            .where(*conditions, Line.tenant_id == self.tenant))
        async for line in stream:
            key = 'debitCents' if line.side == 'debit' else 'creditCents'
            totals[line.account_code][key] += line.amount_cents
            checked_cents(totals[line.account_code][key])
        return [{'accountCode': code, 'accountName': ACCOUNTS[code][0], 'category': ACCOUNTS[code][1],
            **values, 'balanceCents': values['debitCents'] - values['creditCents']} for code, values in totals.items()]

    async def reconciliation(self):
        rows = await self.balances()
        balance = {row['accountCode']: row['balanceCents'] for row in rows}
        snapshot = await inventory_snapshot(self.session, self.tenant)
        pending = await self.session.scalar(select(func.count()).select_from(Source).where(Source.tenant_id == self.tenant, Source.status == 'UNPRICED'))
        differences = {'onHandDifferenceCents': balance['1200'] - snapshot['onHandValueCents'] if snapshot['complete'] else None,
            'inTransitDifferenceCents': balance['1210'] - snapshot['inTransitValueCents'] if snapshot['complete'] else None,
            'workInProgressCents': balance['1220']}
        return {**snapshot, **differences, 'ledgerInventoryCents': balance['1200'], 'ledgerTransitCents': balance['1210'],
            'unresolvedSources': pending, 'isReconciled': snapshot['complete'] and not pending and all(value == 0 for value in differences.values())}

    async def quality(self, end=None):
        query = select(func.count()).select_from(Source).outerjoin(Journal, Journal.id == Source.journal_id).where(
            Source.tenant_id == self.tenant, or_(Source.status == 'UNPRICED',
                and_(Source.status == 'RESOLVED', Journal.date > end) if end else False))
        if end:
            query = query.where(Source.date <= end)
        count = await self.session.scalar(query)
        return {'unresolvedSources': count, 'complete': count == 0}
