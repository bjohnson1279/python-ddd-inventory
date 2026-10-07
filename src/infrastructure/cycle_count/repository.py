from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountRecord, CycleCountLineItem
from src.domain.cycle_count.repository import CycleCountRepository
from src.infrastructure.cycle_count.models import CycleCountPlanModel, CycleCountRecordModel, CycleCountLineItemModel

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

    async def get_record(self, record_id: str) -> CycleCountRecord:
        stmt = select(CycleCountRecordModel).where(CycleCountRecordModel.id == record_id)
        result = await self.session.execute(stmt)
        m = result.scalars().first()
        if not m:
            return None
        return CycleCountRecord(
            id=m.id, tenant_id=m.tenant_id, plan_id=m.plan_id, name=m.name,
            status=m.status, abc_classification=m.abc_classification,
            zone=m.zone, assigned_operator_id=m.assigned_operator_id,
            is_blind_count=m.is_blind_count, created_at=m.created_at
        )

    async def get_assigned_records(self, operator_id: str) -> List[CycleCountRecord]:
        stmt = select(CycleCountRecordModel).where(
            CycleCountRecordModel.assigned_operator_id == operator_id,
            CycleCountRecordModel.status.in_(['PENDING', 'RECOUNT_REQUIRED'])
        )
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [
            CycleCountRecord(
                id=m.id, tenant_id=m.tenant_id, plan_id=m.plan_id, name=m.name,
                status=m.status, abc_classification=m.abc_classification,
                zone=m.zone, assigned_operator_id=m.assigned_operator_id,
                is_blind_count=m.is_blind_count, created_at=m.created_at
            ) for m in models
        ]


    from src.domain.cycle_count.entity import CycleCountLineItem
    from src.infrastructure.cycle_count.models import CycleCountLineItemModel

    async def save_line_items(self, items: List['CycleCountLineItem']) -> None:
        models = [
            CycleCountLineItemModel(
                id=item.id, record_id=item.record_id, sku=item.sku,
                expected_quantity=item.expected_quantity, counted_quantity=item.counted_quantity,
                variance_quantity=item.variance_quantity, variance_value=item.variance_value,
                status=item.status
            ) for item in items
        ]
        self.session.add_all(models)
        await self.session.commit()

    async def get_record_line_items(self, record_id: str) -> List['CycleCountLineItem']:
        stmt = select(CycleCountLineItemModel).where(CycleCountLineItemModel.record_id == record_id)
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [
            CycleCountLineItem(
                id=m.id, record_id=m.record_id, sku=m.sku,
                expected_quantity=m.expected_quantity, counted_quantity=m.counted_quantity,
                variance_quantity=m.variance_quantity, variance_value=m.variance_value,
                status=m.status
            ) for m in models
        ]

    async def get_records_line_items(self, record_ids: List[str]) -> List['CycleCountLineItem']:
        if not record_ids:
            return []
        stmt = select(CycleCountLineItemModel).where(CycleCountLineItemModel.record_id.in_(record_ids))
        result = await self.session.execute(stmt)
        models = result.scalars().all()
        return [
            CycleCountLineItem(
                id=m.id, record_id=m.record_id, sku=m.sku,
                expected_quantity=m.expected_quantity, counted_quantity=m.counted_quantity,
                variance_quantity=m.variance_quantity, variance_value=m.variance_value,
                status=m.status
            ) for m in models
        ]
