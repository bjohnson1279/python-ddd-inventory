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

## 2024-05-24 - [Optimization] Avoid O(N^2) list removal in WebhookDeliveryEngine._queue
**Learning:** Removing items from a list iteratively (`self._queue.remove(item)`) inside a loop over the items to be processed creates an O(N^2) time complexity, leading to severe performance degradation when processing large queues.
**Action:** Reconstruct the queue in a single O(N) pass, separating items ready to be processed from items that need to remain in the queue.

## 2024-05-18 - [Optimization] Avoid dict allocation in get_recommended_frequency
**What:** Moved the dictionary `mapping` inside `ABCClassificationService.get_recommended_frequency` to a module-level constant `RECOMMENDED_FREQUENCY_MAPPING`.
**Why:** Prevented reallocation and initialization of a static dictionary on every method call, avoiding CPU and memory overhead.
**Impact:** Execution time reduced significantly.
**Measurement:** 10M iterations took 1.272s before the change, and 0.558s after the change (a roughly 56% execution time reduction).

## 2024-05-24 - Asyncio Gather for Sequential I/O loops
**Learning:** Running `await` sequentially in a `for` loop (e.g. `for item in items: await handler(item)`) creates an O(N) blocking operation where each item waits for the previous one to complete. This is highly inefficient for tasks like broadcasting websockets or dispatching events to multiple handlers.
**Action:** Use `asyncio.gather(*tasks)` to run independent IO-bound asynchronous tasks concurrently, drastically reducing the total execution time. Make sure to catch exceptions inside the task wrappers so one failing task does not bring down the entire batch.

## 2024-05-24 - O(NxM) list evaluation in RBAC decorators
**Learning:** Found $O(N \times M)$ list evaluations inside the `requires_roles` (`any(role in required_roles for role in user_roles)`) and `requires_permissions` (`all(perm in user_perms for perm in required_permissions)`) decorators. These are evaluated on every request.
**Action:** Convert the required roles and permissions to sets *outside* the wrapper function (at initialization time) and use set operations (`isdisjoint` and difference `-`) inside the wrapper to reduce the time complexity to $O(N + M)$ and avoid repetitive list traversal.

## 2024-05-24 - [Optimization] Avoid O(N^2) list removal in CrossDockingEngine.process_inbound_asn
**Learning:** Iterating over a list (or a copy of it via `list()`) and calling `list.remove()` inside the loop for items that need to be deleted is an O(N^2) operation. In `CrossDockingEngine`, this caused major performance degradation for large queues of pending orders.
**Action:** Always reconstruct the list in a single O(N) pass by creating a new list, appending the elements that should remain, and reassigning the reference (e.g., `new_list = [item for item in old_list if condition]; old_list = new_list`).


## Prevention Directives for Automated Refactoring
- **Never Overwrite Complete Files**: Always use range-scoped replacement chunks (`StartLine`/`EndLine`).
- **No Scratch Files**: Never stage or commit `test_*.ts`, `test_*.js`, `test.js`, or `plan.md` files to git.
- **No Unresolved Conflict Markers**: Never stage or commit files containing Git merge conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`). Always resolve conflicts cleanly before committing.
- **Path Normalization Compatibility**: When passing paths to subprocesses or external APIs, use absolute normalized paths (e.g., `os.path.abspath`) so tests pass on both Linux and Windows.
- **Zero-Diff Task Termination**: If the requested optimization, refactor, or fix is ALREADY natively present in the target branch, DO NOT create an empty pull request or commit an acknowledgment PR. Exit the task cleanly without opening a PR.

## 2024-05-24 - [Optimization] Avoid repetitive eager evaluation of datetime.now()
**Learning:** Found a major performance bottleneck where `datetime.datetime.now()` was used as a fallback argument in a dictionary lookup `txn.get("timestamp", datetime.datetime.now())` inside a loop over a large dataset. Python eagerly evaluates function calls passed as arguments, meaning `datetime.now()` was executed 1,000,000 times even when the transaction already had a timestamp.
**Action:** Always cache dynamically evaluated values like `datetime.now()` outside the loop if they can be reused as fallbacks. Additionally, when looping over large datasets looking for edge cases, always check primitive threshold logic (e.g., `qty_adj < -50`) to short-circuit execution before performing expensive dictionary lookups or accessing object properties.
