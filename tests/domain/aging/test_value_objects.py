import pytest
from src.domain.product.value_objects import SKU
from src.domain.product.exceptions import InvalidSKUError
from src.domain.aging.value_objects import AgingBucket, RecommendedAction, AgingSKU
from src.domain.aging.entity import DeadStockAnalysis

def test_aging_bucket_values():
    assert AgingBucket.DAYS_0_30 == "0_30_DAYS"
    assert AgingBucket.DAYS_31_60 == "31_60_DAYS"
    assert AgingBucket.DAYS_61_90 == "61_90_DAYS"
    assert AgingBucket.DAYS_91_180 == "91_180_DAYS"
    assert AgingBucket.OVER_180_DAYS == "OVER_180_DAYS"

def test_recommended_action_values():
    assert RecommendedAction.NONE == "NONE"
    assert RecommendedAction.MARKDOWN == "MARKDOWN"
    assert RecommendedAction.LIQUIDATE == "LIQUIDATE"
    assert RecommendedAction.DONATE == "DONATE"
    assert RecommendedAction.SCRAP == "SCRAP"

def test_aging_sku_equality_with_str_and_sku():
    sku = AgingSKU("PROD-100")
    assert sku == "PROD-100"
    assert "PROD-100" == sku
    assert sku == SKU("PROD-100")
    assert SKU("PROD-100") == sku
    assert isinstance(sku, SKU)

def test_dead_stock_analysis_coerces_str_to_sku():
    analysis = DeadStockAnalysis(
        sku="TEST-SKU-1",
        location_id="loc-1",
        tenant_id="tenant-1",
        current_quantity=5,
        days_since_last_movement=20,
        is_dead_stock=False,
        aging_bucket=AgingBucket.DAYS_0_30,
        locked_capital_cents=500,
        recommended_action=RecommendedAction.NONE
    )
    assert isinstance(analysis.sku, SKU)
    assert analysis.sku.value == "TEST-SKU-1"
    assert analysis.sku == "TEST-SKU-1"
    assert analysis.sku == SKU("TEST-SKU-1")

def test_dead_stock_analysis_with_sku_instance():
    sku = SKU("TEST-SKU-2")
    analysis = DeadStockAnalysis(
        sku=sku,
        location_id="loc-1",
        tenant_id="tenant-1",
        current_quantity=5,
        days_since_last_movement=20,
        is_dead_stock=False,
        aging_bucket=AgingBucket.DAYS_0_30,
        locked_capital_cents=500,
        recommended_action=RecommendedAction.NONE
    )
    assert isinstance(analysis.sku, AgingSKU)
    assert analysis.sku == "TEST-SKU-2"
    assert analysis.sku == SKU("TEST-SKU-2")

def test_dead_stock_analysis_with_invalid_sku():
    with pytest.raises(InvalidSKUError):
        DeadStockAnalysis(
            sku="invalid sku with spaces",
            location_id="loc-1",
            tenant_id="tenant-1",
            current_quantity=5,
            days_since_last_movement=20,
            is_dead_stock=False,
            aging_bucket=AgingBucket.DAYS_0_30,
            locked_capital_cents=500,
            recommended_action=RecommendedAction.NONE
        )
