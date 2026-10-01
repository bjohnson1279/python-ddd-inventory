import uuid
from .entity import (
    LiquidationProfile, InventoryYieldMetrics, PriceMarkdownRecommendation
)

class YieldCalculationService:
    def calculate_optimal_price(
        self, 
        metrics: InventoryYieldMetrics, 
        profile: LiquidationProfile
    ) -> int:
        
        # Start at base price
        current_price = profile.base_price_cents
        
        # Subtract total accrued holding costs
        accrued_holding_cost = metrics.days_in_inventory * profile.holding_cost_per_day_cents
        current_price -= accrued_holding_cost
        
        # If it's near expiration and we have too much stock to sell at current demand rate
        if metrics.days_until_expiration is not None and metrics.days_until_expiration < 14:
            expected_sales = metrics.historical_daily_demand * metrics.days_until_expiration
            if metrics.current_stock_quantity > expected_sales:
                # Aggressive discount to floor to avoid total loss
                current_price = profile.min_floor_price_cents
                
        # Never go below floor
        return max(current_price, profile.min_floor_price_cents)

class DynamicPricingEngine:
    def __init__(self, calculation_service: YieldCalculationService):
        self.calculation_service = calculation_service
        
    def generate_markdown(
        self,
        metrics: InventoryYieldMetrics,
        profile: LiquidationProfile
    ) -> PriceMarkdownRecommendation:
        
        optimal_price = self.calculation_service.calculate_optimal_price(metrics, profile)
        
        if optimal_price < profile.base_price_cents:
            reasoning = "Accrued holding costs"
            if optimal_price == profile.min_floor_price_cents and metrics.days_until_expiration is not None and metrics.days_until_expiration < 14:
                reasoning = "Approaching Expiration - High Overstock"
                
            return PriceMarkdownRecommendation(
                id=str(uuid.uuid4()),
                sku=metrics.sku,
                recommended_price_cents=optimal_price,
                reasoning=reasoning
            )
        
        return None
