import uuid
from datetime import datetime, timezone
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from src.infrastructure.cost_models import AccountingConfigModel, CostLayerModel
from src.domain.product.layer_costing import CostingMethod, consume, redistribute_value, utc, eligible_layers, validate_total_value
from src.domain.uom import cost_groups


async def locked_config(session, tenant):
    # Both deployed database engines support conflict-safe initialization.
    insert = sqlite_insert if session.bind.dialect.name == 'sqlite' else pg_insert
    await session.execute(insert(AccountingConfigModel).values(tenant_id=tenant,
        costing_method='FIFO', accounting_method='ACCRUAL', currency='USD', version=0)
        .on_conflict_do_nothing(index_elements=['tenant_id']))
    config = await session.scalar(select(AccountingConfigModel).where(
        AccountingConfigModel.tenant_id == tenant).with_for_update())
    # Serializes method changes with stock mutations even on SQLite (which
    # ignores FOR UPDATE). This is a small-retail, tenant-level write lock.
    await session.execute(update(AccountingConfigModel).where(
        AccountingConfigModel.tenant_id == tenant).values(version=AccountingConfigModel.version + 1))
    return config


async def load_layers(session, tenant, stock):
    layers = list(await session.scalars(select(CostLayerModel).where(
        CostLayerModel.tenant_id == tenant, CostLayerModel.stock_id == stock.id).order_by(CostLayerModel.id)))
    layered = sum(layer.remaining_quantity for layer in layers)
    if layered > stock.quantity:
        raise ValueError('Cost layers exceed stock; reconcile inventory before mutation')
    if layered < stock.quantity:
        # Never invent a purchase cost for inventory received before costing.
        missing = stock.quantity - layered
        opening = CostLayerModel(id=str(uuid.uuid4()), stock_id=stock.id, tenant_id=tenant,
            sku=stock.sku, location_id=stock.location_id, initial_quantity=missing,
            remaining_quantity=missing, unit_cost_cents=None, remaining_value_cents=None,
            received_at=datetime.now(timezone.utc), quarantined=False)
        session.add(opening)
        layers.append(opening)
    return layers


async def apply_costing(session, tenant, stock, operation, amount, method,
                        unit_cost_cents=None, lot_number=None, expiration_date=None,
                        received_at=None, quarantined=False, receipt_value_cents=None):
    layers = await load_layers(session, tenant, stock)
    method = CostingMethod(method)
    if operation == 'receive':
        if received_at and utc(received_at) > datetime.now(timezone.utc):
            raise ValueError('Receipt date cannot be in the future')
        value = receipt_value_cents if receipt_value_cents is not None else amount * unit_cost_cents if unit_cost_cents is not None else None
        created = []
        for quantity, cost in cost_groups(amount, value):
            layer = CostLayerModel(id=str(uuid.uuid4()), stock_id=stock.id, tenant_id=tenant,
                sku=stock.sku, location_id=stock.location_id, initial_quantity=quantity,
                remaining_quantity=quantity, unit_cost_cents=cost,
                initial_value_cents=value, remaining_value_cents=value,
                lot_number=lot_number, expiration_date=expiration_date,
                received_at=received_at or datetime.now(timezone.utc), quarantined=quarantined)
            session.add(layer)
            layers.append(layer)
            created.append(layer.id)
        validate_total_value(sum(layer.remaining_value_cents or 0 for layer in layers))
        if method == CostingMethod.WAC and all(
                layer.remaining_value_cents is not None for layer in layers if layer.remaining_quantity):
            redistribute_value(layers, sum(layer.remaining_value_cents or 0 for layer in layers))
        return {'costingMethod': method.value, 'costOfGoodsSoldCents': None,
                'costLayerId': created[0], 'costLayerIds': created,
                'costStatus': 'PRICED' if value is not None else 'UNPRICED'}
    if operation == 'allocate' and sum(layer.remaining_quantity for layer in eligible_layers(layers, method)) - stock.allocated < amount:
        raise ValueError('Insufficient eligible stock to allocate')
    if operation in ('dispatch', 'fulfill-allocation'):
        cost, consumptions = consume(layers, amount, method, lot_number=lot_number)
        remaining_allocated = stock.allocated - (amount if operation == 'fulfill-allocation' else 0)
        if sum(layer.remaining_quantity for layer in eligible_layers(layers, method)) < remaining_allocated:
            raise ValueError('Dispatch would consume eligible stock backing existing reservations')
        return {'costingMethod': method.value, 'costOfGoodsSoldCents': cost,
                'costStatus': 'PRICED' if cost is not None else 'UNPRICED', 'consumedLayers': consumptions}
    return {}
