"""Bounded current inventory snapshots and dated ledger reports; no mock rows."""
import csv
import io
import json
from datetime import date
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from src.infrastructure.stock_models import StockModel as Stock, StockMovementModel as Movement
from src.infrastructure.cost_models import CostLayerModel as Layer
from src.infrastructure.procurement_models import ReorderPolicyModel as Policy
from src.infrastructure.transfer_models import UomConfigModel
from src.domain.product.layer_costing import utc
from src.domain.financial_accounting import checked_cents
from src.application.financial_service import FinancialService

TYPES = {'STOCK', 'INVENTORY_VALUATION', 'INVENTORY_AGING', 'MOVEMENTS', 'REPLENISHMENT', 'TRIAL_BALANCE', 'INCOME_STATEMENT', 'BALANCE_SHEET'}
FINANCIAL = {'TRIAL_BALANCE', 'INCOME_STATEMENT', 'BALANCE_SHEET'}


class Filters(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sku: str | None = Field(default=None, min_length=1, max_length=100)
    locationId: str | None = Field(default=None, min_length=1, max_length=100)
    fromDate: date | None = None
    toDate: date | None = None
    asOf: date | None = None


def validate_definition(payload):
    kind = payload.get('type', 'STOCK')
    if kind not in TYPES:
        raise ValueError('Unsupported report type')
    filters = payload.get('filters') or {}
    if isinstance(filters, str):
        filters = json.loads(filters)
    values = Filters.model_validate(filters).model_dump(mode='json', exclude_none=True)
    if payload.get('grouping') not in (None, {}, '{}', ''):
        raise ValueError('Report grouping is not implemented')
    if kind in FINANCIAL and any(k in values for k in ('sku', 'locationId')):
        raise ValueError('Ledger reports cannot be filtered by stock location/SKU')
    if kind not in FINANCIAL | {'MOVEMENTS'} and any(k in values for k in ('fromDate', 'toDate', 'asOf')):
        raise ValueError('Inventory snapshots describe current stock; historical inventory filters are unsupported')
    if kind == 'MOVEMENTS' and 'asOf' in values or kind in ('TRIAL_BALANCE', 'BALANCE_SHEET') and any(k in values for k in ('fromDate', 'toDate')) or kind == 'INCOME_STATEMENT' and 'asOf' in values:
        raise ValueError('Unsupported date filter for this report')
    return {**payload, 'type': kind, 'filters': values}


def required_permission(definition):
    kind = definition.get('type', 'STOCK')
    return 'finance:read' if kind in FINANCIAL else 'accounting:read' if kind in ('INVENTORY_VALUATION', 'MOVEMENTS') else 'reports:read'


async def snapshot(session, tenant, definition, now, actor):
    kind, filters = definition['type'], definition['filters']
    start, end = [date.fromisoformat(filters[k]) if filters.get(k) else None for k in ('fromDate', 'toDate')]
    if start and end and start > end or start and start > now.date() or end and end > now.date():
        raise ValueError('Report dates are outside the supported history')
    if kind in FINANCIAL:
        as_of = date.fromisoformat(filters['asOf']) if filters.get('asOf') else None
        service = FinancialService(session, tenant, actor)
        rows = await service.balances(start, end) if kind == 'INCOME_STATEMENT' else await service.balances(end=as_of)
        quality = await service.quality(end if kind == 'INCOME_STATEMENT' else as_of)
        if kind == 'INCOME_STATEMENT':
            revenue = -sum(r['balanceCents'] for r in rows if r['category'] == 'revenue')
            expenses = sum(r['balanceCents'] for r in rows if r['category'] == 'expense')
            summary = {'revenueCents': revenue, 'expenseCents': expenses, 'profitCents': revenue - expenses}
            rows = [r for r in rows if r['category'] in ('revenue', 'expense')]
        elif kind == 'BALANCE_SHEET':
            assets = sum(r['balanceCents'] for r in rows if r['category'] == 'asset')
            liabilities = -sum(r['balanceCents'] for r in rows if r['category'] == 'liability')
            equity = -sum(r['balanceCents'] for r in rows if r['category'] == 'equity')
            earnings = -sum(r['balanceCents'] for r in rows if r['category'] in ('revenue', 'expense'))
            summary = {'assetsCents': assets, 'liabilitiesCents': liabilities, 'equityCents': equity, 'currentEarningsCents': earnings}
            quality['isBalanced'] = assets == liabilities + equity + earnings
        else:
            summary = {'totalDebitsCents': sum(max(0, r['balanceCents']) for r in rows),
                'totalCreditsCents': sum(max(0, -r['balanceCents']) for r in rows)}
            quality['isBalanced'] = summary['totalDebitsCents'] == summary['totalCreditsCents']
        for amount in summary.values():
            checked_cents(amount, signed=True)
        return {'columns': list(rows[0]), 'rows': rows, 'summary': summary, 'currency': 'USD',
            'accountingMethod': (await service.status())['method'], 'generatedAt': now.isoformat(), **quality}
    if kind == 'MOVEMENTS':
        query = select(Movement).where(Movement.tenant_id == tenant)
        if filters.get('sku'):
            query = query.where(Movement.sku == filters['sku'])
        if filters.get('locationId'):
            query = query.where(Movement.location_id == filters['locationId'])
        from datetime import datetime, time, timezone, timedelta
        if start:
            query = query.where(Movement.occurred_at >= datetime.combine(start, time(), timezone.utc))
        if end:
            query = query.where(Movement.occurred_at < datetime.combine(end + timedelta(days=1), time(), timezone.utc))
        items = list(await session.scalars(query.order_by(Movement.occurred_at, Movement.id).limit(10001)))
        if len(items) > 10000:
            raise ValueError('Report exceeds 10000 rows; narrow its filters')
        columns = ['movementId', 'sku', 'locationId', 'operation', 'quantity', 'occurredAt', 'cogsCents']
        rows = [{'movementId': r.id, 'sku': r.sku, 'locationId': r.location_id, 'operation': r.operation,
            'quantity': r.amount, 'occurredAt': utc(r.occurred_at).isoformat(), 'cogsCents': r.result.get('costOfGoodsSoldCents')} for r in items]
        return {'columns': columns, 'rows': rows, 'currency': 'USD', 'generatedAt': now.isoformat(),
            'complete': all(r['operation'] not in ('dispatch', 'fulfill-allocation', 'serial-sell', 'kit-sale-component') or r['cogsCents'] is not None for r in rows)}
    if kind == 'REPLENISHMENT':
        from src.application.procurement_service import ProcurementService
        suggestions = await ProcurementService(session, tenant, actor).suggestions()
        units = {u.sku: u.base_unit for u in await session.scalars(select(UomConfigModel).where(UomConfigModel.tenant_id == tenant))}
        columns = ['id', 'sku', 'locationId', 'supplierId', 'supplierActive', 'availableQuantity', 'unusableQuantity', 'incomingQuantity', 'suggestedQuantity', 'reason', 'baseUnit']
        for suggestion in suggestions:
            suggestion['baseUnit'] = units.get(suggestion['sku'], 'units')
        rows = [{k: r.get(k) for k in columns} for r in suggestions if
            (not filters.get('sku') or r['sku'] == filters['sku']) and (not filters.get('locationId') or r['locationId'] == filters['locationId'])]
        if len(rows) > 10000:
            raise ValueError('Report exceeds 10000 rows; narrow its filters')
        return {'columns': columns, 'rows': rows, 'complete': True, 'generatedAt': now.isoformat()}
    query = select(Stock).where(Stock.tenant_id == tenant)
    if filters.get('sku'):
        query = query.where(Stock.sku == filters['sku'])
    if filters.get('locationId'):
        query = query.where(Stock.location_id == filters['locationId'])
    stocks = list(await session.scalars(query.order_by(Stock.sku, Stock.location_id).limit(10001)))
    if len(stocks) > 10000:
        raise ValueError('Report exceeds 10000 rows; narrow its filters')
    rows = [{'sku': s.sku, 'locationId': s.location_id, 'quantity': s.quantity, 'allocated': s.allocated,
        'available': s.quantity - s.allocated, 'stockVersion': s.version} for s in stocks]
    units = {u.sku: u for u in await session.scalars(select(UomConfigModel).where(UomConfigModel.tenant_id == tenant))}
    for row in rows:
        unit = units.get(row['sku'])
        row.update(baseUnit=unit.base_unit if unit else 'units', uomVersion=unit.version if unit else None)
    complete = True
    if kind in ('INVENTORY_VALUATION', 'INVENTORY_AGING'):
        layers = await session.scalars(select(Layer).join(Stock, Stock.id == Layer.stock_id).where(
            Layer.tenant_id == tenant, Layer.remaining_quantity > 0, Stock.tenant_id == tenant,
            *([Stock.sku == filters['sku']] if filters.get('sku') else []),
            *([Stock.location_id == filters['locationId']] if filters.get('locationId') else [])))
        grouped = {}
        for layer in layers:
            grouped.setdefault(layer.stock_id, []).append(layer)
        for stock, row in zip(stocks, rows):
            owned = grouped.get(stock.id, [])
            matched = sum(l.remaining_quantity for l in owned) == stock.quantity
            if kind == 'INVENTORY_VALUATION':
                priced = matched and all(l.remaining_value_cents is not None for l in owned)
                value = sum(l.remaining_value_cents for l in owned) if priced else None
                if value is not None:
                    checked_cents(value)
                row.update(valueCents=value, complete=priced)
                complete = complete and priced
            else:
                buckets = {'age0To30': 0, 'age31To60': 0, 'age61To90': 0, 'ageOver90': 0}
                for layer in owned:
                    days = max(0, (now - utc(layer.received_at)).days)
                    bucket = 'age0To30' if days <= 30 else 'age31To60' if days <= 60 else 'age61To90' if days <= 90 else 'ageOver90'
                    buckets[bucket] += layer.remaining_quantity
                row.update(**buckets, unknownAgeQuantity=max(0, stock.quantity - sum(buckets.values())), complete=matched)
                complete = complete and matched
    columns = ['sku', 'locationId', 'quantity', 'allocated', 'available', 'stockVersion', 'baseUnit', 'uomVersion']
    columns += ['valueCents', 'complete'] if kind == 'INVENTORY_VALUATION' else ['age0To30', 'age31To60', 'age61To90', 'ageOver90', 'unknownAgeQuantity', 'complete'] if kind == 'INVENTORY_AGING' else []
    return {'columns': columns, 'rows': rows, 'complete': complete, 'generatedAt': now.isoformat(), 'currency': 'USD' if kind == 'INVENTORY_VALUATION' else None}


def export_csv(result):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow([*result['columns'], 'reportComplete', 'generatedAt', 'reportType', 'accountingMethod', 'currency'])
    for row in result['rows']:
        values = []
        for column in result['columns']:
            value = row.get(column)
            if isinstance(value, str) and (value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n'))):
                value = "'" + value
            values.append('' if value is None else value)
        writer.writerow([*values, result.get('complete'), result.get('generatedAt'), result.get('reportType'), result.get('accountingMethod'), result.get('currency')])
    return output.getvalue()
