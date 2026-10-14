from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from datetime import date

class ScheduleStatus(Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"

@dataclass
class OperatorProfile:
    operator_id: str
    tenant_id: str
    target_picks_per_hour: float
    max_consecutive_hours: int
    certifications: List[str] = field(default_factory=list)

@dataclass
class OperatorPerformanceKpi:
    operator_id: str
    target_date: date
    actual_picks_per_hour: float
    cycle_count_accuracy_percent: float
    traversal_distance_meters: float

@dataclass
class PredictiveStaffingSchedule:
    schedule_id: str
    target_date: date
    projected_inbound_volume: int
    projected_outbound_volume: int
    recommended_headcount: int
    status: ScheduleStatus = ScheduleStatus.DRAFT
    
    def publish(self) -> None:
        if self.status != ScheduleStatus.DRAFT:
            raise ValueError("Can only publish DRAFT schedules")
        self.status = ScheduleStatus.PUBLISHED
