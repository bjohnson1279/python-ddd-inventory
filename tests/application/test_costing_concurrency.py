import asyncio
from datetime import datetime, timezone
import pytest
from sqlalchemy import select
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.infrastructure.database import Base
from src.infrastructure.product.models import ProductModel
from src.infrastructure.cost_models import CostLayerModel
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.application.stock_service import StockService


@pytest.mark.asyncio
async def test_two_buyers_cannot_both_consume_last_costed_unit(tmp_path):
    engine = create_async_engine(URL.create('sqlite+aiosqlite', database=str(tmp_path / 'costing.sqlite')))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with sessions() as session:
            now = datetime.now(timezone.utc)
            session.add(ProductModel(id='variant', tenant_id='tenant', sku='LAST-SKU', name='Last Unit',
                price_amount=99, created_at=now, updated_at=now))
            await session.commit()
            await StockService(session, 'tenant', 'receiver').mutate('receive', 'LAST-SKU', 'store', 1,
                                                                   'receipt', unit_cost_cents=50)

        async def buyer(key):
            async with sessions() as session:
                return await StockService(session, 'tenant', key).mutate('dispatch', 'LAST-SKU', 'store', 1, key)

        results = await asyncio.gather(buyer('buyer-a'), buyer('buyer-b'), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in results) == 1
        assert sum(isinstance(result, ValueError) for result in results) == 1
        winner = next(result for result in results if isinstance(result, dict))
        assert winner['costOfGoodsSoldCents'] == 50
        async with sessions() as session:
            stock = await session.scalar(select(StockModel))
            layer = await session.scalar(select(CostLayerModel))
            movements = list(await session.scalars(select(StockMovementModel)))
            assert stock.quantity == layer.remaining_quantity == layer.remaining_value_cents == 0
            assert len(movements) == 2  # One receipt, one sale.
    finally:
        await engine.dispose()
