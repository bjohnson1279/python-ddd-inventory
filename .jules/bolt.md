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

## Outbox Worker N+1 and Sequential I/O

**What:** Optimized `_process_outbox` in `src/application/workers/outbox_worker.py`. Replaced sequential `await` operations in a loop with concurrent `asyncio.gather(*tasks)` for I/O operations (Kafka publish and Redis invalidate) and replaced individual `session.delete(event)` calls with a single bulk delete query.

**Why:** Sequential network I/O block each other, unnecessarily slowing down the loop. Additionally, individual database delete queries per row lead to a severe N+1 problem, generating 50 separate delete statements to the database instead of 1.

**Impact:** Substantially reduced latency by parallelizing network I/O calls and minimizing database queries.

**Measurement:** Reduced total processing time for 50 events from ~1.05s to ~0.02s in local simulated benchmarks.


## 2024-05-24 - Webhook Delivery Sequential I/O Bottleneck

**What:** Optimized `WebhookDeliveryEngine.start()` in `src/application/workers/webhook_worker.py`. Replaced sequential `await client.post(...)` inside a loop with concurrent execution using `asyncio.gather(*tasks)`.

**Why:** Sequential network I/O requests blocked the loop, meaning processing time scaled linearly with the size of the webhook queue. By utilizing `asyncio.gather`, we dispatch all HTTP requests concurrently.

**Impact:** Processing time is now bounded by the slowest webhook request instead of the sum of all request times.

**Measurement:** A benchmark enqueueing 100 webhooks with a simulated 50ms latency dropped from ~5.2 seconds sequentially to ~0.19 seconds concurrently.
