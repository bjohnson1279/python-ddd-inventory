import pytest
from datetime import date
from src.domain.labor.entity import ScheduleStatus
from src.domain.labor.services import OperatorPerformanceService, PredictiveSchedulingEngine

def test_operator_performance_service():
    service = OperatorPerformanceService()
    
    kpi = service.calculate_daily_kpi(
        operator_id="OP1",
        target_date=date(2026, 10, 1),
        total_picks=800,
        hours_worked=8.0,
        accurate_counts=48,
        total_counts=50,
        distance_meters=15000.0
    )
    
    assert kpi.actual_picks_per_hour == 100.0
    assert kpi.cycle_count_accuracy_percent == 96.0

def test_predictive_scheduling_engine():
    engine = PredictiveSchedulingEngine()
    
    schedule = engine.generate_staffing_recommendation(
        target_date=date(2026, 10, 2),
        projected_inbound_volume=2000,
        projected_outbound_volume=6000,
        average_operator_target_picks=100.0,
        shift_duration_hours=8.0
    ) # Total 8000 volume. 100 * 8 = 800 per shift. 8000 / 800 = 10 headcount
    
    assert schedule.recommended_headcount == 10
    assert schedule.status == ScheduleStatus.DRAFT
    
    schedule.publish()
    assert schedule.status == ScheduleStatus.PUBLISHED
