import uuid
from datetime import datetime
import pytest
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountRecord, CycleCountLineItem
from src.domain.product.value_objects import SKU
from src.domain.product.exceptions import InvalidSKUError

def test_cycle_count_plan_creation_with_defaults():
    plan1 = CycleCountPlan(
        tenant_id="tenant-123",
        name="Weekly High Value",
        abc_classification="A",
        frequency_days=7
    )
    plan2 = CycleCountPlan(
        tenant_id="tenant-123",
        name="Weekly High Value",
        abc_classification="A",
        frequency_days=7
    )

    assert plan1.tenant_id == "tenant-123"
    assert plan1.name == "Weekly High Value"
    assert plan1.abc_classification == "A"
    assert plan1.frequency_days == 7

    assert plan1.zone is None
    assert plan1.is_active is True

    assert isinstance(plan1.id, str)
    assert isinstance(uuid.UUID(plan1.id), uuid.UUID)
    assert plan1.id != plan2.id

    assert isinstance(plan1.created_at, datetime)
    assert plan1.created_at != plan2.created_at

def test_cycle_count_plan_creation_with_all_args():
    custom_id = str(uuid.uuid4())
    custom_time = datetime(2023, 1, 1, 12, 0, 0)

    plan = CycleCountPlan(
        tenant_id="tenant-456",
        name="Custom Plan",
        abc_classification="B",
        frequency_days=30,
        id=custom_id,
        zone="Zone B",
        is_active=False,
        created_at=custom_time
    )

    assert plan.tenant_id == "tenant-456"
    assert plan.name == "Custom Plan"
    assert plan.abc_classification == "B"
    assert plan.frequency_days == 30
    assert plan.id == custom_id
    assert plan.zone == "Zone B"
    assert plan.is_active is False
    assert plan.created_at == custom_time

def test_cycle_count_record_creation_with_defaults():
    record1 = CycleCountRecord(
        tenant_id="tenant-789",
        name="Count 1",
        status="pending",
        abc_classification="C"
    )
    record2 = CycleCountRecord(
        tenant_id="tenant-789",
        name="Count 1",
        status="pending",
        abc_classification="C"
    )

    assert record1.tenant_id == "tenant-789"
    assert record1.name == "Count 1"
    assert record1.status == "pending"
    assert record1.abc_classification == "C"

    assert record1.is_blind_count is True
    assert record1.plan_id is None
    assert record1.zone is None

    assert isinstance(record1.id, str)
    assert isinstance(uuid.UUID(record1.id), uuid.UUID)
    assert record1.id != record2.id

    assert isinstance(record1.created_at, datetime)
    assert record1.created_at != record2.created_at

def test_cycle_count_record_creation_with_all_args():
    custom_id = str(uuid.uuid4())
    plan_id = str(uuid.uuid4())
    custom_time = datetime(2023, 2, 1, 10, 0, 0)

    record = CycleCountRecord(
        tenant_id="tenant-012",
        name="Count 2",
        status="completed",
        abc_classification="A",
        is_blind_count=False,
        id=custom_id,
        plan_id=plan_id,
        zone="Zone A",
        created_at=custom_time
    )

    assert record.tenant_id == "tenant-012"
    assert record.name == "Count 2"
    assert record.status == "completed"
    assert record.abc_classification == "A"
    assert record.is_blind_count is False
    assert record.id == custom_id
    assert record.plan_id == plan_id
    assert record.zone == "Zone A"
    assert record.created_at == custom_time

def test_cycle_count_line_item_sku_value_object():
    # Test constructing with SKU Value Object directly
    sku_obj = SKU("SKU-100")
    line = CycleCountLineItem(
        record_id="rec-1",
        sku=sku_obj,
        expected_quantity=10
    )

    assert line.sku == sku_obj
    assert isinstance(line.sku, SKU)
    assert line.expected_quantity == 10
    assert line.status == "PENDING"
    assert isinstance(line.id, str)

    # Test constructing with string coercion to SKU Value Object
    line_str = CycleCountLineItem(
        record_id="rec-2",
        sku="SKU-200",
        expected_quantity=5
    )

    assert line_str.sku == SKU("SKU-200")
    assert isinstance(line_str.sku, SKU)
    assert line_str.sku.value == "SKU-200"

    # Test invalid SKU raises InvalidSKUError
    with pytest.raises(InvalidSKUError):
        CycleCountLineItem(
            record_id="rec-3",
            sku="invalid_sku!",
            expected_quantity=1
        )

    with pytest.raises(InvalidSKUError):
        CycleCountLineItem(
            record_id="rec-4",
            sku="",
            expected_quantity=1
        )
