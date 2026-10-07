import pytest
from datetime import datetime, timedelta, timezone
from src.domain.aging.entity import AgingBucket
from src.domain.aging.services import InventoryAgingService


@pytest.fixture
def aging_service():
    return InventoryAgingService()


def test_calculate_aging_buckets_empty_ledger(aging_service):
    current_date = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    result = aging_service.calculate_aging_buckets(
        sku="SKU-1",
        location_id="LOC-1",
        tenant_id="TENANT-1",
        current_date=current_date,
        ledger_entries=[]
    )
    assert result == {
        "days_since_last_movement": 0,
        "bucket": AgingBucket.DAYS_0_30
    }


def test_calculate_aging_buckets_only_non_positive_entries(aging_service):
    current_date = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    entries = [
        {"quantity": 0, "occurred_at": current_date - timedelta(days=100)},
        {"quantity": -5, "occurred_at": current_date - timedelta(days=50)},
    ]
    result = aging_service.calculate_aging_buckets(
        sku="SKU-1",
        location_id="LOC-1",
        tenant_id="TENANT-1",
        current_date=current_date,
        ledger_entries=entries
    )
    assert result == {
        "days_since_last_movement": 0,
        "bucket": AgingBucket.DAYS_0_30
    }


def test_calculate_aging_buckets_selects_latest_positive_receipt(aging_service):
    current_date = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    entries = [
        {"quantity": 10, "occurred_at": current_date - timedelta(days=100)},
        {"quantity": 5, "occurred_at": current_date - timedelta(days=20)},
        {"quantity": 15, "occurred_at": current_date - timedelta(days=50)},
        {"quantity": -2, "occurred_at": current_date - timedelta(days=5)},
    ]
    result = aging_service.calculate_aging_buckets(
        sku="SKU-1",
        location_id="LOC-1",
        tenant_id="TENANT-1",
        current_date=current_date,
        ledger_entries=entries
    )
    assert result["days_since_last_movement"] == 20
    assert result["bucket"] == AgingBucket.DAYS_0_30


@pytest.mark.parametrize(
    "days_old, expected_bucket",
    [
        (0, AgingBucket.DAYS_0_30),
        (15, AgingBucket.DAYS_0_30),
        (30, AgingBucket.DAYS_0_30),
        (31, AgingBucket.DAYS_31_60),
        (45, AgingBucket.DAYS_31_60),
        (60, AgingBucket.DAYS_31_60),
        (61, AgingBucket.DAYS_61_90),
        (75, AgingBucket.DAYS_61_90),
        (90, AgingBucket.DAYS_61_90),
        (91, AgingBucket.DAYS_91_180),
        (120, AgingBucket.DAYS_91_180),
        (180, AgingBucket.DAYS_91_180),
        (181, AgingBucket.OVER_180_DAYS),
        (365, AgingBucket.OVER_180_DAYS),
    ]
)
def test_calculate_aging_buckets_all_ranges_and_boundaries(aging_service, days_old, expected_bucket):
    current_date = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    entries = [
        {"quantity": 1, "occurred_at": current_date - timedelta(days=days_old)}
    ]
    result = aging_service.calculate_aging_buckets(
        sku="SKU-1",
        location_id="LOC-1",
        tenant_id="TENANT-1",
        current_date=current_date,
        ledger_entries=entries
    )
    assert result["days_since_last_movement"] == days_old
    assert result["bucket"] == expected_bucket


def test_calculate_aging_buckets_future_date_edge_case(aging_service):
    current_date = datetime(2025, 1, 15, 12, 0, tzinfo=timezone.utc)
    entries = [
        {"quantity": 10, "occurred_at": current_date + timedelta(days=5)}
    ]
    result = aging_service.calculate_aging_buckets(
        sku="SKU-1",
        location_id="LOC-1",
        tenant_id="TENANT-1",
        current_date=current_date,
        ledger_entries=entries
    )
    assert result["days_since_last_movement"] == 0
    assert result["bucket"] == AgingBucket.DAYS_0_30
