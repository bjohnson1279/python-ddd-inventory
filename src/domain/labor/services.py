import uuid
import math
from datetime import date
from .entity import (
    OperatorPerformanceKpi, PredictiveStaffingSchedule
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
        
        if any(type(value) is not int or value < 0 for value in (total_picks, accurate_counts, total_counts)):
            raise ValueError('Activity counts must be nonnegative integers')
        if any(not math.isfinite(value) or value < 0 for value in (hours_worked, distance_meters)) or accurate_counts > total_counts:
            raise ValueError('Invalid activity measurements')
        if total_picks and hours_worked == 0:
            raise ValueError('Picks require a positive measured duration')
        picks_per_hour = total_picks / hours_worked if hours_worked > 0 else None
        accuracy = (accurate_counts / total_counts) * 100.0 if total_counts > 0 else None
        
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
        if any(type(value) is not int or value < 0 for value in (projected_inbound_volume, projected_outbound_volume)):
            raise ValueError('Projected volumes must be nonnegative integers')
        if any(not math.isfinite(value) or value <= 0 for value in (average_operator_target_picks, shift_duration_hours)):
            raise ValueError('Staffing target and duration must be finite and positive')
        total_volume = projected_inbound_volume + projected_outbound_volume
        picks_per_shift = average_operator_target_picks * shift_duration_hours
        if not math.isfinite(picks_per_shift) or picks_per_shift <= 0:
            raise ValueError('Invalid staffing capacity')
        recommended_headcount = math.ceil(total_volume / picks_per_shift)

        return PredictiveStaffingSchedule(
            schedule_id=str(uuid.uuid4()),
            target_date=target_date,
            projected_inbound_volume=projected_inbound_volume,
            projected_outbound_volume=projected_outbound_volume,
            recommended_headcount=recommended_headcount
        )
