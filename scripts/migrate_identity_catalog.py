"""Explicit migration of the Python catalog; never runs during API startup.

Existing unscoped products require an operator-selected legacy tenant. Run on
the Python database only, after backup, not a sibling backend's catalog table.
"""
import argparse
import asyncio
import re
from sqlalchemy import inspect, text, select, func
import uuid
from datetime import datetime, timezone
from src.infrastructure.database import engine
from src.infrastructure.auth.identity import TenantModel, UserModel, LoginSessionModel, LoginRateModel, SecurityAuditModel
from src.infrastructure.auth.rls import install_tenant_policies
from src.infrastructure.owned_feature_models import ReportDefinitionModel, NotificationModel
from src.infrastructure.cycle_count.models import CycleCountPlanModel, CycleCountRecordModel, CycleCountLineItemModel
from src.infrastructure.product.models import ProductModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.infrastructure.messaging.models import OutboxEventModel
from src.infrastructure.catalog_models import CatalogModel, VariantLinkModel, BarcodeModel
from src.infrastructure.cost_models import AccountingConfigModel, CostLayerModel


async def create_related(conn):
    for model in (StockModel, StockMovementModel, OutboxEventModel, CatalogModel, VariantLinkModel, BarcodeModel,
                  AccountingConfigModel, CostLayerModel, ReportDefinitionModel, NotificationModel,
                  CycleCountPlanModel, CycleCountRecordModel, CycleCountLineItemModel):
        await conn.run_sync(lambda sync, table=model.__table__: table.create(sync, checkfirst=True))
    # Preserve opening quantities without guessing costs; safe on repeat runs.
    rows = await conn.execute(select(StockModel.__table__,
        func.coalesce(func.sum(CostLayerModel.remaining_quantity), 0).label('layered'))
        .outerjoin(CostLayerModel, StockModel.id == CostLayerModel.stock_id)
        .group_by(*StockModel.__table__.columns))
    for stock in rows.mappings():
        missing = stock['quantity'] - stock['layered']
        if missing < 0:
            raise ValueError('Cost layers exceed stock; reconcile inventory before migration')
        if missing:
            await conn.execute(CostLayerModel.__table__.insert().values(id=str(uuid.uuid4()),
                stock_id=stock['id'], tenant_id=stock['tenant_id'], sku=stock['sku'],
                location_id=stock['location_id'], initial_quantity=missing, remaining_quantity=missing,
                unit_cost_cents=None, remaining_value_cents=None, received_at=datetime.now(timezone.utc),
                quarantined=False))
    await install_tenant_policies(conn)


async def migrate(legacy_tenant=None, identity_only=False):
    if legacy_tenant and not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', legacy_tenant):
        raise ValueError('Invalid legacy tenant ID')
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: TenantModel.__table__.create(sync, checkfirst=True))
        await conn.run_sync(lambda sync: UserModel.__table__.create(sync, checkfirst=True))
        columns = {column['name'] for column in await conn.run_sync(lambda sync: inspect(sync).get_columns('python_users'))}
        if 'is_active' not in columns:
            default = 'TRUE' if conn.dialect.name == 'postgresql' else '1'
            await conn.execute(text(f'ALTER TABLE python_users ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT {default}'))
        if 'auth_version' not in columns:
            await conn.execute(text('ALTER TABLE python_users ADD COLUMN auth_version INTEGER NOT NULL DEFAULT 0'))
        if 'supplier_id' not in columns:
            await conn.execute(text('ALTER TABLE python_users ADD COLUMN supplier_id VARCHAR'))
        for model in (LoginSessionModel, LoginRateModel, SecurityAuditModel):
            await conn.run_sync(lambda sync, table=model.__table__: table.create(sync, checkfirst=True))
    if identity_only:
        return
    async with engine.begin() as conn:
        info = await conn.run_sync(lambda sync: {
            'exists': inspect(sync).has_table('products'),
        })
        if not info['exists']:
            await conn.run_sync(lambda sync: ProductModel.__table__.create(sync))
            await create_related(conn)
            return
        columns = await conn.run_sync(lambda sync: inspect(sync).get_columns('products'))
        if 'tenant_id' in {column['name'] for column in columns}:
            await create_related(conn)
            return
        if conn.dialect.name != 'postgresql':
            raise ValueError('Legacy catalog migration requires PostgreSQL; fresh SQLite schemas are supported')
        if not legacy_tenant:
            raise ValueError('Existing catalog requires --legacy-tenant; do not guess product ownership')
        # Identity setup must be done before assigning legacy stock to it.
        exists = await conn.scalar(text('SELECT id FROM python_tenants WHERE id = :tenant'), {'tenant': legacy_tenant})
        if not exists:
            raise ValueError('Set up the legacy tenant through /auth/setup before migrating its catalog')
        constraints = await conn.run_sync(lambda sync: inspect(sync).get_unique_constraints('products'))
        indexes = await conn.run_sync(lambda sync: inspect(sync).get_indexes('products'))
        await conn.execute(text('ALTER TABLE products ADD COLUMN tenant_id VARCHAR'))
        await conn.execute(text('UPDATE products SET tenant_id = :tenant'), {'tenant': legacy_tenant})
        await conn.execute(text('ALTER TABLE products ALTER COLUMN tenant_id SET NOT NULL'))
        quote = conn.dialect.identifier_preparer.quote
        for constraint in constraints:
            if constraint['column_names'] == ['sku']:
                await conn.execute(text('ALTER TABLE products DROP CONSTRAINT ' + quote(constraint['name'])))
        for index in indexes:
            if index.get('unique') and index['column_names'] == ['sku'] and not index.get('duplicates_constraint'):
                await conn.execute(text('DROP INDEX ' + quote(index['name'])))
        await conn.execute(text('ALTER TABLE products ADD CONSTRAINT products_tenant_sku_key UNIQUE (tenant_id, sku)'))
        await conn.execute(text('CREATE INDEX ix_products_tenant_id ON products (tenant_id)'))
        await create_related(conn)


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--legacy-tenant')
    parser.add_argument('--identity-only', action='store_true')
    args = parser.parse_args()
    try:
        await migrate(args.legacy_tenant, args.identity_only)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
