import pytest
from src.domain.aging.entity import AgingBucket, RecommendedAction, DeadStockAnalysis
from src.domain.aging.services import DeadStockRecommendationEngine

def test_dead_stock_threshold_boundary():
    engine = DeadStockRecommendationEngine()

    # Exactly 180 days -> not dead stock
    result_180 = engine.analyze(
        sku="SKU-001",
        location_id="LOC-A",
        tenant_id="TENANT-1",
        current_quantity=15,
        unit_cost_cents=1000,
        days_since_last_movement=180,
        aging_bucket=AgingBucket.DAYS_91_180,
        has_overstock=False
    )
    assert result_180.is_dead_stock is False
    assert result_180.recommended_action == RecommendedAction.NONE

    # 181 days -> dead stock
    result_181 = engine.analyze(
        sku="SKU-001",
        location_id="LOC-A",
        tenant_id="TENANT-1",
        current_quantity=15,
        unit_cost_cents=1000,
        days_since_last_movement=181,
        aging_bucket=AgingBucket.OVER_180_DAYS,
        has_overstock=False
    )
    assert result_181.is_dead_stock is True

def test_dead_stock_liquidate_recommendation():
    engine = DeadStockRecommendationEngine()

    # Dead stock with current_quantity > 10 -> LIQUIDATE
    result = engine.analyze(
        sku="SKU-002",
        location_id="LOC-B",
        tenant_id="TENANT-1",
        current_quantity=11,
        unit_cost_cents=2500,
        days_since_last_movement=200,
        aging_bucket=AgingBucket.OVER_180_DAYS,
        has_overstock=False
    )
    assert result.is_dead_stock is True
    assert result.recommended_action == RecommendedAction.LIQUIDATE

def test_dead_stock_donate_recommendation():
    engine = DeadStockRecommendationEngine()

    # Dead stock with current_quantity == 10 -> DONATE
    result_10 = engine.analyze(
        sku="SKU-003",
        location_id="LOC-B",
        tenant_id="TENANT-1",
        current_quantity=10,
        unit_cost_cents=1500,
        days_since_last_movement=190,
        aging_bucket=AgingBucket.OVER_180_DAYS,
        has_overstock=False
    )
    assert result_10.is_dead_stock is True
    assert result_10.recommended_action == RecommendedAction.DONATE

    # Dead stock with current_quantity == 1 -> DONATE
    result_1 = engine.analyze(
        sku="SKU-003",
        location_id="LOC-B",
        tenant_id="TENANT-1",
        current_quantity=1,
        unit_cost_cents=1500,
        days_since_last_movement=190,
        aging_bucket=AgingBucket.OVER_180_DAYS,
        has_overstock=False
    )
    assert result_1.is_dead_stock is True
    assert result_1.recommended_action == RecommendedAction.DONATE

def test_overstock_markdown_recommendation():
    engine = DeadStockRecommendationEngine()

    # Not dead stock, aging_bucket == DAYS_91_180, has_overstock == True -> MARKDOWN
    result = engine.analyze(
        sku="SKU-004",
        location_id="LOC-C",
        tenant_id="TENANT-2",
        current_quantity=50,
        unit_cost_cents=800,
        days_since_last_movement=120,
        aging_bucket=AgingBucket.DAYS_91_180,
        has_overstock=True
    )
    assert result.is_dead_stock is False
    assert result.recommended_action == RecommendedAction.MARKDOWN

def test_overstock_without_markdown_conditions():
    engine = DeadStockRecommendationEngine()

    # aging_bucket == DAYS_91_180 but has_overstock == False -> NONE
    result_no_overstock = engine.analyze(
        sku="SKU-005",
        location_id="LOC-C",
        tenant_id="TENANT-2",
        current_quantity=50,
        unit_cost_cents=800,
        days_since_last_movement=120,
        aging_bucket=AgingBucket.DAYS_91_180,
        has_overstock=False
    )
    assert result_no_overstock.recommended_action == RecommendedAction.NONE

    # has_overstock == True but aging_bucket == DAYS_61_90 -> NONE
    result_wrong_bucket = engine.analyze(
        sku="SKU-005",
        location_id="LOC-C",
        tenant_id="TENANT-2",
        current_quantity=50,
        unit_cost_cents=800,
        days_since_last_movement=75,
        aging_bucket=AgingBucket.DAYS_61_90,
        has_overstock=True
    )
    assert result_wrong_bucket.recommended_action == RecommendedAction.NONE

    # has_overstock == True but aging_bucket == DAYS_0_30 -> NONE
    result_recent = engine.analyze(
        sku="SKU-005",
        location_id="LOC-C",
        tenant_id="TENANT-2",
        current_quantity=50,
        unit_cost_cents=800,
        days_since_last_movement=15,
        aging_bucket=AgingBucket.DAYS_0_30,
        has_overstock=True
    )
    assert result_recent.recommended_action == RecommendedAction.NONE

def test_locked_capital_calculation():
    engine = DeadStockRecommendationEngine()

    # Standard capital calculation
    result = engine.analyze(
        sku="SKU-006",
        location_id="LOC-D",
        tenant_id="TENANT-3",
        current_quantity=20,
        unit_cost_cents=1250,
        days_since_last_movement=45,
        aging_bucket=AgingBucket.DAYS_31_60,
        has_overstock=False
    )
    assert result.locked_capital_cents == 25000  # 20 * 1250

    # Zero quantity
    result_zero_qty = engine.analyze(
        sku="SKU-006",
        location_id="LOC-D",
        tenant_id="TENANT-3",
        current_quantity=0,
        unit_cost_cents=1250,
        days_since_last_movement=45,
        aging_bucket=AgingBucket.DAYS_31_60,
        has_overstock=False
    )
    assert result_zero_qty.locked_capital_cents == 0

def test_dead_stock_analysis_dataclass_fields():
    engine = DeadStockRecommendationEngine()

    analysis = engine.analyze(
        sku="SKU-TEST",
        location_id="LOC-TEST",
        tenant_id="TENANT-TEST",
        current_quantity=42,
        unit_cost_cents=500,
        days_since_last_movement=100,
        aging_bucket=AgingBucket.DAYS_91_180,
        has_overstock=True
    )

    assert isinstance(analysis, DeadStockAnalysis)
    assert analysis.sku == "SKU-TEST"
    assert analysis.location_id == "LOC-TEST"
    assert analysis.tenant_id == "TENANT-TEST"
    assert analysis.current_quantity == 42
    assert analysis.days_since_last_movement == 100
    assert analysis.is_dead_stock is False
    assert analysis.aging_bucket == AgingBucket.DAYS_91_180
    assert analysis.locked_capital_cents == 21000
    assert analysis.recommended_action == RecommendedAction.MARKDOWN
