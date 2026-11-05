# Reporting and alerts

Implemented 2026-10-13 for the Python backend and its React client.

## Reports

Eight report types produce persisted snapshots: stock, inventory valuation,
remaining-stock aging, movements, replenishment, trial balance, income statement
and balance sheet. JSON and CSV downloads contain actual rows. Inventory reports
describe current stock; movement and financial reports accept the supported UTC
date filters. Arbitrary queries, grouping and historical inventory reconstruction
are rejected. Reports exceeding 10,000 rows fail explicitly.

Stock quantities include their configured base unit and UOM version. Integral
base quantities preserve conversions such as 1.5 kg to 1,500 g. Stock `available`
means recorded quantity minus allocations; replenishment and low-stock alerts
additionally exclude expired and quarantined goods. Replenishment reuses the
existing procurement suggestions, including incoming goods and purchase rules.

Aging buckets use the receipt dates of remaining cost layers, rather than the
last reservation or movement. Sold quantities leave the buckets. Migrated
opening layers retain their recorded baseline dates; original receipt ages
cannot be reconstructed. Missing or inconsistent layers are marked incomplete.
Unpriced valuation is unknown, never silently zero. Financial exports retain
the accounting method, USD currency, exact integer cents and source-quality
flags. CSV includes snapshot metadata and neutralizes spreadsheet formulas in
text fields. JSON also contains financial summaries.

Report execution creates a durable QUEUED job and an outbox request in one
transaction. Processing produces COMPLETED or FAILED; expected failures can be
explicitly retried with the current version. The copied definition remains
pinned to that job. Completed snapshots are immutable under SQLite and
PostgreSQL database guards. A report-ready personal notification and its outbox
event commit together with the snapshot. A failed commit leaves no ready notice
or partial completion.

Generation uses the tenant inventory lock for a bounded database-only snapshot.
Concurrent workers complete a job once; a worker crash rolls back to QUEUED.
The creator's current permissions are checked again when processing. Download
permissions are checked on every request. Artifacts are served by authenticated
`/reports/executions/{id}/content`, with private/no-store caching. There are no
public uploads or mock file URLs. Tenant ownership, actor-bound mutation IDs and
optimistic versions protect command replay and retry operations.

## Schedules and worker operation

Schedules support five-field UTC daily or weekly cron expressions, such as
`0 8 * * *` and `0 8 * * 1`. Unsupported expressions are rejected. A missed
schedule coalesces to one job and advances to its next future occurrence;
repeated ticks cannot create duplicate jobs for that occurrence. Schedules are
disabled with an explicit error if their creator loses permission.

Run a periodic external scheduler with the existing restricted runtime database
login, `DATABASE_URL`, and an explicit tenant:

```powershell
$env:REPORTING_WORKER_TENANT = 'your-tenant-id'
python -m scripts.run_reporting
```

Each invocation processes up to 100 due schedules, refreshes alerts and
generates up to 100 queued jobs. Run one configured pass for each tenant. The UI
also supports manual generation, retry and schedule pause/resume. Deployment
must supply the periodic invocation; installing this code does not start one.

Before deployment, run the existing reviewed identity/catalog migration as the
migration owner, following SECURITY.md. It creates five reporting/alert tables,
adds the nullable notification link column and installs forced PostgreSQL RLS
and snapshot guards. Confirm runtime grants for the new tables. No running
store database was migrated during implementation.

## Alerts and personal inbox

Alerts are opt-in per tenant. Managers can independently enable low stock and
expiry, and set an expiry horizon from 0 to 90 days (default seven). Low stock
uses active supplier reorder policies and usable unallocated quantity. Expiry
uses remaining nonquarantined layers and distinguishes near-expiry from expired
goods. Stock mutations evaluate affected goods within the stock transaction.
Clock-only changes require periodic worker ticks or manual evaluation.

Each active condition has a deduplicated episode. Recovery resolves its notices;
a recurrence creates a new episode. Active administrators, managers and
warehouse operators receive tenant-scoped personal notices. Newly eligible
recipients can receive an existing episode. Notifications and NotificationCreated
outbox events are atomic with their producer transaction.

The inbox uses the logged-in recipient, supports read and bounded snooze, and
restores unread counts when snoozes expire. A recipient can forward a notice to
another active manager. Concurrent escalation forwards once; recovery also
resolves the forwarded alert. Report links remain private and permission-checked.
The Python UI shows actual aging rows and explicit data/error states, including
incomplete report warnings. The aging preview displays its first 100 rows;
exports support the bounded full report.

### Notifications & Personal Inbox API Reference

The personal inbox and notification actions are exposed under `/api/notifications` (and `/notifications`). Endpoints require authentication and allow the `ADMIN`, `MANAGER`, `USER`, and `WAREHOUSE_OPERATOR` roles. Recipients are validated against the authenticated session; specifying another user's ID yields a `403 Forbidden`.

| Endpoint | Method | Roles | Description |
| --- | --- | --- | --- |
| `/api/notifications` | GET | `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR` | List personal notifications with pagination and resolution filters |
| `/api/notifications/inbox/unread` | GET | `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR` | Retrieve current unread notification count |
| `/api/notifications/managers` | GET | `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR` | List eligible tenant managers for notification escalation |
| `/api/notifications/{notification_id}/read` | POST | `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR` | Mark an owned notification as read |
| `/api/notifications/{notification_id}/snooze` | POST | `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR` | Temporarily snooze an owned notification for specified hours |
| `/api/notifications/{notification_id}/escalate` | POST | `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR` | Escalate an owned notification to another tenant manager |

#### `GET /api/notifications`
Retrieves a paginated list of personal notifications for the authenticated user, ordered by creation time descending.

- **Parameters:**
  - `user_id` (Optional, query, string): Target recipient ID. Must match authenticated user if provided.
  - `includeResolved` (Optional, query, boolean, default: `false`): Whether to include resolved alert notifications.
  - `limit` (Optional, query, integer, default: `100`, min: `1`, max: `1000`): Maximum records to return.
  - `offset` (Optional, query, integer, default: `0`, min: `0`): Pagination offset.
- **Responses:**
  - `200 OK`: Array of notification objects:
    ```json
    [
      {
        "id": "notif_123",
        "title": "Retail notification",
        "message": "Stock level low for SKU-001",
        "link": "/reports/executions/rep_456/content",
        "createdAt": "2026-10-13T12:00:00Z",
        "status": "UNREAD",
        "isRead": false,
        "snoozedUntil": null
      }
    ]
    ```
  - `403 Forbidden`: Recipient ID does not match authenticated user.
  - `422 Unprocessable Entity`: Invalid pagination bounds (`limit` < 1 or > 1000, `offset` < 0).
- **Workflow:** Polled or queried by the client dashboard inbox to display personal action alerts and report readiness notices.

#### `GET /api/notifications/inbox/unread`
Calculates the count of currently unread notifications for the caller, taking active snooze durations into account.

- **Parameters:**
  - `user_id` (Optional, query, string): Recipient ID to inspect. Must match authenticated caller.
- **Responses:**
  - `200 OK`: Count payload:
    ```json
    {
      "unread_count": 3
    }
    ```
  - `403 Forbidden`: Recipient mismatch.
- **Workflow:** Lightweight endpoint for badge counters and header polling without fetching full notification bodies.

#### `GET /api/notifications/managers`
Lists all active tenant administrators and managers eligible to receive escalated notifications, excluding the current user.

- **Parameters:** None.
- **Responses:**
  - `200 OK`: Array of candidate managers:
    ```json
    [
      {
        "id": "usr_mgr1",
        "name": "Warehouse Manager",
        "role": "MANAGER"
      }
    ]
    ```
- **Workflow:** Populates escalation target selectors in the notification triage dialog.

#### `POST /api/notifications/{notification_id}/read`
Marks an owned notification as `READ`.

- **Parameters:**
  - `notification_id` (Required, path, string): Unique identifier of the notification.
  - `user_id` (Optional, query, string): Recipient confirmation ID. Must match authenticated caller.
- **Responses:**
  - `200 OK`: `{"status": "success"}`
  - `403 Forbidden`: Recipient mismatch.
  - `404 Not Found`: Notification not found or owned by another user/tenant.
- **Workflow:** Triggered when a user opens or acknowledges a notification from their inbox.

#### `POST /api/notifications/{notification_id}/snooze`
Temporarily suppresses a notification by updating its status to `SNOOZED` until the specified duration expires.

- **Parameters:**
  - `notification_id` (Required, path, string): Unique identifier of the notification.
  - `user_id` (Optional, query, string): Recipient confirmation ID. Must match authenticated caller.
- **Request Body (application/json):**
  - `hours` (Required, integer, min: `1`, max: `168`): Number of hours to snooze (1 hour up to 7 days).
- **Responses:**
  - `200 OK`: `{"status": "success"}`
  - `403 Forbidden`: Recipient mismatch.
  - `404 Not Found`: Notification not found or not owned by caller.
  - `422 Unprocessable Entity`: `hours` out of bounds or invalid payload.
- **Workflow:** Allows operators to postpone action on an alert (e.g. pending supplier response) while hiding it from unread badges until expiration.

#### `POST /api/notifications/{notification_id}/escalate`
Forwards an owned notification to another active manager in the tenant, cloning the alert association and marking the caller's notice as `ESCALATED`.

- **Parameters:**
  - `notification_id` (Required, path, string): Unique identifier of the notification to escalate.
  - `user_id` (Optional, query, string): Recipient confirmation ID. Must match authenticated caller.
- **Request Body (application/json):**
  - `target_manager_id` (Required, string): Active manager ID receiving the escalated notice.
- **Responses:**
  - `200 OK`:
    ```json
    {
      "status": "success",
      "escalated_notification_id": "notif_789"
    }
    ```
  - `400 Bad Request`: `target_manager_id` matches the current user.
  - `403 Forbidden`: Recipient mismatch.
  - `404 Not Found`: Notification or target manager not found.
  - `409 Conflict`: Notification already escalated or underlying alert already resolved.
- **Workflow:** Enables warehouse operators and team members to transfer unresolved inventory anomalies or urgent stock warnings directly to supervisors.

Email delivery, PDF/XLSX generation, public sharing, arbitrary cron/grouping and
statutory financial reporting remain follow-up features.

## Verification

The full Python regression run passed 539 tests with 12 optional integration
skips. Subsequent artifact checks passed 21 tests, and the added unit-conversion
and warehouse cost-redaction case passed separately. The final database/security
selection passed 35 tests, including restricted PostgreSQL runtime queries,
forced RLS, immutable completed snapshots, concurrent processing/escalation,
repeatable migration and outbox rollback. These checks used disposable databases.
Python compilation and CI YAML parsing passed. All 42 selected client
adapter/component tests and TypeScript compilation passed. Windows sandbox
transform-cache errors were resolved by running the client checks with cache
access. No live email delivery or browser-to-server deployment journey was
performed.
