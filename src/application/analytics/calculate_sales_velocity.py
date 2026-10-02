from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

class CalculateSalesVelocity:
    """Calculates sales velocity (ADS) and run-out dates based on dispatch history."""

    async def execute(
        self, 
        sku: str, 
        location_id: str, 
        current_stock: int, 
        history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        now = datetime.utcnow()
        
        # Define intervals
        seven_days_ago = now - timedelta(days=7)
        thirty_days_ago = now - timedelta(days=30)
        
        # Bolt Optimization: Avoid multiple O(N) list traversals when calculating aggregate metrics.
        # Combine aggregations into a single O(N) pass to accumulate all values simultaneously.
        sum_7d = 0
        sum_30d = 0
        sum_90d = 0
        for r in history:
            qty = r.get('quantity', 0)
            dispatched_at = r.get('dispatched_at', now)

            sum_90d += qty
            if dispatched_at >= thirty_days_ago:
                sum_30d += qty
                if dispatched_at >= seven_days_ago:
                    sum_7d += qty

        ads_7d = round(sum_7d / 7.0, 3)
        ads_30d = round(sum_30d / 30.0, 3)
        ads_90d = round(sum_90d / 90.0, 3)

        days_of_cover = float('inf')
        run_out_date = None

        if ads_30d > 0:
            import math
            days_of_cover = math.ceil(current_stock / ads_30d)
            run_out_date = now + timedelta(days=days_of_cover)

        return {
            "sku": sku,
            "location_id": location_id,
            "current_stock": current_stock,
            "average_daily_sales_7d": ads_7d,
            "average_daily_sales_30d": ads_30d,
            "average_daily_sales_90d": ads_90d,
            "days_of_cover": days_of_cover,
            "run_out_date": run_out_date
        }
