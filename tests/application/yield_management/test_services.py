import pytest
from src.domain.yield_management.entity import (
    LiquidationProfile, InventoryYieldMetrics, MarkdownStatus
)
from src.domain.yield_management.services import YieldCalculationService, DynamicPricingEngine

def test_yield_calculation_service():
    calc = YieldCalculationService()
    profile = LiquidationProfile("SKU1", 10000, 10, 5000) # $100 base, $0.10/day holding, $50 floor
    
    # Just holding costs
    metrics1 = InventoryYieldMetrics("SKU1", 100, 100, 5.0, 500)
    price1 = calc.calculate_optimal_price(metrics1, profile)
    assert price1 == 9000 # 10000 - (100 * 10)
    
    # Near expiration and overstock
    metrics2 = InventoryYieldMetrics("SKU1", 100, 10, 2.0, 500) # Expects to sell 20, but has 500
    price2 = calc.calculate_optimal_price(metrics2, profile)
    assert price2 == 5000 # Hits floor

def test_dynamic_pricing_engine():
    calc = YieldCalculationService()
    engine = DynamicPricingEngine(calc)
    
    profile = LiquidationProfile("SKU1", 10000, 10, 5000)
    metrics = InventoryYieldMetrics("SKU1", 100, 10, 2.0, 500)
    
    rec = engine.generate_markdown(metrics, profile)
    assert rec is not None
    assert rec.recommended_price_cents == 5000
    assert rec.status == MarkdownStatus.PROPOSED
    
    rec.approve()
    assert rec.status == MarkdownStatus.APPROVED
    
    rec.push_to_channels()
    assert rec.status == MarkdownStatus.PUSHED_TO_CHANNELS
