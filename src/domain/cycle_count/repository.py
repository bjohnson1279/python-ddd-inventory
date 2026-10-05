from abc import ABC, abstractmethod
from typing import List
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountRecord, CycleCountLineItem

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

    @abstractmethod
    async def get_record(self, record_id: str) -> CycleCountRecord:
        pass

    @abstractmethod
    async def get_assigned_records(self, operator_id: str) -> List[CycleCountRecord]:
        pass

    @abstractmethod
    async def save_line_items(self, items: List['CycleCountLineItem']) -> None:
        pass

    @abstractmethod
    async def get_record_line_items(self, record_id: str) -> List['CycleCountLineItem']:
        pass

    @abstractmethod
    async def get_records_line_items(self, record_ids: List[str]) -> List['CycleCountLineItem']:
        pass
