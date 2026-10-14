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

@pytest.mark.parametrize('target,duration', [(0, 8), (-1, 8), (float('nan'), 8), (100, 0), (100, float('inf'))])
def test_staffing_rejects_invalid_capacity(target, duration):
    with pytest.raises(ValueError):
        PredictiveSchedulingEngine().generate_staffing_recommendation(date.today(), 1, 1, target, duration)

def test_empty_measurements_are_unknown():
    result = OperatorPerformanceService().calculate_daily_kpi('op', date.today(), 0, 0, 0, 0, 0)
    assert result.actual_picks_per_hour is None and result.cycle_count_accuracy_percent is None

@pytest.mark.parametrize('counts,hours,accurate,total,distance', [(-1, 1, 0, 0, 0), (1, 0, 0, 0, 0), (1, float('nan'), 0, 0, 0), (1, 1, 2, 1, 0), (1, 1, 0, 0, -1)])
def test_invalid_measurements(counts, hours, accurate, total, distance):
    with pytest.raises(ValueError):
        OperatorPerformanceService().calculate_daily_kpi('op', date.today(), counts, hours, accurate, total, distance)
