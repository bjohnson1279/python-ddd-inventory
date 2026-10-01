from datetime import datetime, timezone
from typing import List, Dict, Any
from .entity import AgingBucket, RecommendedAction, DeadStockAnalysis

class InventoryAgingService:
    def calculate_aging_buckets(
        self, 
        sku: str, 
        location_id: str, 
        tenant_id: str,
        current_date: datetime,
        ledger_entries: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        In a real scenario, this would replay LIFO/FIFO to determine the age of each unit.
        For now, we'll do a simplified calculation based on the last positive entry.
        """
        last_receipt_date = None
        for entry in ledger_entries:
            if entry.get("quantity", 0) > 0:
                entry_date = entry["occurred_at"]
                if not last_receipt_date or entry_date > last_receipt_date:
                    last_receipt_date = entry_date
        
        days_old = 0
        if last_receipt_date:
            days_old = (current_date - last_receipt_date).days

        bucket = AgingBucket.DAYS_0_30
        if days_old > 180:
            bucket = AgingBucket.OVER_180_DAYS
        elif days_old > 90:
            bucket = AgingBucket.DAYS_91_180
        elif days_old > 60:
            bucket = AgingBucket.DAYS_61_90
        elif days_old > 30:
            bucket = AgingBucket.DAYS_31_60

        return {
            "days_since_last_movement": max(days_old, 0),
            "bucket": bucket
        }

class DeadStockRecommendationEngine:
    def analyze(
        self,
        sku: str,
        location_id: str,
        tenant_id: str,
        current_quantity: int,
        unit_cost_cents: int,
        days_since_last_movement: int,
        aging_bucket: AgingBucket,
        has_overstock: bool
    ) -> DeadStockAnalysis:
        
        is_dead_stock = days_since_last_movement > 180
        action = RecommendedAction.NONE

        if is_dead_stock:
            action = RecommendedAction.LIQUIDATE if current_quantity > 10 else RecommendedAction.DONATE
        elif aging_bucket == AgingBucket.DAYS_91_180 and has_overstock:
            action = RecommendedAction.MARKDOWN
            
        locked_capital = current_quantity * unit_cost_cents

        return DeadStockAnalysis(
            sku=sku,
            location_id=location_id,
            tenant_id=tenant_id,
            current_quantity=current_quantity,
            days_since_last_movement=days_since_last_movement,
            is_dead_stock=is_dead_stock,
            aging_bucket=aging_bucket,
            locked_capital_cents=locked_capital,
            recommended_action=action
        )

class EsgEmissionsCalculator:
    def calculate_scrap_emissions(self, sku: str, quantity: int, factor_per_unit_kg: float) -> float:
        """
        Returns estimated kg of CO2e generated if stock is scrapped.
        """
        return quantity * factor_per_unit_kg
