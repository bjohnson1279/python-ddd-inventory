from datetime import datetime, timedelta, UTC
from src.domain.cycle_count.services import (
    ABCClassificationService,
    CycleCountScheduler,
    CycleCountExecutionService,
)
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountLineItem

def test_abc_classification_zero_org_value():
    service = ABCClassificationService()
    assert service.classify_sku(100, 0) == 'C'
    assert service.classify_sku(100, -10) == 'C'

def test_abc_classification_custom_thresholds():
    service = ABCClassificationService()
    custom_thresholds = {'a_threshold': 0.80, 'b_threshold': 0.60}
    assert service.classify_sku(85, 100, custom_thresholds) == 'A'
    assert service.classify_sku(70, 100, custom_thresholds) == 'B'
    assert service.classify_sku(50, 100, custom_thresholds) == 'C'
    assert service.get_recommended_frequency('UNKNOWN') == 180

def test_scheduler_with_last_count():
    scheduler = CycleCountScheduler()
    plan = CycleCountPlan(
        tenant_id="tenant-1",
        name="Daily A-Items",
        abc_classification="A",
        frequency_days=30
    )

    now = datetime.now(UTC).replace(tzinfo=None)

    # 1. Last count is recent, should not generate audit
    recent_last_count = now - timedelta(days=10)
    audits = scheduler.generate_audits([plan], {plan.id: recent_last_count})
    assert len(audits) == 0

    # 2. Last count is older than frequency, should generate audit
    old_last_count = now - timedelta(days=40)
    audits = scheduler.generate_audits([plan], {plan.id: old_last_count})
    assert len(audits) == 1
    assert audits[0].plan_id == plan.id


def test_cycle_count_execution_service_process_submission():
    service = CycleCountExecutionService()

    # 1. Item with counted_quantity is None (skipped, doesn't flag variance)
    item_none = CycleCountLineItem(
        record_id="rec-1",
        sku="SKU-NONE",
        expected_quantity=10,
        counted_quantity=None,
    )
    result = service.process_submission([item_none])
    assert result is True
    assert item_none.variance_quantity is None
    assert item_none.status == "PENDING"

    # 2. Item with expected_quantity == 0 and counted_quantity == 0 (0% variance -> MATCHED)
    item_zero_match = CycleCountLineItem(
        record_id="rec-1",
        sku="SKU-ZERO-0",
        expected_quantity=0,
        counted_quantity=0,
    )
    result = service.process_submission([item_zero_match])
    assert result is True
    assert item_zero_match.variance_quantity == 0
    assert item_zero_match.status == "MATCHED"

    # 3. Item with expected_quantity == 0 and counted_quantity > 0 (100% variance -> VARIANCE_FLAGGED)
    item_zero_unexpected = CycleCountLineItem(
        record_id="rec-1",
        sku="SKU-ZERO-1",
        expected_quantity=0,
        counted_quantity=1,
    )
    result = service.process_submission([item_zero_unexpected])
    assert result is False
    assert item_zero_unexpected.variance_quantity == 1
    assert item_zero_unexpected.status == "VARIANCE_FLAGGED"

    # 4. Item within variance threshold (pct_variance <= 0.05 -> MATCHED)
    item_within = CycleCountLineItem(
        record_id="rec-1",
        sku="SKU-MATCH",
        expected_quantity=100,
        counted_quantity=105,
    )
    result = service.process_submission([item_within], variance_threshold_pct=0.05)
    assert result is True
    assert item_within.variance_quantity == 5
    assert item_within.status == "MATCHED"

    # 5. Item exceeding variance threshold (pct_variance > 0.05 -> VARIANCE_FLAGGED)
    item_exceeded = CycleCountLineItem(
        record_id="rec-1",
        sku="SKU-DIFF",
        expected_quantity=100,
        counted_quantity=106,
    )
    result = service.process_submission([item_exceeded], variance_threshold_pct=0.05)
    assert result is False
    assert item_exceeded.variance_quantity == 6
    assert item_exceeded.status == "VARIANCE_FLAGGED"

    # 6. Combined list with one matched and one recount required
    item_ok = CycleCountLineItem(
        record_id="rec-1",
        sku="SKU-OK",
        expected_quantity=50,
        counted_quantity=50,
    )
    item_bad = CycleCountLineItem(
        record_id="rec-1",
        sku="SKU-BAD",
        expected_quantity=50,
        counted_quantity=40,
    )
    result = service.process_submission([item_ok, item_bad])
    assert result is False
    assert item_ok.status == "MATCHED"
    assert item_bad.status == "VARIANCE_FLAGGED"
