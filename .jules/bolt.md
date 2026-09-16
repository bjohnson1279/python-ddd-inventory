## 2024-05-24 - O(1) stock lookup in OrderRoutingEngine
**Learning:** Found an $O(N)$ lookup in the `OrderRoutingEngine` for `available_qty` which scales poorly when looking up multiple SKUs and locations.
**Action:** Replace the $O(N)$ lookup with an $O(1)$ lookup using a dictionary mapping `(location_id, sku)` to the `StockLevel` object.

## 2024-06-15 - N+1 query bottleneck in OutboxWorker
**Learning:** Sequential `session.delete(event)` inside loops causes severe N+1 query bottlenecks in PostgreSQL, especially for background workers processing batches of events like the OutboxWorker.
**Action:** Always accumulate event IDs to execute a single bulk delete statement using `session.execute(delete(Model).where(Model.id.in_(ids)))` instead of sequentially deleting within a loop.
