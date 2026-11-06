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

Email delivery, PDF/XLSX generation, public sharing, arbitrary cron/grouping and
statutory financial reporting remain follow-up features.

## Notifications API Specification

All notification endpoints are mounted under both root `/notifications` and `/api/notifications` prefixes. Endpoints enforce tenant isolation and require authorized RBAC roles.

### Allowed Roles
`ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR`

---

### 1. List Inbox Notifications

Retrieves the paginated notification inbox for the authenticated user.

- **Route:** `GET /notifications` (or `GET /api/notifications`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token containing user claims and tenant identity.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector (must match token tenant if provided).
- **Query Parameters:**
  - `user_id` (*Optional*, string): Explicit recipient ID; must match the authenticated caller's ID (`request.state.user_id`).
  - `includeResolved` (*Optional*, boolean, default: `false`): When `true`, returns resolved alert notifications alongside active ones.
  - `limit` (*Optional*, integer, default: `100`, range: `1`–`1000`): Maximum rows to return.
  - `offset` (*Optional*, integer, default: `0`, min: `0`): Pagination offset.

#### Response
- **Status 200 OK**:
```json
[
  {
    "id": "notif-uuid",
    "title": "Retail notification",
    "message": "Low stock alert: SKU-123 on-hand quantity is below threshold",
    "link": "/reports/executions/exec-uuid/content",
    "createdAt": "2026-10-31T12:00:00Z",
    "status": "UNREAD",
    "isRead": false,
    "snoozedUntil": null
  }
]
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Recipient ID does not match the authenticated caller or tenant mismatch.
- `422 Unprocessable Entity`: Invalid pagination parameters (`limit` < 1 or > 1000, `offset` < 0).

---

### 2. Get Unread Notifications Count

Returns the count of active unread or expired-snooze notifications for the current user.

- **Route:** `GET /notifications/inbox/unread` (or `GET /api/notifications/inbox/unread`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Query Parameters:**
  - `user_id` (*Optional*, string): Optional recipient ID; must match authenticated user ID.

#### Response
- **Status 200 OK**:
```json
{
  "unread_count": 3
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Recipient mismatch or insufficient permissions.

---

### 3. List Eligible Escalation Managers

Lists active managers and administrators in the current tenant eligible to receive escalated notifications.

- **Route:** `GET /notifications/managers` (or `GET /api/notifications/managers`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.

#### Response
- **Status 200 OK**:
```json
[
  {
    "id": "usr-mgr-1",
    "name": "Warehouse Manager Jane",
    "role": "MANAGER"
  }
]
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient role permissions or tenant mismatch.

---

### 4. Mark Notification as Read

Updates the status of an owned notification to `READ`.

- **Route:** `POST /notifications/{notification_id}/read` (or `POST /api/notifications/{notification_id}/read`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR`

#### Request Parameters
- **Path Parameters:**
  - `notification_id` (*Required*, string): ID of the notification to mark as read.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Query Parameters:**
  - `user_id` (*Optional*, string): Optional recipient verification; must match caller.

#### Response
- **Status 200 OK**:
```json
{
  "status": "success"
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Notification does not belong to the user or tenant mismatch.
- `404 Not Found`: Notification not found.

---

### 5. Snooze Notification

Temporarily snoozes an owned notification for a specified duration in hours (1 to 168 hours). The notification will reappear as unread after the duration expires.

- **Route:** `POST /notifications/{notification_id}/snooze` (or `POST /api/notifications/{notification_id}/snooze`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR`

#### Request Parameters
- **Path Parameters:**
  - `notification_id` (*Required*, string): Target notification ID.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Query Parameters:**
  - `user_id` (*Optional*, string): Optional recipient verification; must match caller.
- **Request Body (JSON):**
```json
{
  "hours": 24
}
```
  - `hours` (*Required*, integer, `1`–`168`): Number of hours to snooze.

#### Response
- **Status 200 OK**:
```json
{
  "status": "success"
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Recipient or tenant mismatch.
- `404 Not Found`: Notification not found.
- `422 Unprocessable Entity`: Validation failure on snooze hours (`hours` outside 1–168).

---

### 6. Escalate Notification to Manager

Escalates an un-escalated notification to a designated active manager within the same tenant. Automatically duplicates underlying active alert conditions to link with the newly notified manager.

- **Route:** `POST /notifications/{notification_id}/escalate` (or `POST /api/notifications/{notification_id}/escalate`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `WAREHOUSE_OPERATOR`

#### Request Parameters
- **Path Parameters:**
  - `notification_id` (*Required*, string): ID of the notification to escalate.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Query Parameters:**
  - `user_id` (*Optional*, string): Optional recipient verification; must match caller.
- **Request Body (JSON):**
```json
{
  "target_manager_id": "usr-mgr-1"
}
```
  - `target_manager_id` (*Required*, string): ID of the recipient manager.

#### Response
- **Status 200 OK**:
```json
{
  "status": "success",
  "escalated_notification_id": "notif-escalated-uuid"
}
```

#### Error Status Codes
- `400 Bad Request`: Target manager is the caller themselves.
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Recipient or tenant mismatch.
- `404 Not Found`: Notification or target manager not found.
- `409 Conflict`: Notification is already escalated, or associated alert is already resolved.

## Practical Notification Workflows

### Handling Low Stock or Expiry In-App Alerts
1. **Poll or Fetch Unread Count:** Call `GET /api/notifications/inbox/unread` to display inbox badge counts on client dashboards.
2. **Review Active Alerts:** Call `GET /api/notifications` to fetch current notifications and their deep links (e.g., to generated reports or stock items).
3. **Take Action:**
   - Mark as read after reviewing via `POST /api/notifications/{notification_id}/read`.
   - Snooze non-urgent notices during warehouse shifts via `POST /api/notifications/{notification_id}/snooze` with `{"hours": 4}`.
   - Escalate critical notices to a supervisor using `GET /api/notifications/managers` to select an eligible recipient and `POST /api/notifications/{notification_id}/escalate` to deliver the notice.

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
