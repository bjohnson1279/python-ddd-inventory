from datetime import date
import pytest

from src.domain.labor.entity import (
    OperatorProfile,
    OperatorPerformanceKpi,
    PredictiveStaffingSchedule,
    ScheduleStatus,
)


def test_operator_profile_creation_defaults():
    # Arrange & Act
    profile = OperatorProfile(
        operator_id="OP-001",
        tenant_id="TENANT-A",
        target_picks_per_hour=85.5,
        max_consecutive_hours=8,
    )

    # Assert
    assert profile.operator_id == "OP-001"
    assert profile.tenant_id == "TENANT-A"
    assert profile.target_picks_per_hour == 85.5
    assert profile.max_consecutive_hours == 8
    assert profile.certifications == []


def test_operator_profile_creation_with_certifications():
    # Arrange & Act
    certifications = ["FORKLIFT_TIER_1", "HAZMAT_HANDLING", "COLD_STORAGE"]
    profile = OperatorProfile(
        operator_id="OP-002",
        tenant_id="TENANT-B",
        target_picks_per_hour=110.0,
        max_consecutive_hours=10,
        certifications=certifications,
    )

    # Assert
    assert profile.certifications == certifications
    assert len(profile.certifications) == 3


def test_operator_performance_kpi_creation_complete():
    # Arrange & Act
    kpi = OperatorPerformanceKpi(
        operator_id="OP-100",
        target_date=date(2026, 11, 15),
        actual_picks_per_hour=92.4,
        cycle_count_accuracy_percent=98.5,
        traversal_distance_meters=12450.0,
    )

    # Assert
    assert kpi.operator_id == "OP-100"
    assert kpi.target_date == date(2026, 11, 15)
    assert kpi.actual_picks_per_hour == 92.4
    assert kpi.cycle_count_accuracy_percent == 98.5
    assert kpi.traversal_distance_meters == 12450.0


def test_operator_performance_kpi_optional_fields_none():
    # Arrange & Act
    kpi = OperatorPerformanceKpi(
        operator_id="OP-101",
        target_date=date(2026, 11, 16),
        actual_picks_per_hour=None,
        cycle_count_accuracy_percent=None,
        traversal_distance_meters=0.0,
    )

    # Assert
    assert kpi.operator_id == "OP-101"
    assert kpi.actual_picks_per_hour is None
    assert kpi.cycle_count_accuracy_percent is None
    assert kpi.traversal_distance_meters == 0.0


def test_predictive_staffing_schedule_defaults_and_publish():
    # Arrange & Act
    schedule = PredictiveStaffingSchedule(
        schedule_id="SCHED-001",
        target_date=date(2026, 12, 1),
        projected_inbound_volume=1500,
        projected_outbound_volume=3500,
        recommended_headcount=7,
    )

    # Assert
    assert schedule.schedule_id == "SCHED-001"
    assert schedule.target_date == date(2026, 12, 1)
    assert schedule.projected_inbound_volume == 1500
    assert schedule.projected_outbound_volume == 3500
    assert schedule.recommended_headcount == 7
    assert schedule.status == ScheduleStatus.DRAFT

    # Transition to PUBLISHED
    schedule.publish()
    assert schedule.status == ScheduleStatus.PUBLISHED


def test_predictive_staffing_schedule_publish_non_draft_raises():
    # Arrange
    schedule = PredictiveStaffingSchedule(
        schedule_id="SCHED-002",
        target_date=date(2026, 12, 2),
        projected_inbound_volume=2000,
        projected_outbound_volume=4000,
        recommended_headcount=8,
        status=ScheduleStatus.PUBLISHED,
    )

    # Act & Assert
    with pytest.raises(ValueError, match="Can only publish DRAFT schedules"):
        schedule.publish()


def test_schedule_status_enum_values():
    # Assert
    assert ScheduleStatus.DRAFT.value == "DRAFT"
    assert ScheduleStatus.PUBLISHED.value == "PUBLISHED"
    assert len(ScheduleStatus) == 2
