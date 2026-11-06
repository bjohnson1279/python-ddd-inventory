import pytest
from src.domain.aging.entity import (
    DeadStockAnalysis,
    AgingBucket,
    RecommendedAction
)
from src.domain.product.value_objects import SKU
from src.domain.product.exceptions import InvalidSKUError


def test_dead_stock_analysis_coerces_string_to_sku():
    analysis = DeadStockAnalysis(
        sku="TEST-SKU-1",
        location_id="LOC-1",
        tenant_id="TENANT-1",
        current_quantity=20,
        days_since_last_movement=200,
        is_dead_stock=True,
        aging_bucket=AgingBucket.OVER_180_DAYS,
        locked_capital_cents=50000,
        recommended_action=RecommendedAction.LIQUIDATE
    )
    assert isinstance(analysis.sku, SKU)
    assert analysis.sku == SKU("TEST-SKU-1")
    assert analysis.sku.value == "TEST-SKU-1"


def test_dead_stock_analysis_accepts_sku_value_object():
    sku_obj = SKU("TEST-SKU-VO")
    analysis = DeadStockAnalysis(
        sku=sku_obj,
        location_id="LOC-2",
        tenant_id="TENANT-1",
        current_quantity=5,
        days_since_last_movement=190,
        is_dead_stock=True,
        aging_bucket=AgingBucket.OVER_180_DAYS,
        locked_capital_cents=10000,
        recommended_action=RecommendedAction.DONATE
    )
    assert analysis.sku == sku_obj
    assert analysis.sku.value == "TEST-SKU-VO"


def test_dead_stock_analysis_invalid_sku_raises():
    with pytest.raises(InvalidSKUError):
        DeadStockAnalysis(
            sku="invalid sku with spaces!",
            location_id="LOC-1",
            tenant_id="TENANT-1",
            current_quantity=10,
            days_since_last_movement=100,
            is_dead_stock=False,
            aging_bucket=AgingBucket.DAYS_91_180,
            locked_capital_cents=20000,
            recommended_action=RecommendedAction.NONE
        )

    with pytest.raises(InvalidSKUError):
        DeadStockAnalysis(
            sku="",
            location_id="LOC-1",
            tenant_id="TENANT-1",
            current_quantity=10,
            days_since_last_movement=100,
            is_dead_stock=False,
            aging_bucket=AgingBucket.DAYS_91_180,
            locked_capital_cents=20000,
            recommended_action=RecommendedAction.NONE
        )
