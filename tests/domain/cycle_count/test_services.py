from datetime import datetime, timedelta, UTC
from src.domain.cycle_count.services import ABCClassificationService, CycleCountScheduler
from src.domain.cycle_count.entity import CycleCountPlan

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
