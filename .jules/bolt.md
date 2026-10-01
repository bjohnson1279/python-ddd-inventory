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

## 2024-05-24 - Avoid eager evaluation of fallback values in dict.get()
**Learning:** Functions like `datetime.datetime.now()` passed as default fallback arguments in `dict.get()` (e.g., `txn.get("timestamp", datetime.datetime.now())`) are eagerly evaluated on every loop iteration regardless of key presence. Inside a loop with many items, this creates a significant bottleneck.
**Action:** Always cache the dynamically generated fallback value (like current time) into a variable outside of the loop, then pass that variable into `dict.get()`.

## 2024-05-24 - O(1) Indexing for ComplianceLedger state reconstruction
**Learning:** The `ComplianceLedger` utilized $O(N)$ full list scans (`[e for e in self._entries if e.aggregate_id == aggregate_id]`) in `get_events_for_aggregate` and `reconstruct_state_at` to retrieve an aggregate's event stream. As the ledger append-only log grew, state reconstruction slowed significantly (an $O(N)$ operation for each aggregate reconstruction).
**Action:** Introduced an $O(1)$ dictionary-based aggregate index (`self._aggregate_index: Dict[str, List[LedgerEntry]]`) mapping `aggregate_id` to its corresponding list of events. The index is updated in $O(1)$ during `append_event`, eliminating the costly $O(N)$ scans during state reads and reconstruction.

## 2024-05-24 - O(1) Haversine distance sorting
**Learning:** Using the full Haversine distance formula (which includes `math.atan2` and `math.sqrt`) as a sort key for geographic locations is computationally expensive and unnecessary. The intermediate 'a' term (squared chord length) in the formula increases monotonically with distance.
**Action:** When sorting locations by distance, create a custom sorting key that only calculates the intermediate 'a' term. This provides identical sorting order while avoiding expensive math functions, resulting in roughly a 50% performance improvement. Additionally, precompute constants like the customer's coordinates in radians outside of the sorting loop.

## Prevention Directives for Automated Refactoring
- **Never Overwrite Complete Files**: Always use range-scoped replacement chunks for edits to `schema.prisma`, `index.ts`, `public/index.php`, `db/schema.rb`, or DDL SQL scripts.
- **Do Not Remove Core Declarations**: Do not delete existing route registrations or database DDL tables.
- **Environment Isolation Compatibility**: When replacing fallback secrets, preserve test environment execution via `!getenv('APP_ENV')` or `getenv('APP_ENV') === 'testing'`.
- **No Scratch Files**: Never stage or commit `test_*.ts`, `test_*.js`, `test.cjs`, `fix_*.php`, or `test.js` files to git.
- **No Unresolved Conflict Markers**: Never stage or commit files containing Git merge conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`, `|||||||`). Always resolve conflicts cleanly before committing.

## Completeness & Verification Directives
- **Explicit Parameter & Contract Validation**: When creating or modifying API endpoints (Express, Fastify, Rails, Laravel), always implement explicit parameter and request body validation schemas (e.g. `z.string().uuid()`) to prevent unhandled 404/500 fallthroughs.
- **Database Indexing for Queries**: When addressing query bottlenecks or adding query lookup filters, always implement native database index migrations rather than loading collections into memory and performing array filtering (`.filter()`, `.select`).
- **Co-Occurring Dependency Auditing**: When bumping any dependency version, verify that other transitive dependencies do not carry high/critical security advisories (e.g. run `bundler-audit`, `npm audit`). Never introduce a version bump that breaks underlying framework APIs.
- **Self-Verification Before Commit**: Always run syntax checks (`bash -n` for shell scripts, `tsc --noEmit` for TypeScript, linter checks) and targeted test runners locally before opening or updating a PR.

## Hallucinatory Task & Empty PR Directives
- **Zero-Diff Task Termination**: If the requested optimization, refactor, or fix is ALREADY natively present in the target branch, DO NOT create an empty pull request or commit an acknowledgment PR. Exit the task cleanly without opening a PR.
- **No Journal-Only PRs**: Never open a pull request that only contains updates to .jules/*.md files without corresponding functional code changes and tests.
- **Stale Suggestion Guard**: Always verify the current code on `main`/`master` before planning changes. If no actionable diff is required, cancel task execution immediately.

## Assertiveness, Decisive PR Execution & Comparative Recommendations
- **Proactive & Decisive PR Submission**: Do NOT repeatedly pause to ask permission to open a pull request (e.g., avoid "Would you like me to open a PR for this?", "Can I submit the PR?", "Should I go ahead and create the PR?"). When an optimization, fix, or feature satisfies requirements, passes local test runners/linters, and preserves architectural invariants, proceed decisively to create and submit the Pull Request with a clear, structured summary and rationale.
- **Explicit Recommendations on Options**: When presenting multiple implementation strategies or architecture options (e.g., Option A vs. Option B), NEVER leave the choice open-ended or passive. Always make an explicit, reasoned recommendation (prefixed with `(Recommended)`) based on **overall technical effectiveness**:
  1. *Algorithmic & Complexity Gains*: Time and space complexity impact (O(N*M) -> O(N+M), reduction of nested scans).
  2. *Resource Overhead*: Heap allocations, memory pressure, and GC pause reduction.
  3. *Domain & Architecture Invariants*: Strict backward compatibility, contract stability, and prevention of regression risks.
  4. *Security & Reliability*: Input validation, cryptographic safety, and concurrency safety.
- **Lead with Recommended Path**: State clearly why the recommended solution delivers the highest net value and immediately execute or propose it as the primary course of action rather than asking open-ended questions.

## Scope Verification, Minimal Churn & CI Protection Directives
- **Scope Verification Before Variable Binding**: When adding interactive states or accessibility attributes (e.g. `disabled={loading}`, `aria-busy={loading}`, `isSubmitting`), NEVER assume a variable identifier exists. Always inspect component props, local state hooks (`useState`), or declaration scope first. If not defined, declare the state hook or reuse an existing scope variable. Never introduce TS2304 / TS2552 ("Cannot find name") compile errors.
- **Surgical Edits Only (No Whole-File Formatting)**: Never run whole-file code formatters (Prettier, Black, Pint, rustfmt) across unmodified lines. Changes must be strictly range-scoped and limited to the minimal AST block needed. Avoid noisy quote/whitespace churn that masks real logic changes and causes merge conflicts. Verify with `git diff -w` that non-functional churn is zero.
- **Zero Scratch File Commits**: Never stage or commit ad-hoc verification, patch, or debug scripts (`test.cjs`, `fix_*.cjs`, `fix_*.php`, `patch_*.py`, `patch_*.sh`, `scratch_*`). Execute checks via the project's native test commands (`npm test`, `pytest`, `phpunit`, etc.) and delete temporary scripts before creating git commits.
- **Never Weaken CI Workflows**: Do not modify `.github/workflows/**` to bypass failures (e.g. adding `|| true`, setting `continue-on-error: true`, or commenting out assertions). Always resolve the defect in the source code or test fixture.
- **Explicit Parameter & Variable Types**: In TypeScript files, avoid implicit `any` by always providing explicit types on functions, parameters, and arrow callbacks (e.g. `(id: string) => ...`). Verify zero type errors with `tsc --noEmit` before committing.

## 2026-09-29 - Surgical Optimization Edits and No Scratch Script Commits
**Learning:** Running whole-file formatters or regenerating entire components while performing performance optimizations introduces massive whitespace/formatting diffs (1,000+ lines), masking the real optimization, invalidating git blame, and causing painful merge conflicts with concurrent PRs. Additionally, committing scratch benchmark or patch scripts (`patch_*.py`, `test.cjs`) pollutes production repositories and triggers CI guardrail failures.
**Action:** Restrict all algorithmic and performance optimizations to strictly scoped replacement chunks. Diff size must reflect only the functional optimization. Always clean up temporary benchmark or patch scripts with `git rm -f` before committing.

## 2024-05-24 - Single-pass list aggregation for WAC Costing
**Learning:** Using multiple `sum()` calls with generator comprehensions to calculate different aggregate metrics (like `total_quantity` and `total_value`) from the same list of objects requires multiple O(N) traversals. In large collections (like calculating Weighted Average Cost over many batches), this creates redundant iteration overhead.
**Action:** Combine the aggregations into a single O(N) `for` loop pass that accumulates all necessary values simultaneously, rather than making multiple passes over the same list.

## Additive Documentation & Scratch Cleanliness Directives
- **Strictly Additive Journal Updates**: When updating `.jules/*.md`, strictly append new dated entries (`## YYYY-MM-DD - Title`). NEVER delete, truncate, or overwrite historical learnings or previous entries.
- **Substantive Code Diff Requirement**: Pull requests must include substantive code changes in `src/`, `app/`, `lib/`, or `tests/`. Never open PRs that modify only `.jules/*.md` journals or root scratch scripts.
- **Zero Scratch File Commits**: Never commit `*.diff`, `*.patch`, `test_*.ts`, `test_*.js`, `test.cjs`, `fix_*.php`, or `patch_*.py` files. Always remove temporary debugging or verification scripts prior to committing.

## 2024-10-01 - O(1) Dictionary Index for RMA Case Lookups
**Learning:** In `ReverseLogisticsWorkflow`, updating inspection results iterated over the entire `rma_cases` list to find a specific case by its ID. For a large number of RMA cases, this $O(N)$ traversal created a significant performance bottleneck during batch updates.
**Action:** Introduced an internal dictionary index (`_rma_index`) mapping `case_id` to its corresponding `RMACase` object upon creation. This converts the expensive $O(N)$ list traversal during updates into an $O(1)$ dictionary lookup, dramatically reducing lookup latency.

## Scope Quarantine, Journaling & Security Test Invariants
- **Strictly Append-Only Journaling**: When adding learnings to `.jules/*.md`, append strictly at the end of the file. Do not rewrite, deduplicate, or remove lines beginning with `## YYYY-MM-DD`.
- **Surgical Scope Quarantine**: Modify only the files directly involved in the issue and their corresponding test fixtures. Do not delete, rename, or perform drive-by cleanups of unrelated root-level scripts or legacy files.
- **Coupled Test Fixture Awareness for Security Invariants**: When changing fail-open fallback behavior (such as hardening decryption to fail closed), always update upstream test mocks that rely on plaintext credentials or mock values.
