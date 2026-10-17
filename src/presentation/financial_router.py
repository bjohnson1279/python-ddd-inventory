from datetime import date, timedelta
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.financial_models import JournalModel as Journal, JournalLineModel as Line, FinancialSourceModel as Source, FinancialDocumentModel as Document
from src.application.financial_service import FinancialService, journal_dto, source_dto, document_dto
from src.application.stock_service import StockConflict
from src.domain.financial_accounting import ACCOUNTS, checked_cents

router = APIRouter(prefix='/accounting', tags=['financial accounting'])
Version = Annotated[int, Field(ge=0, strict=True)]
Cents = Annotated[int, Field(gt=0, le=9007199254740991, strict=True)]


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenantId: str | None = None
    mutationId: str = Field(min_length=1, max_length=100, pattern=r'\S')
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')


class Activate(Command):
    expectedVersion: Version


class ClosePeriod(Activate):
    closedThrough: date


class PostingLine(BaseModel):
    model_config = ConfigDict(extra='forbid')
    accountCode: str
    amountCents: Cents
    type: Literal['debit', 'credit']
    memo: str = Field(default='', max_length=500)


class ManualJournal(Command):
    tenantId: str | None = None
    description: str = Field(min_length=1, max_length=500, pattern=r'\S')
    date: date
    method: str
    referenceId: str | None = Field(default=None, min_length=1, max_length=100)
    lines: list[PostingLine] = Field(min_length=2, max_length=100)

    @field_validator('method')
    @classmethod
    def method_value(cls, value):
        if value.upper() not in ('CASH', 'ACCRUAL'):
            raise ValueError('Unknown accounting method')
        return value.upper()


class Reverse(Command):
    date: date


class Resolve(Command):
    expectedVersion: Version
    date: date
    referenceId: str = Field(min_length=1, max_length=100)
    lines: list[PostingLine] = Field(min_length=2, max_length=100)


class CommercialDocument(Command):
    kind: Literal['SALE', 'PURCHASE']
    date: date
    reference: str = Field(min_length=1, max_length=100, pattern=r'\S')
    counterparty: str = Field(min_length=1, max_length=200, pattern=r'\S')
    supplierId: str | None = None
    amountCents: Cents
    sourceIds: list[str] = Field(min_length=1, max_length=100)


class Settlement(Command):
    expectedVersion: Version
    reference: str = Field(min_length=1, max_length=100, pattern=r'\S')
    amountCents: Cents
    date: date


class Credit(Settlement):
    sourceId: str = Field(min_length=1, max_length=100)


def service(request, session):
    return FinancialService(session, current_tenant(request), request.state.user_id)


async def execute(request, session, operation, data, **fields):
    payload = {**data.model_dump(mode='json'), **fields}
    key = payload.pop('mutationId')
    try:
        return await service(request, session).execute(operation, payload, key)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except StockConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/financial/status')
async def status(request: Request, session: AsyncSession = Depends(get_db_session)):
    return await service(request, session).status()


@router.get('/financial/opening-preview')
async def opening_preview(request: Request, session: AsyncSession = Depends(get_db_session)):
    from src.application.financial_service import inventory_snapshot
    from src.infrastructure.cost_models import AccountingConfigModel
    config = await session.get(AccountingConfigModel, current_tenant(request))
    return {**await inventory_snapshot(session, current_tenant(request)), 'method': config.accounting_method if config else 'ACCRUAL', 'currency': 'USD'}


@router.post('/financial/activate')
async def activate(data: Activate, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'activate', data)


@router.post('/financial/close-period')
async def close(data: ClosePeriod, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'close_period', data)


@router.get('/chart-of-accounts')
async def accounts():
    return [{'code': code, 'name': name, 'category': category, 'currency': 'USD'} for code, (name, category) in ACCOUNTS.items()]


@router.get('/ledger')
@router.get('/journals')
async def journals(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(Journal).where(Journal.tenant_id == current_tenant(request)).order_by(Journal.number).limit(limit).offset(offset))
    return [journal_dto(row) for row in rows]


@router.get('/journals/{identifier}')
async def journal(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        return journal_dto(await service(request, session).owned(Journal, identifier))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/journals', status_code=201)
async def create_journal(data: ManualJournal, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'create_journal', data)


@router.post('/journals/{identifier}/reverse', status_code=201)
async def reverse(identifier: str, data: Reverse, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'reverse', data, journalId=identifier)


@router.get('/sources')
async def sources(request: Request, status: Literal['PRE_ACTIVATION', 'UNPRICED', 'NO_EFFECT', 'POSTED', 'RESOLVED'] | None = None,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    query = select(Source).where(Source.tenant_id == current_tenant(request))
    if status:
        query = query.where(Source.status == status)
    return [source_dto(row) for row in await session.scalars(query.order_by(Source.date, Source.movement_id).limit(limit).offset(offset))]


@router.get('/sources/{identifier}')
async def source(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        return source_dto(await service(request, session).owned(Source, identifier))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/sources/{identifier}/resolve')
async def resolve(identifier: str, data: Resolve, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'resolve', data, sourceId=identifier)


@router.get('/documents')
async def documents(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(Document).where(Document.tenant_id == current_tenant(request)).order_by(Document.date, Document.id).limit(limit).offset(offset))
    return [document_dto(row) for row in rows]


@router.get('/documents/{identifier}')
async def document(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        return document_dto(await service(request, session).owned(Document, identifier))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/documents', status_code=201)
async def create_document(data: CommercialDocument, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'create_document', data)


@router.post('/documents/{identifier}/settle')
async def settle(identifier: str, data: Settlement, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'settle', data, documentId=identifier)


@router.post('/documents/{identifier}/credit')
async def credit(identifier: str, data: Credit, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'apply_credit', data, documentId=identifier)


@router.post('/documents/{identifier}/refund')
async def refund(identifier: str, data: Settlement, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'refund', data, documentId=identifier)


async def balances(request, session, start=None, end=None):
    try:
        return await service(request, session).balances(start, end)
    except StockConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/trial-balance')
async def trial_balance(request: Request, asOf: date | None = None, session: AsyncSession = Depends(get_db_session)):
    rows = await balances(request, session, end=asOf)

    # Bolt Optimization: Single-pass list aggregation to avoid multiple O(N) traversals
    debit = 0
    credit = 0
    for row in rows:
        bal = row['balanceCents']
        if bal > 0:
            debit += bal
        elif bal < 0:
            credit -= bal

    try:
        checked_cents(debit)
        checked_cents(credit)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'currency': 'USD', 'asOf': asOf.isoformat() if asOf else None, 'accounts': rows,
        'totalDebitsCents': debit, 'totalCreditsCents': credit, 'isBalanced': debit == credit,
        **await service(request, session).quality(asOf)}


@router.get('/income-statement')
async def income(request: Request, fromDate: date | None = None, toDate: date | None = None, session: AsyncSession = Depends(get_db_session)):
    rows = await balances(request, session, fromDate, toDate)

    # Bolt Optimization: Single-pass list aggregation to avoid multiple O(N) traversals
    revenue = 0
    expenses = 0
    for row in rows:
        cat = row['category']
        bal = row['balanceCents']
        if cat == 'revenue':
            revenue -= bal
        elif cat == 'expense':
            expenses += bal

    try:
        for value in (revenue, expenses, revenue - expenses):
            checked_cents(value, signed=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'currency': 'USD', 'revenueCents': revenue, 'expenseCents': expenses, 'profitCents': revenue - expenses,
        'accounts': [r for r in rows if r['category'] in ('revenue', 'expense')], **await service(request, session).quality(toDate)}


@router.get('/balance-sheet')
async def balance_sheet(request: Request, asOf: date | None = None, session: AsyncSession = Depends(get_db_session)):
    rows = await balances(request, session, end=asOf)

    # Bolt Optimization: Single-pass list aggregation to avoid multiple O(N) traversals
    assets = 0
    liabilities = 0
    equity = 0
    earnings = 0
    for r in rows:
        cat = r['category']
        bal = r['balanceCents']
        if cat == 'asset':
            assets += bal
        elif cat == 'liability':
            liabilities -= bal
        elif cat == 'equity':
            equity -= bal
        elif cat in ('revenue', 'expense'):
            earnings -= bal

    try:
        for value in (assets, liabilities, equity, earnings):
            checked_cents(value, signed=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {'currency': 'USD', 'assetsCents': assets, 'liabilitiesCents': liabilities, 'equityCents': equity,
        'currentEarningsCents': earnings, 'isBalanced': assets == liabilities + equity + earnings, 'accounts': rows,
        **await service(request, session).quality(asOf)}


@router.get('/general-ledger')
async def general_ledger(request: Request, accountCode: str, fromDate: date | None = None, toDate: date | None = None,
    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    if accountCode not in ACCOUNTS:
        raise HTTPException(400, 'Unknown account code')
    await balances(request, session, fromDate, toDate)
    state = await service(request, session).state()
    opening = 0
    if fromDate and fromDate > utc_date(state.activated_at):
        opening = next(r['balanceCents'] for r in await balances(request, session, end=fromDate - timedelta(days=1)) if r['accountCode'] == accountCode)
    query = select(Line, Journal).join(Journal, Journal.id == Line.journal_id).where(Line.tenant_id == current_tenant(request),
        Journal.tenant_id == current_tenant(request), Line.account_code == accountCode)
    if fromDate:
        query = query.where(Journal.date >= fromDate)
    if toDate:
        query = query.where(Journal.date <= toDate)
    running, returned, index = opening, [], 0
    stream = await session.stream(query.order_by(Journal.date, Journal.number, Line.position))
    async for line, entry in stream:
        running += line.amount_cents if line.side == 'debit' else -line.amount_cents
        try:
            checked_cents(running, signed=True)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if offset <= index < offset + limit:
            returned.append({'journalId': entry.id, 'number': entry.number, 'date': entry.date.isoformat(), 'description': entry.description,
                'amountCents': line.amount_cents, 'type': line.side, 'runningBalanceCents': running, 'memo': line.memo})
        index += 1
    return {'accountCode': accountCode, 'openingBalanceCents': opening, 'closingBalanceCents': running, 'totalLines': index, 'lines': returned}


def utc_date(value):
    from src.domain.product.layer_costing import utc
    return utc(value).date()


@router.get('/financial/reconciliation')
async def reconciliation(request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        return await service(request, session).reconciliation()
    except StockConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
