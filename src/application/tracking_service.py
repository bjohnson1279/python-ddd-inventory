from sqlalchemy import select
from src.infrastructure.serial_kit_models import TrackingModel, KitModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.application.uom_service import owned_product, configured


async def require_quantity_tracking(session, tenant, sku):
    if await session.scalar(select(TrackingModel.id).where(TrackingModel.tenant_id == tenant, TrackingModel.sku == sku)):
        raise ValueError('Serial-tracked SKU requires an identified serial command')


async def enable_serial_tracking(session, tenant, sku):
    await owned_product(session, tenant, sku)
    existing = await session.scalar(select(TrackingModel).where(TrackingModel.tenant_id == tenant, TrackingModel.sku == sku))
    if existing:
        return existing
    unit = await configured(session, tenant, sku)
    if unit and (unit.category != 'discrete' or unit.base_unit != 'ea'):
        raise ValueError('Serial tracking requires discrete eaches')
    if await session.scalar(select(StockMovementModel.id).where(StockMovementModel.tenant_id == tenant, StockMovementModel.sku == sku).limit(1)):
        raise ValueError('Historical quantity stock requires an explicit serial migration')
    if await session.scalar(select(StockModel.id).where(StockModel.tenant_id == tenant, StockModel.sku == sku, StockModel.quantity > 0).limit(1)):
        raise ValueError('Existing stock requires an explicit serial migration')
    kits = list(await session.scalars(select(KitModel).where(KitModel.tenant_id == tenant)))
    if any(k.sku == sku or any(c['sku'] == sku for c in k.components) for k in kits):
        raise ValueError('Serial SKU cannot be a quantity kit or kit component')
    row = TrackingModel(tenant_id=tenant, sku=sku)
    session.add(row)
    await session.flush()
    return row
