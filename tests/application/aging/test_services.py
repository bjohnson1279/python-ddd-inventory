import pytest
from datetime import datetime, timedelta, timezone
from src.domain.aging.entity import AgingBucket, RecommendedAction
from src.domain.aging.services import (
    InventoryAgingService, DeadStockRecommendationEngine, EsgEmissionsCalculator
)

def test_inventory_aging_service():
    service = InventoryAgingService()
    now = datetime.now(timezone.utc)
    
    entries = [
        {"quantity": 10, "occurred_at": now - timedelta(days=200)},
        {"quantity": 5, "occurred_at": now - timedelta(days=100)}
    ]
    
    result = service.calculate_aging_buckets("SKU1", "LOC1", "T1", now, entries)
    assert result["days_since_last_movement"] == 100
    assert result["bucket"] == AgingBucket.DAYS_91_180

def test_inventory_aging_service_edge_cases():
    service = InventoryAgingService()
    now = datetime.now(timezone.utc)

    # Empty entries
    result_empty = service.calculate_aging_buckets("SKU1", "LOC1", "T1", now, [])
    assert result_empty["days_since_last_movement"] == 0
    assert result_empty["bucket"] == AgingBucket.DAYS_0_30

    # No positive quantity
    no_positive = [
        {"quantity": -5, "occurred_at": now - timedelta(days=10)},
        {"quantity": 0, "occurred_at": now - timedelta(days=5)}
    ]
    result_no_pos = service.calculate_aging_buckets("SKU1", "LOC1", "T1", now, no_positive)
    assert result_no_pos["days_since_last_movement"] == 0
    assert result_no_pos["bucket"] == AgingBucket.DAYS_0_30

    # Multiple entries ending with negative/zero after positive
    mixed_entries = [
        {"quantity": 10, "occurred_at": now - timedelta(days=50)},
        {"quantity": 15, "occurred_at": now - timedelta(days=20)},
        {"quantity": -2, "occurred_at": now - timedelta(days=5)}
    ]
    result_mixed = service.calculate_aging_buckets("SKU1", "LOC1", "T1", now, mixed_entries)
    assert result_mixed["days_since_last_movement"] == 20
    assert result_mixed["bucket"] == AgingBucket.DAYS_0_30

def test_dead_stock_recommendation_engine():
    engine = DeadStockRecommendationEngine()
    
    # 200 days old -> Liquidate
    analysis1 = engine.analyze("SKU1", "L1", "T1", 50, 100, 200, AgingBucket.OVER_180_DAYS, False)
    assert analysis1.is_dead_stock is True
    assert analysis1.recommended_action == RecommendedAction.LIQUIDATE
    assert analysis1.locked_capital_cents == 5000
    
    # 200 days old but few items -> Donate
    analysis2 = engine.analyze("SKU1", "L1", "T1", 5, 100, 200, AgingBucket.OVER_180_DAYS, False)
    assert analysis2.recommended_action == RecommendedAction.DONATE

    # 100 days old + overstock -> Markdown
    analysis3 = engine.analyze("SKU1", "L1", "T1", 50, 100, 100, AgingBucket.DAYS_91_180, True)
    assert analysis3.is_dead_stock is False
    assert analysis3.recommended_action == RecommendedAction.MARKDOWN

def test_esg_emissions_calculator():
    calc = EsgEmissionsCalculator()
    emissions = calc.calculate_scrap_emissions("SKU1", 10, 2.5)
    assert emissions == 25.0
