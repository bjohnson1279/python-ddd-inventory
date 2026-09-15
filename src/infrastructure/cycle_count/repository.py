from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountRecord
from src.domain.cycle_count.repository import CycleCountRepository
from src.infrastructure.cycle_count.models import CycleCountPlanModel, CycleCountRecordModel

class SQLAlchemyCycleCountRepository(CycleCountRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_plan(self, plan: CycleCountPlan) -> CycleCountPlan:
        model = CycleCountPlanModel(
            id=plan.id,
            tenant_id=plan.tenant_id,
            name=plan.name,
            abc_classification=plan.abc_classification,
            frequency_days=plan.frequency_days,
            zone=plan.zone,
            is_active=plan.is_active,
            created_at=plan.created_at
        )
        self.session.add(model)
        await self.session.commit()
        return plan

    async def get_active_plans(self, tenant_id: str) -> List[CycleCountPlan]:
        stmt = select(CycleCountPlanModel).where(
            CycleCountPlanModel.tenant_id == tenant_id,
            CycleCountPlanModel.is_active == True
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [
            CycleCountPlan(
                id=m.id,
                tenant_id=m.tenant_id,
                name=m.name,
                abc_classification=m.abc_classification,
                frequency_days=m.frequency_days,
                zone=m.zone,
                is_active=m.is_active,
                created_at=m.created_at
            ) for m in models
        ]

    async def save_record(self, record: CycleCountRecord) -> CycleCountRecord:
        model = CycleCountRecordModel(
            id=record.id,
            tenant_id=record.tenant_id,
            plan_id=record.plan_id,
            name=record.name,
            status=record.status,
            abc_classification=record.abc_classification,
            zone=record.zone,
            is_blind_count=record.is_blind_count,
            created_at=record.created_at
        )
        self.session.add(model)
        await self.session.commit()
        return record

    async def save_records(self, records: List[CycleCountRecord]) -> List[CycleCountRecord]:
        models = [
            CycleCountRecordModel(
                id=record.id,
                tenant_id=record.tenant_id,
                plan_id=record.plan_id,
                name=record.name,
                status=record.status,
                abc_classification=record.abc_classification,
                zone=record.zone,
                is_blind_count=record.is_blind_count,
                created_at=record.created_at
            ) for record in records
        ]
        self.session.add_all(models)
        await self.session.commit()
        return records
