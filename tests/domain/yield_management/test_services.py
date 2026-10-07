import pytest
from src.domain.yield_management.entity import (
    LiquidationProfile, InventoryYieldMetrics
)
from src.domain.yield_management.services import YieldCalculationService


def test_calculate_optimal_price_standard_holding_cost():
    """Verify price reduction based on accrued holding costs."""
    calc = YieldCalculationService()
    profile = LiquidationProfile(
        sku="TEST-SKU-1",
        base_price_cents=10000,
        holding_cost_per_day_cents=20,
        min_floor_price_cents=3000
    )
    metrics = InventoryYieldMetrics(
        sku="TEST-SKU-1",
        days_in_inventory=50,
        days_until_expiration=30,
        historical_daily_demand=5.0,
        current_stock_quantity=100
    )
    # Expected accrued holding cost: 50 days * 20 cents/day = 1000 cents
    # Optimal price: 10000 - 1000 = 9000 cents
    optimal_price = calc.calculate_optimal_price(metrics, profile)
    assert optimal_price == 9000


def test_calculate_optimal_price_floor_price_bound():
    """Verify optimal price does not fall below min_floor_price_cents due to holding cost."""
    calc = YieldCalculationService()
    profile = LiquidationProfile(
        sku="TEST-SKU-2",
        base_price_cents=10000,
        holding_cost_per_day_cents=100,
        min_floor_price_cents=4000
    )
    metrics = InventoryYieldMetrics(
        sku="TEST-SKU-2",
        days_in_inventory=80,
        days_until_expiration=60,
        historical_daily_demand=2.0,
        current_stock_quantity=50
    )
    # Accrued holding cost: 80 * 100 = 8000 cents -> calculated price: 2000 cents
    # Since floor is 4000 cents, optimal price should be 4000 cents
    optimal_price = calc.calculate_optimal_price(metrics, profile)
    assert optimal_price == 4000


def test_calculate_optimal_price_no_expiration_date():
    """Verify calculation when days_until_expiration is None."""
    calc = YieldCalculationService()
    profile = LiquidationProfile(
        sku="TEST-SKU-3",
        base_price_cents=5000,
        holding_cost_per_day_cents=10,
        min_floor_price_cents=1000
    )
    metrics = InventoryYieldMetrics(
        sku="TEST-SKU-3",
        days_in_inventory=20,
        days_until_expiration=None,
        historical_daily_demand=10.0,
        current_stock_quantity=500
    )
    # Accrued holding cost: 20 * 10 = 200 cents
    # Expected price: 5000 - 200 = 4800 cents
    optimal_price = calc.calculate_optimal_price(metrics, profile)
    assert optimal_price == 4800


def test_calculate_optimal_price_near_expiration_overstock():
    """Verify aggressive discount to floor when near expiration (< 14 days) and overstocked."""
    calc = YieldCalculationService()
    profile = LiquidationProfile(
        sku="TEST-SKU-4",
        base_price_cents=8000,
        holding_cost_per_day_cents=5,
        min_floor_price_cents=2500
    )
    metrics = InventoryYieldMetrics(
        sku="TEST-SKU-4",
        days_in_inventory=10,
        days_until_expiration=10,
        historical_daily_demand=2.0,
        current_stock_quantity=50
    )
    # Expected sales = 2.0 demand * 10 days = 20
    # Current stock (50) > expected sales (20) -> aggressive discount to min floor price (2500)
    optimal_price = calc.calculate_optimal_price(metrics, profile)
    assert optimal_price == 2500


def test_calculate_optimal_price_near_expiration_sufficient_demand():
    """Verify near expiration (< 14 days) does not trigger aggressive floor discount if demand is sufficient."""
    calc = YieldCalculationService()
    profile = LiquidationProfile(
        sku="TEST-SKU-5",
        base_price_cents=8000,
        holding_cost_per_day_cents=10,
        min_floor_price_cents=2000
    )
    metrics = InventoryYieldMetrics(
        sku="TEST-SKU-5",
        days_in_inventory=10,
        days_until_expiration=10,
        historical_daily_demand=5.0,
        current_stock_quantity=30
    )
    # Expected sales = 5.0 demand * 10 days = 50
    # Current stock (30) <= expected sales (50) -> standard calculation: 8000 - (10 * 10) = 7900 cents
    optimal_price = calc.calculate_optimal_price(metrics, profile)
    assert optimal_price == 7900


def test_calculate_optimal_price_expiration_boundary_condition():
    """Verify boundary condition when days_until_expiration is exactly 14."""
    calc = YieldCalculationService()
    profile = LiquidationProfile(
        sku="TEST-SKU-6",
        base_price_cents=10000,
        holding_cost_per_day_cents=15,
        min_floor_price_cents=3000
    )
    metrics = InventoryYieldMetrics(
        sku="TEST-SKU-6",
        days_in_inventory=20,
        days_until_expiration=14,
        historical_daily_demand=1.0,
        current_stock_quantity=100
    )
    # 14 < 14 is False, so near expiration check does not trigger
    # Expected price: 10000 - (20 * 15) = 9700 cents
    optimal_price = calc.calculate_optimal_price(metrics, profile)
    assert optimal_price == 9700


def test_calculate_optimal_price_zero_holding_cost():
    """Verify optimal price when days in inventory or holding cost per day is zero."""
    calc = YieldCalculationService()
    profile = LiquidationProfile(
        sku="TEST-SKU-7",
        base_price_cents=12000,
        holding_cost_per_day_cents=0,
        min_floor_price_cents=5000
    )
    metrics = InventoryYieldMetrics(
        sku="TEST-SKU-7",
        days_in_inventory=0,
        days_until_expiration=30,
        historical_daily_demand=10.0,
        current_stock_quantity=50
    )
    # 12000 - 0 = 12000
    optimal_price = calc.calculate_optimal_price(metrics, profile)
    assert optimal_price == 12000
