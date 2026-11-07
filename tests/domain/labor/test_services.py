import math
from datetime import date
import pytest

from src.domain.labor.entity import ScheduleStatus
from src.domain.labor.services import (
    OperatorPerformanceService,
    PredictiveSchedulingEngine,
)


# ============================================================================
# OperatorPerformanceService Tests
# ============================================================================

def test_calculate_daily_kpi_standard_scenario():
    # Arrange
    service = OperatorPerformanceService()
    target_date = date(2026, 10, 15)

    # Act
    kpi = service.calculate_daily_kpi(
        operator_id="OP-001",
        target_date=target_date,
        total_picks=600,
        hours_worked=7.5,
        accurate_counts=45,
        total_counts=50,
        distance_meters=8500.0,
    )

    # Assert
    assert kpi.operator_id == "OP-001"
    assert kpi.target_date == target_date
    assert kpi.actual_picks_per_hour == 80.0
    assert kpi.cycle_count_accuracy_percent == 90.0
    assert kpi.traversal_distance_meters == 8500.0


def test_calculate_daily_kpi_zero_picks_with_zero_hours():
    # When no picks and zero hours worked, actual_picks_per_hour should be None
    service = OperatorPerformanceService()
    kpi = service.calculate_daily_kpi(
        operator_id="OP-002",
        target_date=date(2026, 10, 15),
        total_picks=0,
        hours_worked=0.0,
        accurate_counts=0,
        total_counts=0,
        distance_meters=0.0,
    )
    assert kpi.actual_picks_per_hour is None
    assert kpi.cycle_count_accuracy_percent is None
    assert kpi.traversal_distance_meters == 0.0


def test_calculate_daily_kpi_positive_picks_zero_hours_raises():
    # Positive picks with zero hours worked should raise ValueError
    service = OperatorPerformanceService()
    with pytest.raises(ValueError, match="Picks require a positive measured duration"):
        service.calculate_daily_kpi(
            operator_id="OP-003",
            target_date=date(2026, 10, 15),
            total_picks=10,
            hours_worked=0.0,
            accurate_counts=0,
            total_counts=0,
            distance_meters=100.0,
        )


def test_calculate_daily_kpi_boundary_zero_counts():
    # When total_counts is 0, cycle_count_accuracy_percent should be None
    service = OperatorPerformanceService()
    kpi = service.calculate_daily_kpi(
        operator_id="OP-004",
        target_date=date(2026, 10, 15),
        total_picks=100,
        hours_worked=4.0,
        accurate_counts=0,
        total_counts=0,
        distance_meters=500.0,
    )
    assert kpi.actual_picks_per_hour == 25.0
    assert kpi.cycle_count_accuracy_percent is None


def test_calculate_daily_kpi_accuracy_boundaries():
    service = OperatorPerformanceService()

    # 100% accuracy boundary
    kpi_100 = service.calculate_daily_kpi(
        operator_id="OP-005",
        target_date=date(2026, 10, 15),
        total_picks=0,
        hours_worked=1.0,
        accurate_counts=50,
        total_counts=50,
        distance_meters=100.0,
    )
    assert kpi_100.cycle_count_accuracy_percent == 100.0

    # 0% accuracy boundary
    kpi_0 = service.calculate_daily_kpi(
        operator_id="OP-005",
        target_date=date(2026, 10, 15),
        total_picks=0,
        hours_worked=1.0,
        accurate_counts=0,
        total_counts=50,
        distance_meters=100.0,
    )
    assert kpi_0.cycle_count_accuracy_percent == 0.0


@pytest.mark.parametrize(
    "total_picks,accurate_counts,total_counts",
    [
        (-1, 5, 10),
        (10, -1, 10),
        (10, 5, -1),
        ("10", 5, 10),
        (10.5, 5, 10),
        (10, 5.0, 10),
        (10, 5, 10.0),
        (True, 5, 10),  # In Python, bool is a subclass of int, but type(True) is bool != int
    ],
)
def test_calculate_daily_kpi_rejects_non_integer_or_negative_counts(
    total_picks, accurate_counts, total_counts
):
    service = OperatorPerformanceService()
    with pytest.raises(ValueError, match="Activity counts must be nonnegative integers"):
        service.calculate_daily_kpi(
            operator_id="OP-ERR",
            target_date=date(2026, 10, 15),
            total_picks=total_picks,
            hours_worked=8.0,
            accurate_counts=accurate_counts,
            total_counts=total_counts,
            distance_meters=1000.0,
        )


@pytest.mark.parametrize(
    "hours_worked,distance_meters,accurate_counts,total_counts",
    [
        (-0.5, 100.0, 5, 10),
        (8.0, -1.0, 5, 10),
        (float("nan"), 100.0, 5, 10),
        (8.0, float("nan"), 5, 10),
        (float("inf"), 100.0, 5, 10),
        (8.0, float("inf"), 5, 10),
        (8.0, 100.0, 11, 10),  # accurate_counts > total_counts
    ],
)
def test_calculate_daily_kpi_rejects_invalid_measurements(
    hours_worked, distance_meters, accurate_counts, total_counts
):
    service = OperatorPerformanceService()
    with pytest.raises(ValueError, match="Invalid activity measurements"):
        service.calculate_daily_kpi(
            operator_id="OP-ERR",
            target_date=date(2026, 10, 15),
            total_picks=100,
            hours_worked=hours_worked,
            accurate_counts=accurate_counts,
            total_counts=total_counts,
            distance_meters=distance_meters,
        )


# ============================================================================
# PredictiveSchedulingEngine Tests
# ============================================================================

def test_generate_staffing_recommendation_standard_round_up():
    engine = PredictiveSchedulingEngine()
    target_date = date(2026, 11, 1)

    # 1000 inbound + 1005 outbound = 2005 volume.
    # 100 picks/hr * 8 hrs = 800 picks per shift.
    # 2005 / 800 = 2.50625 -> ceil is 3.
    schedule = engine.generate_staffing_recommendation(
        target_date=target_date,
        projected_inbound_volume=1000,
        projected_outbound_volume=1005,
        average_operator_target_picks=100.0,
        shift_duration_hours=8.0,
    )

    assert schedule.target_date == target_date
    assert schedule.projected_inbound_volume == 1000
    assert schedule.projected_outbound_volume == 1005
    assert schedule.recommended_headcount == 3
    assert schedule.status == ScheduleStatus.DRAFT
    assert isinstance(schedule.schedule_id, str)
    assert len(schedule.schedule_id) > 0


def test_generate_staffing_recommendation_exact_multiple():
    engine = PredictiveSchedulingEngine()
    # 400 + 400 = 800 volume.
    # 100 * 8 = 800 picks per shift.
    # 800 / 800 = 1.0 -> ceil is 1.
    schedule = engine.generate_staffing_recommendation(
        target_date=date(2026, 11, 1),
        projected_inbound_volume=400,
        projected_outbound_volume=400,
        average_operator_target_picks=100.0,
        shift_duration_hours=8.0,
    )
    assert schedule.recommended_headcount == 1


def test_generate_staffing_recommendation_zero_total_volume():
    engine = PredictiveSchedulingEngine()
    # 0 inbound + 0 outbound = 0 volume -> ceil(0 / 800) = 0 headcount.
    schedule = engine.generate_staffing_recommendation(
        target_date=date(2026, 11, 1),
        projected_inbound_volume=0,
        projected_outbound_volume=0,
        average_operator_target_picks=50.0,
        shift_duration_hours=8.0,
    )
    assert schedule.recommended_headcount == 0


def test_generate_staffing_recommendation_custom_shift_duration():
    engine = PredictiveSchedulingEngine()
    # 1000 total volume. 50 picks/hr * 10 hrs = 500 per shift.
    # 1000 / 500 = 2 headcount.
    schedule = engine.generate_staffing_recommendation(
        target_date=date(2026, 11, 1),
        projected_inbound_volume=500,
        projected_outbound_volume=500,
        average_operator_target_picks=50.0,
        shift_duration_hours=10.0,
    )
    assert schedule.recommended_headcount == 2


@pytest.mark.parametrize(
    "inbound,outbound",
    [
        (-1, 100),
        (100, -1),
        (-5, -5),
        ("100", 100),
        (100, "100"),
        (100.5, 100),
        (100, 100.5),
        (True, 100),
        (100, False),
    ],
)
def test_generate_staffing_recommendation_rejects_invalid_volume_types_and_values(
    inbound, outbound
):
    engine = PredictiveSchedulingEngine()
    with pytest.raises(ValueError, match="Projected volumes must be nonnegative integers"):
        engine.generate_staffing_recommendation(
            target_date=date(2026, 11, 1),
            projected_inbound_volume=inbound,
            projected_outbound_volume=outbound,
            average_operator_target_picks=80.0,
            shift_duration_hours=8.0,
        )


@pytest.mark.parametrize(
    "target_picks,duration",
    [
        (0.0, 8.0),
        (-10.0, 8.0),
        (80.0, 0.0),
        (80.0, -8.0),
        (float("nan"), 8.0),
        (80.0, float("nan")),
        (float("inf"), 8.0),
        (80.0, float("inf")),
    ],
)
def test_generate_staffing_recommendation_rejects_non_finite_or_non_positive_parameters(
    target_picks, duration
):
    engine = PredictiveSchedulingEngine()
    with pytest.raises(
        ValueError, match="Staffing target and duration must be finite and positive"
    ):
        engine.generate_staffing_recommendation(
            target_date=date(2026, 11, 1),
            projected_inbound_volume=100,
            projected_outbound_volume=100,
            average_operator_target_picks=target_picks,
            shift_duration_hours=duration,
        )


def test_generate_staffing_recommendation_rejects_subnormal_capacity():
    # If target_picks * shift_duration underflows to 0 or becomes non-finite
    engine = PredictiveSchedulingEngine()
    with pytest.raises(ValueError, match="Invalid staffing capacity"):
        # picks_per_shift <= 0 through extremely tiny positive floats whose product underflows
        engine.generate_staffing_recommendation(
            target_date=date(2026, 11, 1),
            projected_inbound_volume=100,
            projected_outbound_volume=100,
            average_operator_target_picks=1e-200,
            shift_duration_hours=1e-200,
        )
