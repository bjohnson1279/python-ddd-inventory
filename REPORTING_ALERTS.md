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

## Reports API Specification

All report endpoints are mounted under `/reports` (and `/api/reports`). Endpoints enforce tenant isolation and require authorized RBAC roles.

### Allowed Roles Summary
- Management & Configuration (`ADMIN`, `MANAGER`, `ACCOUNTANT`): Create definitions, execute reports, manage schedules, trigger processing, and retry failed executions.
- Viewing & Content (`ADMIN`, `MANAGER`, `USER`, `VIEWER`, `ACCOUNTANT`): List reports, list executions, view execution details, download content, and inspect schedules (filtered by user permissions).
- Public / Background: `/reports/scheduler/tick` (internal scheduler tick) and `/reports/shared/{token}` (external share lookup).

---

### 1. Create Report Definition

Creates a new durable report definition pinned to the caller's tenant.

- **Route:** `POST /reports` (or `POST /api/reports`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `ACCOUNTANT`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Request Body (application/json):**
  - `name` (*Required*, string, min: 1, max: 100): Report name (must contain non-whitespace characters).
  - `description` (*Optional*, string, default: `""`, max: 2000): Detailed summary of report scope.
  - `type` (*Optional*, string, default: `"STOCK"`): One of `STOCK`, `INVENTORY_VALUATION`, `INVENTORY_AGING`, `MOVEMENTS`, `REPLENISHMENT`, `TRIAL_BALANCE`, `INCOME_STATEMENT`, `BALANCE_SHEET`.
  - `filters` (*Optional*, object or string, default: `{}`): Supported UTC date filters and parameters.
  - `grouping` (*Optional*, object or string, default: `null`): Optional grouping configurations.
  - `mutationId` (*Optional*, string, max: 100): Client-supplied idempotency key.
  - `reason` (*Optional*, string, max: 500): Audit justification.

#### Response
- **Status 200 OK**:
```json
{
  "success": true,
  "report": {
    "id": "rep_123",
    "name": "Inventory Valuation",
    "type": "INVENTORY_VALUATION",
    "filters": {},
    "tenant_id": "tenant-1",
    "tenantId": "tenant-1",
    "created_by": "usr_456"
  }
}
```

#### Error Status Codes
- `400 Bad Request`: Invalid report type, unsupported filters, or unsafe query structure.
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient role permissions or missing domain permission.
- `409 Conflict`: Mutation ID conflict with different parameters.

---

### 2. List Report Definitions

Lists all report definitions accessible to the authenticated tenant user based on assigned permissions.

- **Route:** `GET /reports` (or `GET /api/reports`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `ACCOUNTANT`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.

#### Response
- **Status 200 OK**:
```json
{
  "reports": [
    {
      "id": "rep_123",
      "name": "Inventory Valuation",
      "type": "INVENTORY_VALUATION",
      "filters": {},
      "tenant_id": "tenant-1",
      "created_by": "usr_456"
    }
  ]
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Tenant mismatch or unauthorized caller.

---

### 3. Enqueue Report Execution

Enqueues an asynchronous generation job for an existing report definition.

- **Route:** `POST /reports/{identifier}/execute` (or `POST /api/reports/{identifier}/execute`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `ACCOUNTANT`

#### Request Parameters
- **Path Parameters:**
  - `identifier` (*Required*, string): ID of the report definition to execute.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Request Body (application/json):**
  - `format` (*Optional*, string, default: `"json"`): Export format (`"json"` or `"csv"`).
  - `mutationId` (*Optional*, string, max: 100): Idempotency key.
  - `reason` (*Optional*, string, max: 500): Execution audit note.

#### Response
- **Status 202 Accepted**:
```json
{
  "success": true,
  "executionId": "exec_789",
  "execution": {
    "id": "exec_789",
    "report_id": "rep_123",
    "format": "json",
    "status": "QUEUED",
    "created_at": "2026-10-13T12:00:00Z"
  }
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient role permissions or caller lacks permission for definition.
- `404 Not Found`: Report definition does not exist in tenant.
- `422 Unprocessable Entity`: Invalid format specified (e.g. PDF/XLSX).

---

### 4. Schedule Report Execution

Creates a periodic cron schedule for automated execution of a report definition.

- **Route:** `POST /reports/{identifier}/schedule` (or `POST /api/reports/{identifier}/schedule`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `ACCOUNTANT`

#### Request Parameters
- **Path Parameters:**
  - `identifier` (*Required*, string): ID of the target report definition.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Request Body (application/json):**
  - `cronExpression` (*Required*, string, max: 100): Standard five-field UTC daily/weekly cron string (e.g., `"0 8 * * *"` or `"0 8 * * 1"`).
  - `format` (*Optional*, string, default: `"json"`): Output format (`"json"` or `"csv"`).
  - `deliveryMethod` (*Optional*, string, default: `"INTERNAL"`): Delivery destination (`"INTERNAL"`).
  - `mutationId` (*Optional*, string, max: 100): Idempotency key.
  - `reason` (*Optional*, string, max: 500): Scheduling note.

#### Response
- **Status 200 OK**:
```json
{
  "success": true,
  "schedule": {
    "id": "sch_345",
    "report_id": "rep_123",
    "cron": "0 8 * * *",
    "format": "json",
    "active": true,
    "next_run_at": "2026-10-14T08:00:00Z",
    "version": 0
  }
}
```

#### Error Status Codes
- `400 Bad Request`: Unsupported or invalid cron expression.
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient role permissions.
- `404 Not Found`: Report definition not found.
- `422 Unprocessable Entity`: Missing `cronExpression` or invalid payload format.

---

### 5. List Report Executions

Retrieves a paginated list of report execution jobs for the tenant.

- **Route:** `GET /reports/executions` (or `GET /api/reports/executions`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `ACCOUNTANT`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Query Parameters:**
  - `limit` (*Optional*, integer, default: `100`, range: `1`–`1000`): Maximum executions to return.
  - `offset` (*Optional*, integer, default: `0`, min: `0`): Pagination offset.

#### Response
- **Status 200 OK**:
```json
[
  {
    "id": "exec_789",
    "report_id": "rep_123",
    "status": "COMPLETED",
    "format": "json",
    "fileUrl": "/reports/executions/exec_789/content",
    "created_at": "2026-10-13T12:00:00Z"
  }
]
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Tenant mismatch or unauthorized caller.
- `422 Unprocessable Entity`: Pagination bounds exceeded.

---

### 6. Get Report Execution Details

Retrieves metadata and current status for a specific execution job.

- **Route:** `GET /reports/executions/{identifier}` (or `GET /api/reports/executions/{identifier}`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `ACCOUNTANT`

#### Request Parameters
- **Path Parameters:**
  - `identifier` (*Required*, string): Execution job identifier.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.

#### Response
- **Status 200 OK**:
```json
{
  "id": "exec_789",
  "report_id": "rep_123",
  "status": "COMPLETED",
  "format": "json",
  "fileUrl": "/reports/executions/exec_789/content",
  "created_at": "2026-10-13T12:00:00Z"
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient permission for underlying report definition.
- `404 Not Found`: Execution job not found in tenant.

---

### 7. Download Report Artifact Content

Streams the generated JSON payload or CSV file for a completed report execution.

- **Route:** `GET /reports/executions/{identifier}/content` (or `GET /api/reports/executions/{identifier}/content`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `ACCOUNTANT`

#### Request Parameters
- **Path Parameters:**
  - `identifier` (*Required*, string): Execution job identifier.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.

#### Response
- **Status 200 OK**:
  - `Content-Type`: `application/json` or `text/csv; charset=utf-8`
  - `Content-Disposition`: `attachment; filename="report.{format}"`
  - `Cache-Control`: `private, no-store`

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient permission to view report type.
- `404 Not Found`: Execution job not found.
- `409 Conflict`: Report export is not yet completed (`status != 'COMPLETED'`).

---

### 8. Process Report Execution Job

Processes a pending execution job directly in the request context, generating and persisting the report snapshot.

- **Route:** `POST /reports/executions/{identifier}/process` (or `POST /api/reports/executions/{identifier}/process`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `ACCOUNTANT`

#### Request Parameters
- **Path Parameters:**
  - `identifier` (*Required*, string): Execution job identifier to process.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.

#### Response
- **Status 200 OK**:
```json
{
  "id": "exec_789",
  "status": "COMPLETED",
  "fileUrl": "/reports/executions/exec_789/content",
  "attempts": 1
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Caller lacks permission for underlying report definition.
- `404 Not Found`: Execution job not found.

---

### 9. Retry Failed Report Execution

Resets a failed execution job back to `QUEUED` status for reprocessing.

- **Route:** `POST /reports/executions/{identifier}/retry` (or `POST /api/reports/executions/{identifier}/retry`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `ACCOUNTANT`

#### Request Parameters
- **Path Parameters:**
  - `identifier` (*Required*, string): ID of the failed execution job.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Request Body (application/json):**
  - `expectedVersion` (*Required*, integer, ge: 0): Optimistic locking version of the execution record.
  - `mutationId` (*Optional*, string, max: 100): Idempotency key.
  - `reason` (*Optional*, string, max: 500): Justification for retry.

#### Response
- **Status 200 OK**:
```json
{
  "id": "exec_789",
  "status": "QUEUED",
  "version": 2
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient role permissions.
- `404 Not Found`: Execution job not found.
- `409 Conflict`: Record version mismatch or execution job is not in `FAILED` status.

---

### 10. List Report Schedules

Lists all active and paused report schedules in the tenant.

- **Route:** `GET /reports/schedules` (or `GET /api/reports/schedules`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `ACCOUNTANT`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.

#### Response
- **Status 200 OK**:
```json
[
  {
    "id": "sch_345",
    "report_id": "rep_123",
    "cron": "0 8 * * *",
    "format": "json",
    "active": true,
    "next_run_at": "2026-10-14T08:00:00Z",
    "version": 0
  }
]
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient permissions or tenant mismatch.

---

### 11. Toggle Schedule Active State

Activates or pauses an existing report schedule.

- **Route:** `POST /reports/schedules/{identifier}/active` (or `POST /api/reports/schedules/{identifier}/active`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `ACCOUNTANT`

#### Request Parameters
- **Path Parameters:**
  - `identifier` (*Required*, string): Schedule identifier.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.
- **Request Body (application/json):**
  - `active` (*Required*, boolean): `true` to enable schedule, `false` to pause.
  - `expectedVersion` (*Required*, integer, ge: 0): Optimistic locking version of the schedule row.
  - `mutationId` (*Optional*, string, max: 100): Idempotency key.
  - `reason` (*Optional*, string, max: 500): Audit justification.

#### Response
- **Status 200 OK**:
```json
{
  "id": "sch_345",
  "active": false,
  "version": 1
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient role permissions.
- `404 Not Found`: Schedule record not found.
- `409 Conflict`: Optimistic locking version mismatch.

---

### 12. Periodic Scheduler Tick

Evaluates due schedules for the tenant and enqueues execution jobs. Typically invoked by an external cron worker.

- **Route:** `POST /reports/scheduler/tick` (or `POST /api/reports/scheduler/tick`)
- **Authentication:** Authenticated tenant caller.

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant selector.

#### Response
- **Status 200 OK**:
```json
{
  "executionsQueued": 1
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.

---

### 13. Access Shared Report Token

Validates an external report share token. Public report sharing is currently disabled and consistently returns 404.

- **Route:** `GET /reports/shared/{token}` (or `GET /api/reports/shared/{token}`)

#### Request Parameters
- **Path Parameters:**
  - `token` (*Required*, string): Report share token.

#### Response
- **Status 404 Not Found**:
```json
{
  "detail": "Report share not found"
}
```

---

## Practical Report Workflows

### Generating and Downloading an Inventory Valuation Snapshot
1. **Create or Lookup Definition:** Call `POST /reports` with `type: "INVENTORY_VALUATION"` or fetch existing definitions via `GET /reports`.
2. **Enqueue Execution:** Call `POST /reports/{report_id}/execute` specifying `format: "json"` or `"csv"`. Obtain the returned `executionId`.
3. **Process Job:** The background worker picks up the job, or run manual processing via `POST /reports/executions/{executionId}/process`.
4. **Download Artifact:** Once status is `COMPLETED`, retrieve report data via `GET /reports/executions/{executionId}/content`.

---

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
