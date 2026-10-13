from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.cost_models import CostLayerModel
from src.domain.aging.services import InventoryAgingService, DeadStockRecommendationEngine
from src.domain.product.layer_costing import utc, half_up

router = APIRouter(prefix='/aging', tags=['aging'])


@router.get('/report')
async def aging_report(request: Request, session: AsyncSession = Depends(get_db_session)):
    from src.application.retail_reports import snapshot
    from src.infrastructure.financial_posting import database_now
    now = await session.run_sync(lambda sync: database_now(sync.connection()))
    return await snapshot(session, current_tenant(request), {'type': 'INVENTORY_AGING', 'filters': {}}, now, request.state.user_id)


@router.get('/{sku}')
async def analyze_sku_aging(sku: str, request: Request, location_id: str, tenant_id: str | None = None,
                             session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    stock = await session.scalar(select(StockModel).where(StockModel.tenant_id == tenant,
        StockModel.sku == sku, StockModel.location_id == location_id))
    if not stock:
        raise HTTPException(404, 'Stock not found')
    movements = await session.scalars(select(StockMovementModel).where(StockMovementModel.tenant_id == tenant,
        StockMovementModel.sku == sku, StockMovementModel.location_id == location_id,
        StockMovementModel.operation.in_(['receive', 'dispatch', 'fulfill-allocation',
            'serial-receive', 'serial-sell', 'serial-return', 'serial-transfer', 'serial-write-off',
            'kit-assemble', 'kit-assemble-component', 'kit-disassemble', 'kit-disassemble-component', 'kit-sale-component'])))
    entries = [{'quantity': movement.amount, 'occurred_at': utc(movement.occurred_at)} for movement in movements]
    layers = list(await session.scalars(select(CostLayerModel).where(CostLayerModel.tenant_id == tenant,
        CostLayerModel.stock_id == stock.id, CostLayerModel.remaining_quantity > 0)))
    complete = sum(layer.remaining_quantity for layer in layers) == stock.quantity and all(
        layer.remaining_value_cents is not None for layer in layers)
    value = sum(layer.remaining_value_cents for layer in layers) if complete else None
    unit_cost = half_up(value, stock.quantity) if value is not None and stock.quantity else 0
    aging = InventoryAgingService().calculate_aging_buckets(sku, location_id, tenant,
        datetime.now(timezone.utc), entries)
    analysis = DeadStockRecommendationEngine().analyze(sku, location_id, tenant, stock.quantity,
        unit_cost, aging['days_since_last_movement'], aging['bucket'], False)
    return {'sku': sku, 'is_dead_stock': analysis.is_dead_stock, 'aging_bucket': analysis.aging_bucket.value,
            'days_since_last_movement': analysis.days_since_last_movement,
            'locked_capital_cents': value, 'recommended_action': analysis.recommended_action.value,
            'potential_scrap_emissions_kg': None}
