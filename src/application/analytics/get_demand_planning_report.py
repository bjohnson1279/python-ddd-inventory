from typing import List, Dict, Any, Optional
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
        active_forecasts_map = self._build_active_forecasts_map(forecasts, now)
        history_map = self._build_history_map(all_history)

        inventory_items_list = list(inventory_items)
        velocities = await self._calculate_item_velocities(
            inventory_items_list, location_id, history_map
        )

        report_items = []
        for i, item in enumerate(inventory_items_list):
            sku = item.get("sku")
            velocity = velocities[i]
            policy = policy_map.get(sku, {})
            active_forecast = active_forecasts_map.get(sku)

            report_item = self._build_report_item(
                item=item,
                velocity=velocity,
                policy=policy,
                active_forecast=active_forecast,
                location_id=location_id
            )
            report_items.append(report_item)

        return report_items

    def _build_active_forecasts_map(
        self,
        forecasts: List[Dict[str, Any]],
        now: datetime
    ) -> Dict[str, Dict[str, Any]]:
        # Build active forecasts map (next 30 days)
        end_window = now + timedelta(days=30)
        active_forecasts_map = {}
        for f in forecasts:
            p_end = f.get("period_end", now)
            p_start = f.get("period_start", now)
            sku = f.get("sku")
            if p_end >= now and p_start <= end_window and sku not in active_forecasts_map:
                active_forecasts_map[sku] = f
        return active_forecasts_map

    def _build_history_map(
        self,
        all_history: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        # Build history map
        history_map = {}
        for record in all_history:
            sku = record.get("sku")
            if sku not in history_map:
                history_map[sku] = []
            history_map[sku].append(record)
        return history_map

    async def _calculate_item_velocities(
        self,
        inventory_items_list: List[Dict[str, Any]],
        location_id: str,
        history_map: Dict[str, List[Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        # Bolt Optimization: Execute velocity calculations concurrently with chunking
        # Impact: Prevents O(N) blocking network/async delays while avoiding connection pool exhaustion.
        velocities = []
        chunk_size = 50

        for i in range(0, len(inventory_items_list), chunk_size):
            chunk = inventory_items_list[i:i + chunk_size]
            chunk_tasks = []
            for item in chunk:
                sku = item.get("sku")
                current_stock = item.get("quantity", 0)
                sku_history = history_map.get(sku, [])
                chunk_tasks.append(
                    self.calculate_sales_velocity.execute(sku, location_id, current_stock, sku_history)
                )
            velocities.extend(await asyncio.gather(*chunk_tasks))
        return velocities

    def _build_report_item(
        self,
        item: Dict[str, Any],
        velocity: Dict[str, Any],
        policy: Dict[str, Any],
        active_forecast: Optional[Dict[str, Any]],
        location_id: str
    ) -> Dict[str, Any]:
        sku = item.get("sku")
        current_stock = item.get("quantity", 0)

        # Policy
        reorder_point = policy.get("reorder_point", 10)
        reorder_quantity = policy.get("reorder_quantity", 20)
        safety_stock = policy.get("safety_stock", 5)

        # Forecast
        if active_forecast:
            forecasted_demand_30d = active_forecast.get("forecasted_quantity", 0)
            confidence_level = active_forecast.get("confidence_level", 0.8)
        else:
            forecasted_demand_30d = math.ceil(velocity.get("average_daily_sales_30d", 0) * 30)
            confidence_level = 0.70 if velocity.get("average_daily_sales_30d", 0) > 0 else 0.50

        # Recommendation
        action_required = current_stock <= reorder_point
        recommended_order_quantity = reorder_quantity if action_required else 0

        return {
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
        }
