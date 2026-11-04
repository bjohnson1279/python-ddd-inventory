import pytest
from src.domain.yield_management.entity import (
    LiquidationProfile,
    InventoryYieldMetrics,
    PriceMarkdownRecommendation,
    MarkdownStatus
)
from src.domain.product.value_objects import SKU
from src.domain.product.exceptions import InvalidSKUError


def test_liquidation_profile_coerces_string_to_sku():
    profile = LiquidationProfile(
        sku="TEST-SKU-1",
        base_price_cents=10000,
        holding_cost_per_day_cents=20,
        min_floor_price_cents=3000
    )
    assert isinstance(profile.sku, SKU)
    assert profile.sku == SKU("TEST-SKU-1")
    assert profile.sku.value == "TEST-SKU-1"


def test_liquidation_profile_accepts_sku_value_object():
    sku_obj = SKU("TEST-SKU-VO")
    profile = LiquidationProfile(
        sku=sku_obj,
        base_price_cents=10000,
        holding_cost_per_day_cents=20,
        min_floor_price_cents=3000
    )
    assert profile.sku is sku_obj
    assert profile.sku.value == "TEST-SKU-VO"


def test_liquidation_profile_invalid_sku_raises():
    with pytest.raises(InvalidSKUError):
        LiquidationProfile(
            sku="invalid sku with spaces!",
            base_price_cents=10000,
            holding_cost_per_day_cents=20,
            min_floor_price_cents=3000
        )


def test_inventory_yield_metrics_coerces_string_to_sku():
    metrics = InventoryYieldMetrics(
        sku="METRIC-SKU-1",
        days_in_inventory=40,
        days_until_expiration=25,
        historical_daily_demand=3.5,
        current_stock_quantity=150
    )
    assert isinstance(metrics.sku, SKU)
    assert metrics.sku == SKU("METRIC-SKU-1")
    assert metrics.sku.value == "METRIC-SKU-1"


def test_inventory_yield_metrics_accepts_sku_value_object():
    sku_obj = SKU("METRIC-SKU-VO")
    metrics = InventoryYieldMetrics(
        sku=sku_obj,
        days_in_inventory=40,
        days_until_expiration=25,
        historical_daily_demand=3.5,
        current_stock_quantity=150
    )
    assert metrics.sku is sku_obj
    assert metrics.sku.value == "METRIC-SKU-VO"


def test_inventory_yield_metrics_invalid_sku_raises():
    with pytest.raises(InvalidSKUError):
        InventoryYieldMetrics(
            sku="",
            days_in_inventory=40,
            days_until_expiration=25,
            historical_daily_demand=3.5,
            current_stock_quantity=150
        )


def test_price_markdown_recommendation_coerces_string_to_sku():
    rec = PriceMarkdownRecommendation(
        id="rec-123",
        sku="REC-SKU-1",
        recommended_price_cents=4500,
        reasoning="Holding cost accumulation"
    )
    assert isinstance(rec.sku, SKU)
    assert rec.sku == SKU("REC-SKU-1")
    assert rec.sku.value == "REC-SKU-1"
    assert rec.status == MarkdownStatus.PROPOSED


def test_price_markdown_recommendation_accepts_sku_value_object():
    sku_obj = SKU("REC-SKU-VO")
    rec = PriceMarkdownRecommendation(
        id="rec-123",
        sku=sku_obj,
        recommended_price_cents=4500,
        reasoning="Holding cost accumulation"
    )
    assert rec.sku is sku_obj
    assert rec.sku.value == "REC-SKU-VO"


def test_price_markdown_recommendation_invalid_sku_raises():
    with pytest.raises(InvalidSKUError):
        PriceMarkdownRecommendation(
            id="rec-invalid",
            sku="bad_sku_lower",
            recommended_price_cents=4500,
            reasoning="Invalid SKU format"
        )


def test_price_markdown_recommendation_lifecycle_transitions():
    rec = PriceMarkdownRecommendation(
        id="rec-lifecycle",
        sku="SKU-PROMO",
        recommended_price_cents=5000,
        reasoning="Clearance"
    )
    assert rec.status == MarkdownStatus.PROPOSED

    # Cannot push directly from PROPOSED
    with pytest.raises(ValueError, match="Can only push APPROVED markdowns"):
        rec.push_to_channels()

    rec.approve()
    assert rec.status == MarkdownStatus.APPROVED

    # Cannot re-approve once APPROVED
    with pytest.raises(ValueError, match="Can only approve PROPOSED markdowns"):
        rec.approve()

    rec.push_to_channels()
    assert rec.status == MarkdownStatus.PUSHED_TO_CHANNELS
