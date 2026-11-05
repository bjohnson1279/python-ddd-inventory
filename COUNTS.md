# Physical counts and inventory adjustments

Counts now reconcile quantity and carrying value through a manager-reviewed
workflow. Stock, cost layers, movement history, count status, retry records and
outbox enqueue commit together. This uses the existing tenant inventory write
lock on SQLite and PostgreSQL. Asynchronous Kafka publication is now implemented
through the leased worker described in [event delivery](EVENT_DELIVERY.md).

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

## API Specification

All cycle-counting endpoints are mounted under `/api/cycle-counts`. Requests require bearer token authentication and support multi-tenant isolation via token claims or optional `X-Tenant-ID` headers.

| Endpoint | Method | Roles | Description |
| --- | --- | --- | --- |
| `/api/cycle-counts/plans` | POST | `ADMIN`, `MANAGER` | Create a recurring cycle count audit plan |
| `/api/cycle-counts/schedule` | POST | `ADMIN`, `MANAGER` | Trigger scheduled audit generation based on active plans |
| `/api/cycle-counts/classify` | POST | `ADMIN`, `MANAGER` | Classify SKU into ABC tier based on usage value |
| `/api/cycle-counts/start` | POST | `ADMIN`, `MANAGER` | Start a new point-in-time physical inventory count |
| `/api/cycle-counts` | GET | `ADMIN`, `MANAGER`, `WAREHOUSE_OPERATOR` | List historical count records (filtered by operator for non-managers) |
| `/api/cycle-counts/assigned` | GET | `ADMIN`, `MANAGER`, `WAREHOUSE_OPERATOR` | List open count assignments for the current operator |
| `/api/cycle-counts/submit` | POST | `ADMIN`, `MANAGER`, `WAREHOUSE_OPERATOR` | Submit physical count observations for review |
| `/api/cycle-counts/sync` | POST | `ADMIN`, `MANAGER`, `WAREHOUSE_OPERATOR` | Replay batch of offline count submissions |
| `/api/cycle-counts/{record_id}/approve` | POST | `ADMIN`, `MANAGER` | Approve submitted count and reconcile stock/cost adjustments |
| `/api/cycle-counts/{record_id}/recount` | POST | `ADMIN`, `MANAGER` | Reject submission and trigger a recount with fresh snapshot |
| `/api/cycle-counts/adjust` | POST | `ADMIN`, `MANAGER` | Perform direct inventory quantity and cost adjustment |

---

### `POST /api/cycle-counts/plans`
Creates a recurring cycle-count audit plan assigned to an ABC classification tier and optional warehouse zone.

- **Allowed Roles:** `ADMIN`, `MANAGER`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier; must match token tenant claim.
- **Request Body (`application/json`):**
  - `tenant_id` (*Required*, string): Tenant identifier.
  - `name` (*Required*, string, min: 1, max: 100): Descriptive plan name (e.g., `Daily Fast-Movers`).
  - `abc_classification` (*Required*, string, enum: `A`, `B`, `C`): SKU velocity tier.
  - `frequency_days` (*Required*, integer, min: 1, max: 3650): Cadence in days between count iterations.
  - `zone` (*Optional*, string, min: 1, max: 100): Storage zone or location targeted by this plan.
- **Responses:**
  - `200 OK`: Created plan record:
    ```json
    {
      "id": "5fc90664-6d62-4266-8ca2-144a76f7f855",
      "tenant_id": "tenant-1",
      "name": "Daily Fast-Movers",
      "abc_classification": "A",
      "frequency_days": 30,
      "zone": "Zone-A",
      "is_active": true,
      "created_at": "2026-11-05T10:00:00Z"
    }
    ```
  - `401 Unauthorized`: Missing or invalid Bearer token.
  - `403 Forbidden`: Caller lacks required role or tenant mismatch.
  - `422 Unprocessable Entity`: Field validation failed (e.g. invalid frequency or ABC tier).
- **Workflow:** Used by inventory controllers to define audit policies. Active plans are subsequently processed by the scheduler to spawn audit count sessions.

---

### `POST /api/cycle-counts/schedule`
Evaluates active cycle-count plans and automatically generates point-in-time audit count records for due plans with stocked SKUs.

- **Allowed Roles:** `ADMIN`, `MANAGER`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Request Body (`application/json`):**
  - `tenant_id` (*Required*, string): Tenant identifier.
- **Responses:**
  - `200 OK`:
    ```json
    {
      "scheduled": 2,
      "audits": ["count-uuid-1", "count-uuid-2"]
    }
    ```
  - `401 Unauthorized`: Missing or invalid Bearer token.
  - `403 Forbidden`: Insufficient role privileges.
- **Workflow:** Called by scheduled background workers or inventory administrators to trigger periodic audits based on plan intervals.

---

### `POST /api/cycle-counts/classify`
Evaluates the cumulative monetary usage value of an item against organizational turnover to recommend an ABC velocity tier and count frequency.

- **Allowed Roles:** `ADMIN`, `MANAGER`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Request Body (`application/json`):**
  - `total_usage_value` (*Required*, float, ge: 0): Total annualized consumption/sales value for the SKU.
  - `total_org_value` (*Required*, float, ge: 0): Total annualized consumption/sales value across all SKUs.
  - `thresholds` (*Optional*, object): Custom thresholds with keys `a_threshold` and `b_threshold` (e.g. `{"a_threshold": 0.8, "b_threshold": 0.95}`).
- **Responses:**
  - `200 OK`:
    ```json
    {
      "class": "A",
      "frequency_days": 30
    }
    ```
  - `400 Bad Request`: Invalid thresholds supplied (e.g. thresholds outside `[0, 1]` or invalid keys).
  - `401 Unauthorized`: Missing or invalid Bearer token.
  - `403 Forbidden`: Insufficient permissions.
- **Workflow:** Assists inventory managers in categorizing products according to Pareto analysis prior to plan creation.

---

### `POST /api/cycle-counts/start`
Freezes a point-in-time snapshot of on-hand inventory quantities and stock versions for selected SKUs (or all SKUs) at a given location.

- **Allowed Roles:** `ADMIN`, `MANAGER`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Request Body (`application/json`):**
  - `mutation_id` (*Required*, string, min: 1, max: 100): Idempotency key for request deduplication.
  - `name` (*Optional*, string, min: 1, max: 100, default: `"Physical count"`): Title or label for the count session.
  - `location_id` (*Optional*, string, min: 1, max: 100, default: `"default"`): Target warehouse location identifier.
  - `skus` (*Optional*, array of strings, max 1,000 items): Specific SKUs to count. If omitted, all existing stocked SKUs at the location are included.
  - `assigned_operator_id` (*Optional*, string): User ID of the warehouse operator assigned to count.
  - `is_blind_count` (*Optional*, boolean, default: `true`): When true, expected quantities and variances are masked from operators.
- **Responses:**
  - `200 OK`:
    ```json
    {
      "record_id": "27edb46d-737e-4e82-a388-c887cac52d99",
      "name": "Physical count 1",
      "status": "PENDING",
      "location_id": "default",
      "zone": "default",
      "revision": 0,
      "assigned_operator_id": "usr-operator-1",
      "is_blind_count": true,
      "approved_by": null,
      "items": [
        {
          "sku": "TEST-SKU",
          "expected_quantity": 10,
          "counted_quantity": null,
          "variance_quantity": null,
          "status": "PENDING"
        }
      ]
    }
    ```
  - `400 Bad Request`: Duplicate SKUs in request, unknown SKU, or count limits exceeded.
  - `401 Unauthorized`: Missing or invalid Bearer token.
  - `403 Forbidden`: Insufficient permissions.
- **Workflow:** Kicks off an audit. Freezes expected quantities and baseline stock versions for discrepancy detection upon review.

---

### `GET /api/cycle-counts`
Lists historical and active cycle-count records for the tenant, ordered by creation time descending.

- **Allowed Roles:** `ADMIN`, `MANAGER`, `WAREHOUSE_OPERATOR`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Query Parameters:**
  - `limit` (*Optional*, integer, default: `100`, min: `1`, max: `1000`): Maximum records to retrieve.
  - `offset` (*Optional*, integer, default: `0`, min: `0`): Pagination offset.
- **Responses:**
  - `200 OK`: Array of cycle count summary DTOs. For non-manager operators, returns only counts assigned to the caller, and blind count responses hide expected quantities and variances until completion.
  - `401 Unauthorized`: Missing or invalid Bearer token.
  - `403 Forbidden`: Caller lacks authorized role.
- **Workflow:** Displayed in administrative count dashboards and mobile warehouse operator assignment lists.

---

### `GET /api/cycle-counts/assigned`
Retrieves open, uncompleted cycle counts assigned to the authenticated warehouse operator.

- **Allowed Roles:** `ADMIN`, `MANAGER`, `WAREHOUSE_OPERATOR`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Query Parameters:**
  - `limit` (*Optional*, integer, default: `100`, min: `1`, max: `1000`): Maximum records.
  - `offset` (*Optional*, integer, default: `0`, min: `0`): Pagination offset.
- **Responses:**
  - `200 OK`: Array of assigned count records with `status != 'COMPLETED'`.
  - `401 Unauthorized`: Missing or invalid Bearer token.
  - `403 Forbidden`: Caller lacks authorized role.
- **Workflow:** Queried by handheld scanners and mobile clients upon operator login to display pending counting tasks.

---

### `POST /api/cycle-counts/submit`
Submits recorded physical quantities for all items in an assigned cycle count.

- **Allowed Roles:** `ADMIN`, `MANAGER`, `WAREHOUSE_OPERATOR`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Request Body (`application/json`):**
  - `record_id` (*Required*, string): Unique identifier of the count record.
  - `items` (*Required*, array of objects, min: 1, max: 1000 items):
    - `sku` (*Required*, string, min: 1, max: 100): SKU identifier.
    - `counted_quantity` (*Required*, integer, min: 0): Non-negative integer observed quantity.
  - `revision` (*Optional*, integer, min: 0): Snapshot revision index matching the started count session.
  - `mutation_id` (*Optional*, string, min: 1, max: 100): Idempotency token.
- **Responses:**
  - `200 OK`:
    ```json
    {
      "success": true,
      "status": "SUBMITTED",
      "requiresReview": true,
      "varianceFlagged": true,
      "record_id": "27edb46d-737e-4e82-a388-c887cac52d99",
      "revision": 0
    }
    ```
  - `400 Bad Request`: Count lines missing, duplicate SKUs, or line items do not match snapshot.
  - `404 Not Found`: Record not found or inaccessible.
  - `409 Conflict`: Mutation ID reused with divergent payload.
- **Workflow:** Operators post observed inventory quantities from handheld terminals. Lines with discrepancies over 5% automatically flag `requiresReview` and `varianceFlagged`.

---

### `POST /api/cycle-counts/sync`
Batch endpoint for synchronizing offline count submissions collected in warehouse dead zones.

- **Allowed Roles:** `ADMIN`, `MANAGER`, `WAREHOUSE_OPERATOR`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Request Body (`application/json`):** Array of 1 to 100 offline submission objects. Each item must contain:
  - `record_id` (*Required*, string): Count record ID.
  - `revision` (*Required*, integer, min: 0): Required revision number.
  - `mutation_id` (*Required*, string, min: 1, max: 100): Stable idempotency key.
  - `items` (*Required*, array of line item objects): Array of `{ "sku": string, "counted_quantity": integer }`.
- **Responses:**
  - `200 OK`: Array of submission responses corresponding to each synced count.
  - `400 Bad Request`: Empty payload, batch exceeds 100 submissions, or validation error on any submission.
  - `401 Unauthorized`: Missing or invalid Bearer token.
- **Workflow:** Replays cached offline counts in a single atomic transaction when portable devices regain connectivity.

---

### `POST /api/cycle-counts/{record_id}/approve`
Reviews and approves a submitted count, generating adjustments to reconcile inventory balances, cost layers, and outbox notification events.

- **Allowed Roles:** `ADMIN`, `MANAGER`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Path Parameters:**
  - `record_id` (*Required*, string): Unique count identifier.
- **Request Body (`application/json`):**
  - `revision` (*Required*, integer, min: 0): Current count revision being approved.
  - `reason` (*Required*, string, min: 1, max: 500): Justification for approving reconciliation adjustments.
  - `mutation_id` (*Required*, string, min: 1, max: 100): Idempotency key.
  - `location_id` (*Optional*, string): Warehouse location identifier.
- **Responses:**
  - `200 OK`:
    ```json
    {
      "record_id": "27edb46d-737e-4e82-a388-c887cac52d99",
      "status": "COMPLETED",
      "approved_by": "usr-manager-1",
      "adjustments": [
        {
          "sku": "TEST-SKU",
          "delta": -2,
          "inventoryValueChangeCents": -200
        }
      ]
    }
    ```
  - `404 Not Found`: Record not found.
  - `409 Conflict`: Intermediate stock transactions occurred after snapshot capture; recount required.
- **Workflow:** Supervisors review observed counts. Approval applies stock balance adjustments, writes inventory movements, and marks the session `COMPLETED`.

---

### `POST /api/cycle-counts/{record_id}/recount`
Rejects submitted counts due to unacceptable variances or intermediate stock conflicts, capturing a new snapshot and incrementing the revision.

- **Allowed Roles:** `ADMIN`, `MANAGER`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Path Parameters:**
  - `record_id` (*Required*, string): Unique count identifier.
- **Request Body (`application/json`):**
  - `revision` (*Required*, integer, min: 0): Current count revision to reject.
  - `reason` (*Required*, string, min: 1, max: 500): Explanation for requesting a recount.
  - `mutation_id` (*Required*, string, min: 1, max: 100): Idempotency key.
  - `location_id` (*Optional*, string): Warehouse location identifier.
- **Responses:**
  - `200 OK`: Updated count record with incremented revision and `status: "PENDING"`.
  - `404 Not Found`: Record not found.
  - `409 Conflict`: Completed counts cannot be reopened.
- **Workflow:** Used when observed variances cannot be verified or when stock movements during counting invalidate the frozen snapshot.

---

### `POST /api/cycle-counts/adjust`
Executes an immediate manual inventory adjustment for scrap, found stock, theft, or damage outside of a full cycle count session.

- **Allowed Roles:** `ADMIN`, `MANAGER`
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.
- **Request Body (`application/json`):**
  - `sku` (*Required*, string, min: 1, max: 100): Target SKU identifier.
  - `location_id` (*Optional*, string, default: `"default"`): Storage location.
  - `delta` (*Required*, integer, non-zero): Signed adjustment quantity (negative for loss, positive for gain).
  - `expected_version` (*Required*, integer, min: 0): Optimistic concurrency version of the stock record.
  - `reason_code` (*Required*, string, enum: `DAMAGE`, `THEFT`, `FOUND`, `CORRECTION`, `EXPIRED`): Business reason classification.
  - `reason` (*Required*, string, min: 1, max: 500): Detailed narrative reason for the adjustment.
  - `mutation_id` (*Required*, string, min: 1, max: 100): Idempotency key.
  - `unit_cost_cents` (*Optional*, integer, min: 0): Unit valuation in cents (applicable only to positive inventory gains).
  - `lot_number` (*Optional*, string, min: 1, max: 100): Associated lot number.
- **Responses:**
  - `200 OK`:
    ```json
    {
      "id": "2ca9e4dc-b9cb-4dcd-b7ae-5cef128e8946",
      "sku": "TEST-SKU",
      "locationId": "default",
      "quantity": 15,
      "allocated": 0,
      "available": 15,
      "version": 2,
      "delta": 5,
      "inventoryValueChangeCents": 500,
      "reasonCode": "FOUND",
      "reason": "Found box in aisle 3",
      "costingMethod": "FIFO",
      "costLayerId": "557db6db-d863-4f32-915c-a1f2abdb33f1"
    }
    ```
  - `400 Bad Request`: `delta` is 0, negative delta specified with unit cost, or mismatched reason code (e.g. `FOUND` with negative delta).
  - `403 Forbidden`: Caller lacks required permission to supply or modify purchase costs.
  - `404 Not Found`: Target SKU or stock record does not exist.
  - `409 Conflict`: Stale `expected_version` or reserved stock would be violated.
- **Workflow:** Ad-hoc adjustment tool for warehouse floor supervisors to log damaged goods, write-offs, or uncounted inventory discoveries.
