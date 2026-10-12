# Physical counts and inventory adjustments

Counts now reconcile quantity and carrying value through a manager-reviewed
workflow. Stock, cost layers, movement history, count status, retry records and
outbox enqueue commit together. This uses the existing tenant inventory write
lock on SQLite and PostgreSQL; publication remains separate unfinished work.

## Workflow

1. `POST /api/cycle-counts/start` captures the quantity and stock version of each
   selected SKU at one location. Supply `mutation_id`, optional `name`,
   `location_id` (default `default`), `skus`, `assigned_operator_id` and
   `is_blind_count`. Omitted SKUs select all existing stock rows at that location,
   including zero-quantity rows. Unknown/unstocked SKUs are rejected. Each count
   supports at most 1,000 SKUs; select subsets for larger locations so that a
   count always fits the submission limit.
2. `GET /api/cycle-counts` lists tenant history. Operators see only assigned
   records. `/assigned` lists open assignments. Blind operator responses omit
   expected quantities and variances; managers can review the actual snapshot.
3. `POST /api/cycle-counts/submit` accepts `record_id`, `revision`, `mutation_id`
   and `items: [{sku, counted_quantity}]`. Submit every line exactly once with
   nonnegative integer quantities. The record becomes `SUBMITTED`; quantities
   never change at submission. Variances above 5% are flagged for review.
4. A manager/admin posts to `/{record_id}/approve` with `revision`, `reason` and
   `mutation_id`. Approval reconciles every discrepancy, records the approver
   and reason, and marks the count `COMPLETED`. Repeated approvals return the
   saved result without additional movements. Warehouse operators cannot approve,
   create counts, request recounts or make direct adjustments.
5. `/{record_id}/recount` accepts the same review fields, clears observations,
   captures fresh quantities/versions and increments the revision. A completed
   count cannot reopen; start a new count. Legacy records without a snapshot
   need a recount with explicit `location_id` before approval. Legacy completed
   records cannot be represented as reconciled counts.

Approval requires every current stock version to match its snapshot, even on
matched lines. Sales, receipts, allocations and adjustments after capture cause
409; a manager must request a recount. This deliberately conservative policy
avoids overwriting later stock movements. Keep stock activity paused while
counting a location where practical; offline counts obey the same policy.
Approval cannot reduce on-hand quantity below reservations. Resolve reservations
and recount before retrying. The entire approval rolls back if any line fails.

Submission without a revision is retained for first-revision legacy callers only;
new clients should always provide it. Missing revisions after recount are rejected.

## Costs and direct adjustments

`POST /api/cycle-counts/adjust` requires `sku`, `location_id`, signed nonzero
`delta`, `expected_version`, `reason_code`, `reason` and `mutation_id`.
Reason codes: DAMAGE, THEFT, EXPIRED (losses); FOUND (gains); CORRECTION (either).
Only managers/admins can adjust. Optional `lot_number` selects loss layers or
labels a gain layer. Positive adjustments may include `unit_cost_cents`; negative
adjustments cannot rewrite cost.

Losses consume layers under FIFO/LIFO/WAC/FEFO, including expired/quarantined
goods because disposal is not a sale. Reserved quantities remain protected.
The movement records `inventoryValueChangeCents`, consumed layers, actor and
reason; it does not recognize sale COGS or post a financial journal.
WAC losses require the existing pool to be priced, as dispatch does.

Count gains create unpriced opening-style layers; no purchase cost is invented.
Use the existing accounting layer-pricing API to establish cost afterwards.
Direct gains with explicit costs create priced layers and preserve WAC rounding.
Counts are aggregate SKU/location observations, not lot-level physical counts;
shortages follow the configured costing order and gains have no inferred lot.

## Offline replay, scheduling and migration

`POST /api/cycle-counts/sync` accepts 1–100 submissions. Every offline entry must
include revision and a stable mutation ID. Its payload is identical to `/submit`.
Duplicate entries return saved results; altered payloads using an existing ID
return 409. The whole batch rolls back if any entry is invalid, foreign,
unassigned or stale. Sync only submits observations; it never approves stock.
Replay authorization checks current assignment. A previously accepted submission
replays its original response even after review; query history for current status.

Plans/scheduling now use persisted audit history and actual location snapshots.
An open audit blocks a second audit for that plan; a completed audit also blocks
rescheduling until the configured interval from its creation has elapsed. Plans
with no stock at their zone/location are skipped. ABC classification labels plans;
per-SKU ABC filtering is not implemented. Set plan `zone` to its stock location.

Run `python -m scripts.migrate_identity_catalog` with the migration database
account before deploying. It adds count snapshot/revision/review columns,
64-bit signed variance values and a tenant-scoped retry table, with forced RLS.
Existing legacy counts remain historical; the migration does not invent snapshots
or retroactively adjust their stock. Repeat runs preserve quantities.

The Python React adapter now uses the mounted count routes, carries revisions
and retry IDs, and exposes explicit review/adjustment methods. Array-only legacy
submissions use revision zero; after recount supply `{revision, mutationId, items}`.
Dedicated count/review UI controls and lot-specific counts remain future work.

Tests cover approval once, stale snapshots/recounts, reservation protection,
multi-line/outbox rollback, integer validation, complete submissions, offline
atomic replay, blind permissions, all four costing methods and unknown gain costs.
The PostgreSQL security scenario upgrades old count tables and verifies concurrent
approvals with a real restricted login produce one adjustment and outbox event.

Verification: the full regression run passed 365 tests and skipped the two opt-in
PostgreSQL scenarios; its sole failure was an existing throttle test crossing a
five-minute clock boundary. That test now uses a controlled clock/fresh account
and checks window reset. It and the final large-location guard passed in a
two-test follow-up, covering 367 distinct Python tests across the runs. Dedicated
PostgreSQL costing/security runs passed both scenarios. Six Python client adapter
tests, TypeScript checking, compilation and whitespace checks passed. No store
database was migrated or service deployed.
