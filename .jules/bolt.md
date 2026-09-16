## 2024-05-24 - O(1) stock lookup in OrderRoutingEngine
**Learning:** Found an $O(N)$ lookup in the `OrderRoutingEngine` for `available_qty` which scales poorly when looking up multiple SKUs and locations.
**Action:** Replace the $O(N)$ lookup with an $O(1)$ lookup using a dictionary mapping `(location_id, sku)` to the `StockLevel` object.

## 2024-05-18 - [Optimization] Avoid dict allocation in get_recommended_frequency

**What:** Moved the dictionary `mapping` inside `ABCClassificationService.get_recommended_frequency` to a class-level constant `_RECOMMENDED_FREQUENCY_MAPPING`.
**Why:** Prevented reallocation and initialization of a static dictionary on every method call, avoiding CPU and memory overhead.
**Impact:** Execution time reduced significantly.
**Measurement:** 10M iterations took 3.0047s before the change, and 1.2414s after the change (a roughly 58% execution time reduction).

## 2024-05-24 - Cycle Count Schedule Audits Batch Insert
**What:** The `schedule_audits` endpoint inside `src/presentation/cycle_count/router.py` generated `audits` and saved them using a for loop `await repo.save_record(audit)`. It was optimized by adding a `save_records` batch insert method into `src/domain/cycle_count/repository.py` and implementing it in `src/infrastructure/cycle_count/repository.py` using `session.add_all()`.
**Why:** Running an individual `save_record` in a loop created an N+1 query issue, which significantly degraded performance as the number of scheduled audits scaled up. A batch insert avoids this issue.
**Impact:** Reduced overhead by inserting all models via `session.add_all()` at once instead of individual `session.add()` inside a loop.
**Measurement:** A quick benchmark on a simulated 1000 record insert reduced the query duration from 1.29s to 0.075s.

