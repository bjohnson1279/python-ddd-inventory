import asyncio
import time
import uuid
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from src.infrastructure.database import Base
from src.domain.cycle_count.entity import CycleCountRecord
from src.infrastructure.cycle_count.repository import SQLAlchemyCycleCountRepository

async def setup_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    return Session()

async def benchmark():
    session = await setup_db()
    repo = SQLAlchemyCycleCountRepository(session)

    records = [
        CycleCountRecord(
            id=str(uuid.uuid4()),
            tenant_id="tenant-1",
            plan_id="plan-1",
            name=f"audit-{i}",
            abc_classification="A",
            status="PENDING",
            is_blind_count=False
        ) for i in range(1000)
    ]

    start = time.time()
    for record in records:
        await repo.save_record(record)
    duration = time.time() - start
    print(f"Sequential save duration: {duration:.4f}s")

    # Optional: we can add batch save bench later

if __name__ == "__main__":
    asyncio.run(benchmark())
