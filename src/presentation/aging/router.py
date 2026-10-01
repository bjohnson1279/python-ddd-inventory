from fastapi import APIRouter, HTTPException
from typing import Dict, Any
from datetime import datetime, timezone
from src.domain.aging.services import InventoryAgingService, DeadStockRecommendationEngine, EsgEmissionsCalculator

router = APIRouter(prefix="/api/aging", tags=["aging"])

aging_service = InventoryAgingService()
recommendation_engine = DeadStockRecommendationEngine()
esg_calc = EsgEmissionsCalculator()

@router.get("/{sku}")
def analyze_sku_aging(sku: str, location_id: str, tenant_id: str):
    # Mock data that would normally be fetched from a repository
    now = datetime.now(timezone.utc)
    mock_entries = [{"quantity": 50, "occurred_at": datetime(2023, 1, 1, tzinfo=timezone.utc)}]
    
    aging_data = aging_service.calculate_aging_buckets(sku, location_id, tenant_id, now, mock_entries)
    
    analysis = recommendation_engine.analyze(
        sku=sku,
        location_id=location_id,
        tenant_id=tenant_id,
        current_quantity=50,
        unit_cost_cents=1000,
        days_since_last_movement=aging_data["days_since_last_movement"],
        aging_bucket=aging_data["bucket"],
        has_overstock=True
    )
    
    emissions = esg_calc.calculate_scrap_emissions(sku, 50, 1.2)
    
    return {
        "sku": analysis.sku,
        "is_dead_stock": analysis.is_dead_stock,
        "aging_bucket": analysis.aging_bucket.value,
        "days_since_last_movement": analysis.days_since_last_movement,
        "locked_capital_cents": analysis.locked_capital_cents,
        "recommended_action": analysis.recommended_action.value,
        "potential_scrap_emissions_kg": emissions
    }
