import uuid
import math
from typing import List
from datetime import date
from .entity import (
    OperatorProfile, OperatorPerformanceKpi, PredictiveStaffingSchedule
)

class OperatorPerformanceService:
    def calculate_daily_kpi(
        self,
        operator_id: str,
        target_date: date,
        total_picks: int,
        hours_worked: float,
        accurate_counts: int,
        total_counts: int,
        distance_meters: float
    ) -> OperatorPerformanceKpi:
        
        picks_per_hour = total_picks / hours_worked if hours_worked > 0 else 0.0
        accuracy = (accurate_counts / total_counts) * 100.0 if total_counts > 0 else 100.0
        
        return OperatorPerformanceKpi(
            operator_id=operator_id,
            target_date=target_date,
            actual_picks_per_hour=picks_per_hour,
            cycle_count_accuracy_percent=accuracy,
            traversal_distance_meters=distance_meters
        )

class PredictiveSchedulingEngine:
    def generate_staffing_recommendation(
        self,
        target_date: date,
        projected_inbound_volume: int,
        projected_outbound_volume: int,
        average_operator_target_picks: float,
        shift_duration_hours: float = 8.0
    ) -> PredictiveStaffingSchedule:
        
        # Simple heuristic: total volume divided by average picks per shift
        total_volume = projected_inbound_volume + projected_outbound_volume
        
        picks_per_shift = average_operator_target_picks * shift_duration_hours
        
        if picks_per_shift > 0:
            recommended_headcount = math.ceil(total_volume / picks_per_shift)
        else:
            recommended_headcount = 0
            
        return PredictiveStaffingSchedule(
            schedule_id=str(uuid.uuid4()),
            target_date=target_date,
            projected_inbound_volume=projected_inbound_volume,
            projected_outbound_volume=projected_outbound_volume,
            recommended_headcount=recommended_headcount
        )
