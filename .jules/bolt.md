## 2024-05-24 - O(1) Indexing for Order Routing Engine
**Bottleneck:**
The `OrderRoutingEngine` in `src/domain/routing.py` used an inefficient O(N) list traversal for fetching available quantities (`_get_available_qty`) and for deducting stock levels during order routing.
With large datasets (e.g., thousands of locations and SKUs, and millions of stock levels), looping through `self.stock_levels` for each query significantly degraded performance.

**Learning:**
Trading memory for computation time by building an upfront dictionary index `{(stock.location_id, stock.sku): stock}` during the initialization step reduces access complexity from O(N) to O(1).
This fundamental optimization reduced simulation time for 100 orders and 100,000 stock levels from ~214 seconds to ~0.18 seconds (an 1100x improvement).
Always analyze search paths in nested loops to ensure O(1) time complexity where possible.
