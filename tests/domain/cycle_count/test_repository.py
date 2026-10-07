import pytest
from typing import List
from src.domain.cycle_count.repository import CycleCountRepository
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountRecord

def test_cannot_instantiate_abc():
    """Test that CycleCountRepository cannot be instantiated directly."""
    with pytest.raises(TypeError):
        CycleCountRepository()

def test_subclass_missing_methods():
    """Test that a subclass missing required abstract methods cannot be instantiated."""
    class IncompleteRepository(CycleCountRepository):
        async def save_plan(self, plan: CycleCountPlan) -> CycleCountPlan:
            return plan
        # Missing get_active_plans, save_record, save_records

    with pytest.raises(TypeError):
        IncompleteRepository()

@pytest.mark.asyncio
async def test_concrete_subclass():
    """Test that a concrete subclass implementing all methods works correctly."""
    class ConcreteRepository(CycleCountRepository):
        async def save_plan(self, plan: CycleCountPlan) -> CycleCountPlan:
            return plan

        async def get_active_plans(self, tenant_id: str) -> List[CycleCountPlan]:
            return []

        async def save_record(self, record: CycleCountRecord) -> CycleCountRecord:
            return record

        async def save_records(self, records: List[CycleCountRecord]) -> List[CycleCountRecord]:
            return records

        async def get_record(self, record_id: str) -> CycleCountRecord:
            return None

        async def get_assigned_records(self, operator_id: str) -> List[CycleCountRecord]:
            return []

        async def save_line_items(self, items: List['CycleCountLineItem']) -> None:
            pass

        async def get_record_line_items(self, record_id: str) -> List['CycleCountLineItem']:
            return []

        async def get_records_line_items(self, record_ids: List[str]) -> List['CycleCountLineItem']:
            return []


    # Should not raise any errors
    repo = ConcreteRepository()

    plan = CycleCountPlan(tenant_id="test", name="test", abc_classification="A", frequency_days=30)
    assert await repo.save_plan(plan) == plan

    assert await repo.get_active_plans("test") == []

    record = CycleCountRecord(tenant_id="test", name="test", status="PENDING", abc_classification="A")
    assert await repo.save_record(record) == record

    assert await repo.save_records([record]) == [record]
