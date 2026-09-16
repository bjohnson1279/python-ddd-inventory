from abc import ABC, abstractmethod
from typing import List
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountRecord

class CycleCountRepository(ABC):
    @abstractmethod
    async def save_plan(self, plan: CycleCountPlan) -> CycleCountPlan:
        pass

    @abstractmethod
    async def get_active_plans(self, tenant_id: str) -> List[CycleCountPlan]:
        pass

    @abstractmethod
    async def save_record(self, record: CycleCountRecord) -> CycleCountRecord:
        pass

    @abstractmethod
    async def save_records(self, records: List[CycleCountRecord]) -> List[CycleCountRecord]:
        pass
