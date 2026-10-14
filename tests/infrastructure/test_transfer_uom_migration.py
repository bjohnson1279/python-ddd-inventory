import pytest
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import create_async_engine
from src.infrastructure.database import Base
from scripts import migrate_identity_catalog as migration


@pytest.mark.asyncio
async def test_repeatable_upgrade_preserves_existing_whole_cent_cost_layers(monkeypatch):
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    monkeypatch.setattr(migration, 'engine', engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Exercise the actual old table shape rather than only new-schema creation.
            await conn.execute(text('ALTER TABLE python_cost_layers DROP COLUMN initial_value_cents'))
            await conn.execute(text("INSERT INTO python_stock (id,tenant_id,sku,location_id,quantity,allocated,version) VALUES ('old-stock','legacy','A','shop',2,0,1)"))
            await conn.execute(text("INSERT INTO python_cost_layers (id,stock_id,tenant_id,sku,location_id,initial_quantity,remaining_quantity,unit_cost_cents,remaining_value_cents,received_at,quarantined) "
                "VALUES ('old-layer','old-stock','legacy','A','shop',2,2,101,202,CURRENT_TIMESTAMP,0)"))
        await migration.migrate()
        await migration.migrate()
        async with engine.begin() as conn:
            columns = await conn.run_sync(lambda sync: {column['name'] for column in inspect(sync).get_columns('python_cost_layers')})
            assert 'initial_value_cents' in columns
            row = (await conn.execute(text("SELECT remaining_quantity,remaining_value_cents,initial_value_cents FROM python_cost_layers WHERE id='old-layer'"))).one()
            assert tuple(row) == (2, 202, None)
            for name in ('python_uom_configs', 'python_stock_transfers', 'python_inventory_actions'):
                assert await conn.run_sync(lambda sync, table=name: inspect(sync).has_table(table))
    finally:
        await engine.dispose()
