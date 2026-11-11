# Python tenant and security coverage

Every mounted private API request verifies its signed token against the current
user and a persisted, unexpired, unrevoked login session. The user must still be
active and the token's authorization version must match the user record. Roles
and action permissions come from the current database identity, not token role
claims or caller-selected headers. API responses use `Cache-Control: no-store`.

## Session and account controls

- JWTs retain HS256 algorithm pinning, issuer/audience checks and one-hour expiry.
  They now require a session ID and authorization version. Tokens issued before
  this migration are rejected; users must sign in again.
- `POST /auth/logout` revokes the current session. Administrative revocation,
  role changes, disabling/enabling users and password changes invalidate all
  outstanding sessions for the affected user. In-flight requests authorized
  before a change may complete; subsequent requests recheck the database.
- `POST /auth/password` verifies the current password, changes its PBKDF2 hash,
  revokes existing sessions and requires a new login. Passwords/hashes/tokens are
  excluded from user-list/profile responses and security audit records.
- Login and registration use persistent, atomic five-minute fixed-window limits:
  ten attempts per account/tenant identity and thirty per connection IP per
  action. Limits include successful attempts, span both URL aliases and workers,
  and return 429 with `Retry-After`. Counter identifiers are HMAC digests. The
  application does not inspect caller-provided forwarding headers to choose IP;
  configure the server's trusted-proxy boundary correctly when deployed.
- Tenant registration is closed by default. Set `SETUP_TOKEN` and provide
  `X-Setup-Token` for operator-controlled bootstrap. `ALLOW_PUBLIC_SIGNUP=true`
  explicitly enables public registration, for example in a development setup.
  Never publish the operator bootstrap token in the shared browser client.

## Administrative API

User endpoints also have `/api` aliases. Administrators can list/invite users,
change their role/status and revoke their sessions; target lookups always use the
authenticated tenant. Last-active-administrator demotion or disabling returns
409. Administrative changes serialize on the tenant record and produce scoped
security audit events. The shared client receives its expected user/role and
permission DTO shapes. Generated temporary passwords are returned only to the
administrator creating that user.

| Endpoint | Behavior |
|---|---|
| `GET /auth/me` | Current verified user's profile |
| `GET /users` | Owned tenant's users, without credential fields |
| `POST /users` | Invite/create `{email, name?, role, password?, supplierId?}` |
| `PATCH /users/{id}/role` | Set a supported role and revoke target sessions |
| `PATCH /users/{id}/status` | Set `{isActive}` and revoke target sessions |
| `POST /users/{id}/revoke-sessions` | Force target user to sign in again |
| `GET /roles`, `GET /roles/permissions` | Read built-in role/action definitions |

Supported roles are ADMIN, MANAGER, USER, VIEWER, WAREHOUSE_OPERATOR, ACCOUNTANT
and SUPPLIER_USER. Definitions are fixed server policies, not mutable custom
roles. USER/VIEWER can read catalog, stock, valuation and reports; warehouse
operators can perform stock operations and assigned counts, but cannot supply
purchase costs or read COGS/valuation. Accountants can read accounting data and
manage costing settings/opening prices. Managers can manage retail operations
but cannot administer users. Supplier users require an administrator-assigned
supplier binding and are limited to their identity/supplier permissions.

## Administrative and Identity API Reference

All endpoints in this family are available under both root and `/api` prefixes (e.g. `/auth/login` and `/api/auth/login`). Responses include `Cache-Control: no-store`.

### 1. Provision Tenant and Initial Administrator (`POST /auth/setup`)
Initializes a new tenant partition along with its primary administrator account. When `ALLOW_PUBLIC_SIGNUP` is false (default), callers must supply the operator bootstrap token in `X-Setup-Token`.

- **Route:** `POST /auth/setup` (or `POST /api/auth/setup`)
- **Allowed Roles:** Public / Bootstrap (rate-limited, protected by `X-Setup-Token` when public signup is disabled)
- **Headers:**
  - `X-Setup-Token` (*Optional*, string): Operator bootstrap token required when `ALLOW_PUBLIC_SIGNUP=false`.
- **Request Body (`application/json`):**
  - `tenantId` (*Required*, string, length 1–100, pattern: `^[A-Za-z0-9_-]+$`): Unique tenant identifier.
  - `orgName` (*Required*, string, length 1–200): Display name of the organization/company.
  - `adminName` (*Required*, string, length 1–200): Full name of the initial administrator.
  - `adminEmail` (*Required*, string, length 3–254): Email address for the initial administrator.
  - `adminPassword` (*Required*, string, length 8–1024): Master password for administrator login.
- **Response:**
  - `201 Created`:
    ```json
    {
      "tenantId": "org-main",
      "userId": "user-uuid"
    }
    ```
- **Error Status Codes:**
  - `400 Bad Request`: Validation failure on input fields.
  - `403 Forbidden`: Tenant registration is disabled or invalid `X-Setup-Token`.
  - `409 Conflict`: Tenant identifier already exists.
  - `429 Too Many Requests`: Throttled by fixed-window rate limiter.
- **Workflow:** Executed during system onboarding to provision an initial tenant and its founding administrator.

---

### 2. User Login (`POST /auth/login`)
Authenticates credentials against a tenant and issues a signed HS256 JWT access token associated with a persisted login session.

- **Route:** `POST /auth/login` (or `POST /api/auth/login`)
- **Allowed Roles:** Public / Unauthenticated (rate-limited)
- **Request Body (`application/json`):**
  - `tenantId` (*Required*, string, length 1–100): Target organization tenant ID.
  - `email` (*Required*, string, length 3–254): Registered user email address.
  - `password` (*Required*, string, length 1–1024): Plaintext password to verify against PBKDF2 hash.
- **Response:**
  - `200 OK`:
    ```json
    {
      "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    }
    ```
- **Error Status Codes:**
  - `401 Unauthorized`: Invalid tenant, email, password, or inactive user account.
  - `429 Too Many Requests`: Throttled after exceeding rate limits (10 attempts per account/tenant or 30 per IP per 5-minute window).
- **Workflow:** Primary entry point for UI clients and automated API clients to obtain a 1-hour bearer session token.

---

### 3. Get Current User Profile (`GET /auth/me`)
Retrieves profile metadata and permissions for the currently authenticated identity.

- **Route:** `GET /auth/me` (or `GET /api/auth/me`)
- **Allowed Roles:** All authenticated roles (`ADMIN`, `MANAGER`, `USER`, `VIEWER`, `WAREHOUSE_OPERATOR`, `ACCOUNTANT`, `SUPPLIER_USER`) with `identity:self` permission.
- **Headers:**
  - `Authorization` (*Required*, string): `Bearer <token>` session token.
- **Response:**
  - `200 OK`:
    ```json
    {
      "id": "usr-123",
      "userId": "usr-123",
      "tenantId": "org-main",
      "email": "admin@example.com",
      "name": "Admin User",
      "role": "ADMIN",
      "isActive": true,
      "supplierId": null
    }
    ```
- **Error Status Codes:**
  - `401 Unauthorized`: Missing, expired, or revoked token.
  - `404 Not Found`: User record does not exist in the tenant.
- **Workflow:** Called by web and mobile client applications on startup to populate current user context, display name, and role permissions.

---

### 4. Logout Session (`POST /auth/logout`)
Terminates and revokes the caller's active session, invalidating the current token.

- **Route:** `POST /auth/logout` (or `POST /api/auth/logout`)
- **Allowed Roles:** All authenticated roles with `identity:self` permission.
- **Headers:**
  - `Authorization` (*Required*, string): `Bearer <token>` session token.
- **Response:**
  - `200 OK`:
    ```json
    {
      "success": true
    }
    ```
- **Error Status Codes:**
  - `401 Unauthorized`: Invalid or already revoked token.
- **Workflow:** Invoked when a user signs out to ensure the token cannot be reused even if unexpired.

---

### 5. Change Password (`POST /auth/password`)
Verifies the caller's current password, updates the stored PBKDF2 hash to the new password, revokes all existing sessions, and generates an audit event.

- **Route:** `POST /auth/password` (or `POST /api/auth/password`)
- **Allowed Roles:** All authenticated roles with `identity:self` permission.
- **Headers:**
  - `Authorization` (*Required*, string): `Bearer <token>` session token.
- **Request Body (`application/json`):**
  - `currentPassword` (*Required*, string, length 1–1024): Current plaintext password for re-authentication.
  - `newPassword` (*Required*, string, length 8–1024): Desired new password.
- **Response:**
  - `200 OK`:
    ```json
    {
      "success": true,
      "loginRequired": true
    }
    ```
- **Error Status Codes:**
  - `401 Unauthorized`: Incorrect current password or invalid session.
- **Workflow:** Enables self-service credential updates while invalidating lingering sessions across all devices.

---

### 6. List Tenant Users (`GET /users`)
Lists all registered users within the caller's tenant, ordered by email. Credential hashes are excluded.

- **Route:** `GET /users` (or `GET /api/users`)
- **Allowed Roles:** `ADMIN` (`users:manage` permission).
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Must match token tenant context.
- **Response:**
  - `200 OK`:
    ```json
    {
      "users": [
        {
          "id": "usr-1",
          "userId": "usr-1",
          "tenantId": "org-main",
          "email": "admin@example.com",
          "name": "Admin",
          "role": "ADMIN",
          "isActive": true,
          "supplierId": null
        }
      ]
    }
    ```
- **Error Status Codes:**
  - `401 Unauthorized`: Missing or invalid authentication token.
  - `403 Forbidden`: Non-admin user or tenant ID mismatch.
- **Workflow:** Populates user administration grids and identity management dashboards.

---

### 7. Invite / Create User (`POST /users`)
Provisions a new user account within the tenant. Generates a cryptographically random temporary password if none is supplied.

- **Route:** `POST /users` (or `POST /api/users`)
- **Allowed Roles:** `ADMIN` (`users:manage` permission).
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
- **Request Body (`application/json`):**
  - `email` (*Required*, string, length 3–254): Email address for the new user.
  - `name` (*Optional*, string, max length 200, default `""`): Full display name (falls back to email if omitted).
  - `role` (*Optional*, string, default `"USER"`): One of `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `WAREHOUSE_OPERATOR`, `ACCOUNTANT`, `SUPPLIER_USER`.
  - `password` (*Optional*, string, length 8–1024, default `null`): Explicit initial password. If omitted, a secure temporary password is generated.
  - `supplierId` (*Optional*, string, length 1–100, default `null`): Bound supplier ID. Required if and only if role is `SUPPLIER_USER`.
- **Response:**
  - `201 Created`:
    ```json
    {
      "id": "usr-new",
      "userId": "usr-new",
      "tenantId": "org-main",
      "email": "operator@example.com",
      "name": "Warehouse Lead",
      "role": "WAREHOUSE_OPERATOR",
      "isActive": true,
      "supplierId": null,
      "temporaryPassword": "generated-secure-token"
    }
    ```
    *Note: `temporaryPassword` is returned only when no explicit password was supplied.*
- **Error Status Codes:**
  - `400 Bad Request` / `422 Unprocessable Entity`: Validation failure (unknown role or invalid supplier binding).
  - `403 Forbidden`: Non-admin caller.
  - `404 Not Found`: Bound supplier does not exist or is inactive.
  - `409 Conflict`: User email already exists in this tenant.
- **Workflow:** Onboards team members, operators, accountants, or supplier contacts into the workspace.

---

### 8. Update User Role (`PATCH /users/{user_id}/role`)
Updates an existing user's role assignment and optional supplier binding. Automatically revokes the target user's active sessions, increments their authorization version, and records an audit log entry.

- **Route:** `PATCH /users/{user_id}/role` (or `PATCH /api/users/{user_id}/role`)
- **Allowed Roles:** `ADMIN` (`users:manage` permission).
- **Path Parameters:**
  - `user_id` (*Required*, string): ID of the user to update.
- **Request Body (`application/json`):**
  - `role` (*Required*, string): Target role (`ADMIN`, `MANAGER`, `USER`, `VIEWER`, `WAREHOUSE_OPERATOR`, `ACCOUNTANT`, `SUPPLIER_USER`).
  - `supplierId` (*Optional*, string, default `null`): Bound supplier ID; required if role is `SUPPLIER_USER`, prohibited otherwise.
- **Response:**
  - `200 OK`: Updated user DTO.
- **Error Status Codes:**
  - `403 Forbidden`: Caller lacks administrator role.
  - `404 Not Found`: Target user or bound supplier not found.
  - `409 Conflict`: Attempted demotion of the last active administrator in the tenant.
  - `422 Unprocessable Entity`: Mismatch between `SUPPLIER_USER` role and `supplierId` field.
- **Workflow:** Elevates staff permissions or assigns supplier scoping.

---

### 9. Update User Active Status (`PATCH /users/{user_id}/status`)
Enables or disables a user account. Disabling a user immediately revokes all their active sessions and logs an audit event.

- **Route:** `PATCH /users/{user_id}/status` (or `PATCH /api/users/{user_id}/status`)
- **Allowed Roles:** `ADMIN` (`users:manage` permission).
- **Path Parameters:**
  - `user_id` (*Required*, string): ID of the user to update.
- **Request Body (`application/json`):**
  - `isActive` (*Required*, boolean): Account status (`true` to enable, `false` to disable).
- **Response:**
  - `200 OK`: Updated user DTO.
- **Error Status Codes:**
  - `403 Forbidden`: Caller lacks administrator role.
  - `404 Not Found`: Target user not found.
  - `409 Conflict`: Attempted disabling of the last active administrator in the tenant.
- **Workflow:** Offboards departing personnel or re-enables seasonal warehouse operators.

---

### 10. Force Session Revocation (`POST /users/{user_id}/revoke-sessions`)
Invalidates all outstanding login sessions for a specific user by incrementing their authorization version and marking sessions revoked in the database.

- **Route:** `POST /users/{user_id}/revoke-sessions` (or `POST /api/users/{user_id}/revoke-sessions`)
- **Allowed Roles:** `ADMIN` (`users:manage` permission).
- **Path Parameters:**
  - `user_id` (*Required*, string): Target user identifier.
- **Response:**
  - `200 OK`:
    ```json
    {
      "success": true
    }
    ```
- **Error Status Codes:**
  - `403 Forbidden`: Caller lacks administrator role.
  - `404 Not Found`: Target user not found.
- **Workflow:** Used during security incident response or when a device is reported lost or stolen.

---

### 11. Read Role Definitions (`GET /roles`)
Returns the complete list of system roles alongside their action permissions for the authenticated tenant.

- **Route:** `GET /roles` (or `GET /api/roles`)
- **Allowed Roles:** `ADMIN` (`users:manage` permission).
- **Response:**
  - `200 OK`:
    ```json
    [
      {
        "id": "ADMIN",
        "name": "ADMIN",
        "isCustom": false,
        "tenantId": "org-main",
        "permissions": [
          {"id": "catalog:read", "resource": "catalog", "action": "read"},
          {"id": "catalog:write", "resource": "catalog", "action": "write"}
        ]
      }
    ]
    ```
- **Workflow:** Used by administrative settings panels to render role permission matrices.

---

### 12. Read Permission Definitions (`GET /roles/permissions`)
Returns a flat dictionary list of all discrete system permissions.

- **Route:** `GET /roles/permissions` (or `GET /api/roles/permissions`)
- **Allowed Roles:** `ADMIN` (`users:manage` permission).
- **Response:**
  - `200 OK`:
    ```json
    [
      {"id": "accounting:read", "resource": "accounting", "action": "read"},
      {"id": "accounting:write", "resource": "accounting", "action": "write"}
    ]
    ```
- **Workflow:** Supplies resource/action definitions to UI role viewers.

## Ownership and permission boundaries

All supplied tenant selectors are checked, including duplicate query/header
values, snake/camel case and nested JSON selectors. The selected tenant always
comes from verified identity. Cross-tenant object IDs return not-found responses.

- Count repositories require tenant scope, including parent joins for line
  items. Operators can submit only assigned counts; administrators/managers can
  work with owned-tenant counts. Missing, closed or unpersisted counts cannot
  create fabricated successful submissions. Persisted lines/status are saved;
  stock/cost reconciliation is now implemented through manager review; see `COUNTS.md`.
- Report definitions are persisted and tenant-owned. Execution/scheduling first
  verify ownership, then return 501 until durable jobs are implemented. Unverified
  report-share tokens return 404; arbitrary tokens never become download URLs.
- Notification read/snooze/escalation use both tenant and current recipient.
  A caller cannot choose someone else's inbox. Escalation targets must be active
  administrators/managers in the same tenant. Notifications are persisted.
- Aging reads actual owned stock/movements/cost layers. Unknown costs remain null;
  emissions are not fabricated. This remains a simplified movement-based aging
  view, not a complete unit-age/ESG reporting implementation.
- GraphQL product reads use the verified tenant and real catalog rows, replacing
  mock products. There are no unguarded product queries or write mutations.
- Supplier routes now use persisted owned suppliers/POs and shipping notices;
  see `PROCUREMENT.md`. Inactive/foreign bindings are rejected, acknowledgments
  and ASNs are durable, and neither operation adjusts warehouse stock.
- Signed integration webhooks remain public only on their exact registered
  routes and retain their signature checks. Their real stock-ingestion/durable
  deduplication work is still pending. Dormant offline-sync/WebSocket modules are
  not mounted or advertised as secure production workflows.

## PostgreSQL defense in depth and migration

Run `python -m scripts.migrate_identity_catalog` with the privileged migration
connection before deploying. It upgrades existing identity columns, creates
session/rate/audit/report/notification tables, and installs repeatable forced RLS
policies on catalog, stock, movements, costs, configuration, reports,
notifications, security audit, count plans/records/lines and outbox payloads.
Notification and cost-layer policies also verify their parent ownership.

Use a non-superuser, non-`BYPASSRLS` runtime login. The request database dependency
sets `app.current_tenant_id` from the signed identity with transaction-local
`set_config`; it clears automatically on commit/rollback and cannot persist into
another pooled request. Scoped repositories still enforce ownership on SQLite
and when a privileged development database account bypasses RLS.

Identity/login/rate tables support public credential verification and bootstrap,
so their access remains explicitly scoped by the authentication/administrative
services rather than these business-data RLS policies. Migrations require a
privileged connection; the event worker iterates the tenant registry and sets
transaction-local tenant context using an ordinary non-BYPASSRLS runtime login.
Ordinary runtime access without tenant context sees no outbox/business rows.
Event stream counters also have forced tenant policies; event policies check
owned stream parents. Admin/manager outbox read/retry permissions, actor-bound
versioned retries and sanitized delivery errors are implemented in
[event delivery](EVENT_DELIVERY.md).

## Verification and remaining work

Financial read/write/management permissions are limited to administrators,
managers and accountants. Warehouse stock commands can produce their automatic
cost journals but cannot read financial sources, journals or commercial cash
records. Financial commands use actor-bound retries and owned-parent lookups
before replay. PostgreSQL forces policies on all financial tables; SQLite and
PostgreSQL also enforce immutable posted headers/lines and snapshot consistency.
See [financial accounting](FINANCIAL_ACCOUNTING.md) for activation, source review,
closing rules and database verification.

Serial registration/formula management require manager/admin permissions;
operators can perform serial and kit operations with cost redaction. Serial
pricing requires accounting permission, and serial write-offs require the
existing returns permission. Command IDs are actor-bound, owned identities are
checked before replay and stale versions fail without partial stock changes.
Four new tables have forced PostgreSQL policies with owned product, variant,
cost-layer, kit and manifest-component checks. See
[serials and kits](SERIALS_KITS.md) for workflows and concurrency/RLS verification.

Transfer commands and UOM configurations now have explicit permissions and
actor-bound retries. Operators can move goods but nested transfer cost snapshots
are redacted; only managers/admins configure units. Forced PostgreSQL policies
scope all three new tables and enforce owned product links. See
`TRANSFERS_UOM.md` for workflow tests. Its PostgreSQL contract has now passed
against the disposable database used for event-delivery verification.

Returns and write-offs require explicit read/write permissions. Their services
verify owned source movements, purchase receipts and quarantine stock before
retry replay. Forced PostgreSQL policies additionally enforce those parent
links. Customer return authorization caps quantities across requests, and
write-offs preserve allocated goods. Non-accounting stock responses redact the
new per-layer consumed `costCents` values. See `RETURNS.md` for workflow coverage
and workflow coverage. These new PostgreSQL policies have now passed their
dedicated contract against the disposable event-delivery test database.

The full regression run passed 350 tests with the opt-in PostgreSQL scenario
skipped in that run. Dedicated database runs separately passed both the existing
costing/migration scenario and the new security scenario using a real non-owner,
non-superuser login. The latter checks raw unfiltered queries, cross-tenant insert
rejection, parent ownership, outbox isolation, transaction/pool scope cleanup,
admin changes/audit persistence, role restrictions and immediate revocation.

Security API tests cover session logout, password changes, disabled users,
last-admin protection, cross-tenant IDs, forged role claims, role permissions,
duplicate/nested tenant selectors, operator assignments, recipient privacy,
report/GraphQL isolation, registration defaults and throttling. Older tests now
use real login rather than mocked administrator roles or permissive status sets.
The PostgreSQL scenarios are part of the dedicated backend CI job.

Reporting now has durable tenant-owned jobs, schedules, alert configuration,
conditions and notice links, with forced PostgreSQL policies and owned-parent
checks on all five new tables. Completed snapshots are immutable under both
SQLite and PostgreSQL. Creator permissions are rechecked during generation and
current download permissions are enforced on private artifacts. Notifications
are recipient-scoped; manager escalation is bounded and atomic with its outbox
event. Warehouse aging views expose quantity and age without costs. See
[reporting and alerts](REPORTING_ALERTS.md) for operation and verification.

Custom role editing, MFA/SSO, account-recovery delivery,
public report sharing and active WebSocket
authorization remain additional work. No store database was migrated or service
deployed during this change.

Workforce refinement adds explicit self/manager permissions. Operators access
only their own profile, shifts and source-backed activities; managers inspect
tenant records. User administration and workforce writes share a tenant lock so
disable/role edits serialize with eligibility checks. Four workforce tables have
forced PostgreSQL RLS with owned user/profile/shift/movement/count parent checks.
Task source identity is unique and task measurements are immutable under both
SQLite and PostgreSQL. See [workforce refinements](WORKFORCE.md).
