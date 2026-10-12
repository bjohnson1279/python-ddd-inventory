from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.auth.rbac import requires_roles
from src.infrastructure.cost_models import AccountingConfigModel, CostLayerModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.product.models import ProductModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.application.costing_service import locked_config, load_layers
from src.domain.product.layer_costing import normalize_method, CostingMethod, half_up, redistribute_value, utc, validate_total_value

router = APIRouter(prefix='/accounting', tags=['costing and valuation'])


def config_dto(config, tenant):
    return {'tenantId': tenant, 'accountingMethod': config.accounting_method if config else 'ACCRUAL',
            'costingMethod': config.costing_method if config else 'FIFO',
            'currencyCode': 'USD', 'fiscalYearStart': '01-01'}


class ConfigDTO(BaseModel):
    tenantId: str
    costingMethod: str
    accountingMethod: str = 'ACCRUAL'

    @field_validator('costingMethod')
    @classmethod
    def costing_method(cls, value):
        return normalize_method(value).value

    @field_validator('accountingMethod')
    @classmethod
    def accounting_method(cls, value):
        if value.upper() not in ('CASH', 'ACCRUAL'):
            raise ValueError('Unsupported accounting method')
        return value.upper()


@router.get('/tenant-config/{tenant_id}')
@requires_roles(['ADMIN', 'MANAGER', 'USER'])
async def config(tenant_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    if tenant_id != current_tenant(request):
        raise HTTPException(403, 'Tenant ID mismatch')
    return config_dto(await session.get(AccountingConfigModel, tenant_id), tenant_id)


@router.post('/tenant-config')
@requires_roles(['ADMIN', 'MANAGER'])
async def save_config(data: ConfigDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    if data.tenantId != tenant:
        raise HTTPException(403, 'Tenant ID mismatch')
    try:
        config = await locked_config(session, tenant)
        if config.costing_method != data.costingMethod:
            stocked = await session.scalar(select(StockModel.id).where(
                StockModel.tenant_id == tenant, StockModel.quantity > 0).limit(1))
            if stocked:
                raise HTTPException(409, 'Cannot change costing method while stock is on hand')
        config.costing_method = data.costingMethod
        config.accounting_method = data.accountingMethod
        await session.commit()
        return config_dto(config, tenant)
    except Exception:
        await session.rollback()
        raise


def layer_dto(layer):
    return {'id': layer.id, 'sku': layer.sku, 'locationId': layer.location_id,
            'initialQuantity': layer.initial_quantity, 'remainingQuantity': layer.remaining_quantity,
            'unitCostCents': layer.unit_cost_cents, 'remainingValueCents': layer.remaining_value_cents,
            'lotNumber': layer.lot_number, 'expirationDate': layer.expiration_date,
            'receivedAt': layer.received_at, 'quarantined': layer.quarantined,
            'costStatus': 'PRICED' if layer.unit_cost_cents is not None else 'UNPRICED'}


@router.get('/cost-layers')
@requires_roles(['ADMIN', 'MANAGER', 'USER'])
async def layers(request: Request, sku: str | None = None, locationId: str | None = None,
                 session: AsyncSession = Depends(get_db_session)):
    query = select(CostLayerModel).where(CostLayerModel.tenant_id == current_tenant(request))
    if sku is not None:
        query = query.where(CostLayerModel.sku == sku)
    if locationId is not None:
        query = query.where(CostLayerModel.location_id == locationId)
    return [layer_dto(layer) for layer in await session.scalars(query.order_by(CostLayerModel.received_at, CostLayerModel.id))]


async def valuation_rows(session, tenant, location, method, sku=None):
    config = await session.get(AccountingConfigModel, tenant)
    method = normalize_method(method or (config.costing_method if config else 'FIFO'))
    query = select(StockModel).where(StockModel.tenant_id == tenant)
    if sku is not None:
        query = query.where(StockModel.sku == sku)
    if location is not None:
        query = query.where(StockModel.location_id == location)
    stocks = list(await session.scalars(query))
    layer_query = select(CostLayerModel).where(CostLayerModel.tenant_id == tenant)
    if sku is not None:
        layer_query = layer_query.where(CostLayerModel.sku == sku)
    if location is not None:
        layer_query = layer_query.where(CostLayerModel.location_id == location)
    grouped_layers = {}
    for layer in await session.scalars(layer_query):
        grouped_layers.setdefault(layer.stock_id, []).append(layer)
    products = {product.sku: product for product in await session.scalars(
        select(ProductModel).where(ProductModel.tenant_id == tenant))}
    grouped = {}
    for stock in stocks:
        remaining = [layer for layer in grouped_layers.get(stock.id, []) if layer.remaining_quantity]
        if sum(layer.remaining_quantity for layer in remaining) != stock.quantity or any(
                layer.remaining_value_cents is None for layer in remaining):
            raise HTTPException(409, f'Unpriced or inconsistent stock for {stock.sku} at {stock.location_id}')
        product = products.get(stock.sku)
        if not product:
            raise HTTPException(409, 'Stock catalog reference is missing')
        row = grouped.setdefault(stock.sku, {'variantId': product.id, 'sku': stock.sku,
            'name': product.name, 'costingMethod': method.value, 'totalQuantity': 0,
            'totalValueCents': 0, '_layers': []})
        row['totalQuantity'] += stock.quantity
        row['totalValueCents'] += sum(layer.remaining_value_cents for layer in remaining)
        row['_layers'].extend(remaining)
    for row in grouped.values():
        validate_total_value(row['totalValueCents'])
        row['unitCostCents'] = half_up(row['totalValueCents'], row['totalQuantity']) if row['totalQuantity'] else 0
    return list(grouped.values())


@router.get('/valuation-report')
@requires_roles(['ADMIN', 'MANAGER', 'USER'])
async def valuation_report(request: Request, locationId: str | None = None, method: str | None = None,
                           session: AsyncSession = Depends(get_db_session)):
    try:
        rows = await valuation_rows(session, current_tenant(request), locationId, method)
        config = await session.get(AccountingConfigModel, current_tenant(request))
        configured = config.costing_method if config else 'FIFO'
        if method is not None and normalize_method(method).value != configured:
            raise HTTPException(409, 'Valuation reports use the configured carrying method; use a valuation preview to compare methods')
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return [{key: value for key, value in row.items() if not key.startswith('_')} for row in rows]


@router.get('/valuation/{variant_id}')
@requires_roles(['ADMIN', 'MANAGER', 'USER'])
async def valuation(variant_id: str, request: Request, quantity: int | None = Query(None, ge=0),
                    locationId: str | None = None, method: str | None = None,
                    session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    product = await session.scalar(select(ProductModel).where(ProductModel.id == variant_id, ProductModel.tenant_id == tenant))
    if not product:
        raise HTTPException(404, 'Variant not found')
    try:
        rows = await valuation_rows(session, tenant, locationId, method, product.sku)
        config = await session.get(AccountingConfigModel, tenant)
        actual_method = normalize_method(method or (config.costing_method if config else 'FIFO'))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    row = next((row for row in rows if row['sku'] == product.sku), None)
    available = row['totalQuantity'] if row else 0
    requested = available if quantity is None else quantity
    if requested > available:
        raise HTTPException(400, 'Insufficient cost layers for valuation')
    total = row['totalValueCents'] if row else 0
    if requested != available:
        if actual_method == CostingMethod.WAC:
            total = half_up(total * requested, available) if available else 0
        else:
            selected = row['_layers'] if row else []
            if actual_method == CostingMethod.FEFO:
                from datetime import datetime, timezone
                selected = sorted(selected, key=lambda layer: (
                    utc(layer.expiration_date) if layer.expiration_date else datetime.max.replace(tzinfo=timezone.utc),
                    utc(layer.received_at), layer.id))
            else:
                selected = sorted(selected, key=lambda layer: (utc(layer.received_at), layer.id),
                                  reverse=actual_method == CostingMethod.LIFO)
            total, remaining = 0, requested
            for layer in selected:
                taken = min(remaining, layer.remaining_quantity)
                total += half_up(layer.remaining_value_cents * taken, layer.remaining_quantity)
                remaining -= taken
                if not remaining:
                    break
    return {'quantity': requested, 'totalCostCents': total,
            'unitCostCents': half_up(total, requested) if requested else 0, 'methodUsed': actual_method.value}


class PriceDTO(BaseModel):
    unitCostCents: int = Field(ge=0, le=2_147_483_647, strict=True)
    mutationId: str = Field(min_length=1, max_length=100)


@router.post('/cost-layers/{layer_id}/price')
@requires_roles(['ADMIN', 'MANAGER'])
async def price_layer(layer_id: str, data: PriceDTO, request: Request,
                      session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    fingerprint = {'operation': 'price-cost-layer', 'layerId': layer_id, 'unitCostCents': data.unitCostCents}
    try:
        config = await locked_config(session, tenant)
        prior = await session.scalar(select(StockMovementModel).where(
            StockMovementModel.tenant_id == tenant, StockMovementModel.mutation_id == data.mutationId))
        if prior:
            if prior.request != fingerprint:
                raise HTTPException(409, 'Mutation ID was already used for another request')
            result = prior.result
            await session.rollback()
            return result
        layer = await session.scalar(select(CostLayerModel).where(CostLayerModel.id == layer_id, CostLayerModel.tenant_id == tenant))
        if not layer:
            raise HTTPException(404, 'Cost layer not found')
        if layer.unit_cost_cents is not None or not layer.remaining_quantity:
            raise HTTPException(409, 'Only remaining unpriced stock can be priced')
        stock = await session.scalar(select(StockModel).where(StockModel.id == layer.stock_id, StockModel.tenant_id == tenant))
        if not stock:
            raise HTTPException(409, 'Stock reference is missing')
        all_layers = await load_layers(session, tenant, stock)
        layer.unit_cost_cents = data.unitCostCents
        layer.remaining_value_cents = layer.remaining_quantity * data.unitCostCents
        validate_total_value(sum(l.remaining_value_cents or 0 for l in all_layers))
        if config.costing_method == 'WAC' and all(l.remaining_value_cents is not None for l in all_layers if l.remaining_quantity):
            redistribute_value(all_layers, sum(l.remaining_value_cents or 0 for l in all_layers))
        await session.execute(update(StockModel).where(StockModel.id == stock.id).values(version=StockModel.version + 1))
        result = {'layerId': layer.id, 'sku': layer.sku, 'locationId': layer.location_id,
                  'unitCostCents': data.unitCostCents, 'costingMethod': config.costing_method,
                  'remainingQuantity': layer.remaining_quantity}
        session.add(StockMovementModel(tenant_id=tenant, mutation_id=data.mutationId,
            actor_id=request.state.user_id, sku=layer.sku, location_id=layer.location_id,
            operation='price-cost-layer', amount=layer.remaining_quantity, request=fingerprint, result=result))
        session.add(OutboxEventModel(aggregate_type='Stock', aggregate_id=stock.id,
            event_type='CostLayerPriced', payload={**result, 'tenantId': tenant, 'mutationId': data.mutationId}))
        await session.commit()
        return result
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(409, str(exc))
    except Exception:
        await session.rollback()
        raise
