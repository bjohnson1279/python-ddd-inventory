from typing import List, Dict, Any
from datetime import datetime, timedelta
import math
import asyncio

from src.domain.analytics.repository import AnalyticsRepository
from src.application.analytics.calculate_sales_velocity import CalculateSalesVelocity

class GetDemandPlanningReport:
    def __init__(
        self,
        repository: AnalyticsRepository,
        calculate_sales_velocity: CalculateSalesVelocity
    ):
        self.repository = repository
        self.calculate_sales_velocity = calculate_sales_velocity

    async def execute(self, location_id: str = "default") -> List[Dict[str, Any]]:
        # 1. Fetch data concurrently
        now = datetime.utcnow()
        ninety_days_ago = now - timedelta(days=90)

        (
            inventory_items, 
            policies, 
            forecasts, 
            all_history
        ) = await asyncio.gather(
            self.repository.get_inventory_levels_by_location(location_id),
            self.repository.get_reorder_policies(location_id),
            self.repository.get_active_forecasts(location_id),
            self.repository.fetch_dispatch_history(location_id, ninety_days_ago)
        )

        policy_map = {p.get("sku"): p for p in policies}
        
        # Build active forecasts map (next 30 days)
        end_window = now + timedelta(days=30)
        active_forecasts_map = {}
        for f in forecasts:
            p_end = f.get("period_end", now)
            p_start = f.get("period_start", now)
            sku = f.get("sku")
            if p_end >= now and p_start <= end_window and sku not in active_forecasts_map:
                active_forecasts_map[sku] = f

        # Build history map
        history_map = {}
        for record in all_history:
            sku = record.get("sku")
            if sku not in history_map:
                history_map[sku] = []
            history_map[sku].append(record)

        report_items = []
        for item in inventory_items:
            sku = item.get("sku")
            current_stock = item.get("quantity", 0)

            # Velocity
            sku_history = history_map.get(sku, [])
            velocity = await self.calculate_sales_velocity.execute(sku, location_id, current_stock, sku_history)

            # Policy
            policy = policy_map.get(sku, {})
            reorder_point = policy.get("reorder_point", 10)
            reorder_quantity = policy.get("reorder_quantity", 20)
            safety_stock = policy.get("safety_stock", 5)

            # Forecast
            active_forecast = active_forecasts_map.get(sku)
            if active_forecast:
                forecasted_demand_30d = active_forecast.get("forecasted_quantity", 0)
                confidence_level = active_forecast.get("confidence_level", 0.8)
            else:
                forecasted_demand_30d = math.ceil(velocity.get("average_daily_sales_30d", 0) * 30)
                confidence_level = 0.70 if velocity.get("average_daily_sales_30d", 0) > 0 else 0.50

            # Recommendation
            action_required = current_stock <= reorder_point
            recommended_order_quantity = reorder_quantity if action_required else 0

            report_items.append({
                "sku": sku,
                "location_id": location_id,
                "current_stock": current_stock,
                "average_daily_sales_7d": velocity.get("average_daily_sales_7d"),
                "average_daily_sales_30d": velocity.get("average_daily_sales_30d"),
                "average_daily_sales_90d": velocity.get("average_daily_sales_90d"),
                "days_of_cover": velocity.get("days_of_cover"),
                "run_out_date": velocity.get("run_out_date"),
                "reorder_point": reorder_point,
                "reorder_quantity": reorder_quantity,
                "safety_stock": safety_stock,
                "forecasted_demand_30d": forecasted_demand_30d,
                "confidence_level": confidence_level,
                "action_required": action_required,
                "recommended_order_quantity": recommended_order_quantity
            })

        return report_items
