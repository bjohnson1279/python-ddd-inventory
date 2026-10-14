import pytest
from sqlalchemy import inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from scripts import migrate_identity_catalog as migration
from src.infrastructure.stock_models import StockModel
from src.infrastructure.cost_models import CostLayerModel
from sqlalchemy import select


@pytest.mark.asyncio
async def test_fresh_catalog_migration_is_repeatable(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    monkeypatch.setattr(migration, 'engine', engine)
    try:
        await migration.migrate(identity_only=True)
        async with engine.connect() as conn:
            tables = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
            assert 'python_users' in tables
            assert 'products' not in tables
        await migration.migrate()
        await migration.migrate()
        async with engine.connect() as conn:
            columns = await conn.run_sync(lambda sync: inspect(sync).get_columns('products'))
            assert 'tenant_id' in {column['name'] for column in columns}
            unique = await conn.run_sync(lambda sync: inspect(sync).get_unique_constraints('products'))
            assert any(item['column_names'] == ['tenant_id', 'sku'] for item in unique)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_sqlite_migration_refuses_to_guess_or_rebuild_catalog(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    monkeypatch.setattr(migration, 'engine', engine)
    try:
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE products (id VARCHAR PRIMARY KEY, sku VARCHAR UNIQUE)'))
            await conn.execute(text("INSERT INTO products VALUES ('legacy', 'SKU-A')"))
        with pytest.raises(ValueError, match='Legacy catalog migration requires PostgreSQL'):
            await migration.migrate()
        async with engine.connect() as conn:
            assert await conn.scalar(text('SELECT sku FROM products')) == 'SKU-A'
            columns = await conn.run_sync(lambda sync: inspect(sync).get_columns('products'))
            assert 'tenant_id' not in {column['name'] for column in columns}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_costing_migration_preserves_opening_stock_as_unpriced(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    monkeypatch.setattr(migration, 'engine', engine)
    try:
        await migration.migrate()
        async with engine.begin() as conn:
            await conn.execute(StockModel.__table__.insert().values(id='opening', tenant_id='tenant',
                sku='OPEN-SKU', location_id='store', quantity=7, allocated=0, version=1))
        await migration.migrate()
        await migration.migrate()
        async with engine.connect() as conn:
            rows = (await conn.execute(select(CostLayerModel.__table__))).mappings().all()
            assert len(rows) == 1
            assert rows[0]['remaining_quantity'] == 7
            assert rows[0]['unit_cost_cents'] is None
            assert rows[0]['remaining_value_cents'] is None
            assert await conn.scalar(select(StockModel.quantity)) == 7
    finally:
        await engine.dispose()
