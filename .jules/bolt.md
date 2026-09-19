## 2024-05-24 - O(1) stock lookup in OrderRoutingEngine
**Learning:** Found an $O(N)$ lookup in the `OrderRoutingEngine` for `available_qty` which scales poorly when looking up multiple SKUs and locations.
**Action:** Replace the $O(N)$ lookup with an $O(1)$ lookup using a dictionary mapping `(location_id, sku)` to the `StockLevel` object.

## 2024-05-18 - [Optimization] Avoid dict allocation in get_recommended_frequency

**What:** Moved the dictionary `mapping` inside `ABCClassificationService.get_recommended_frequency` to a class-level constant `_RECOMMENDED_FREQUENCY_MAPPING`.
**Why:** Prevented reallocation and initialization of a static dictionary on every method call, avoiding CPU and memory overhead.
**Impact:** Execution time reduced significantly.
**Measurement:** 10M iterations took 3.0047s before the change, and 1.2414s after the change (a roughly 58% execution time reduction).

