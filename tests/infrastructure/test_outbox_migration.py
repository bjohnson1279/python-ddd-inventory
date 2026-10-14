import json
import os
import uuid
from datetime import datetime
import pytest
from sqlalchemy import text, inspect
from sqlalchemy.engine import make_url, URL
from sqlalchemy.ext.asyncio import create_async_engine
from src.infrastructure.auth.identity import TenantModel
from scripts import migrate_identity_catalog as migration


@pytest.mark.asyncio
@pytest.mark.parametrize('backend', ['sqlite', 'postgres'])
async def test_legacy_delivery_upgrade_is_repeatable_and_does_not_guess_ownership(tmp_path, monkeypatch, backend):
    if backend == 'sqlite':
        engine = create_async_engine(URL.create('sqlite+aiosqlite', database=str(tmp_path / 'old-outbox.sqlite')))
    else:
        database = os.getenv('COSTING_TEST_DATABASE_URL')
        if not database:
            pytest.skip('Set COSTING_TEST_DATABASE_URL to disposable local costing_test database')
        url = make_url(database)
        if url.host not in ('127.0.0.1', 'localhost') or url.database != 'costing_test':
            raise ValueError('Outbox migration tests require disposable local costing_test database')
        schema = 'outbox_legacy_' + uuid.uuid4().hex
        bootstrap = create_async_engine(url)
        async with bootstrap.begin() as conn:
            await conn.execute(text('CREATE SCHEMA ' + schema))
        await bootstrap.dispose()
        engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    monkeypatch.setattr(migration, 'engine', engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: TenantModel.__table__.create(sync))
            await conn.execute(TenantModel.__table__.insert().values(id='legacy', name='Legacy store'))
            await conn.execute(text('CREATE TABLE outbox_events (id VARCHAR PRIMARY KEY, aggregate_type VARCHAR NOT NULL, '
                'aggregate_id VARCHAR NOT NULL, event_type VARCHAR NOT NULL, payload JSON NOT NULL, created_at TIMESTAMP)'))
            for identifier, payload in [('owned', {'tenantId': 'legacy', 'quantity': 2}), ('unknown', {'quantity': 2})]:
                await conn.execute(text("INSERT INTO outbox_events (id,aggregate_type,aggregate_id,event_type,payload,created_at) "
                    "VALUES (:id,'Stock','stock','StockChanged',:payload,:created)"),
                    {'id': identifier, 'payload': json.dumps(payload), 'created': datetime(2026, 1, 1)})
            if backend == 'sqlite':
                await conn.execute(text("INSERT INTO outbox_events (id,aggregate_type,aggregate_id,event_type,payload,created_at) "
                    "VALUES ('zz-broken','Stock','stock','StockChanged','{broken',CURRENT_TIMESTAMP)"))
        await migration.migrate()
        await migration.migrate()
        async with engine.begin() as conn:
            rows = (await conn.execute(text('SELECT id,status,attempts,version,last_error,payload,created_at,sequence FROM outbox_events ORDER BY id'))).mappings().all()
            assert rows[0]['id'] == 'owned' and rows[0]['status'] == 'PENDING'
            assert rows[0]['attempts'] == 0 and rows[0]['version'] == 0
            assert rows[0]['sequence'] == 1 and rows[1]['sequence'] is None
            assert rows[1]['status'] == 'DEAD_LETTER' and rows[1]['last_error'] == 'UnassignedLegacyEvent'
            indexes = await conn.run_sync(lambda sync: {item['name'] for item in inspect(sync).get_indexes('outbox_events')})
            assert {'ix_outbox_delivery_queue', 'ix_outbox_aggregate_order'} <= indexes
            if backend == 'postgres':
                assert rows[0]['created_at'].year == 2026 and rows[0]['created_at'].utcoffset() is not None
                assert rows[1]['payload'] == {'quantity': 2}
            else:
                assert json.loads(rows[1]['payload']) == {'quantity': 2}
                assert rows[2]['payload'] == '{broken' and rows[2]['status'] == 'DEAD_LETTER'
    finally:
        await engine.dispose()
