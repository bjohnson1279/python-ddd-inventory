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

Custom role editing, MFA/SSO, account-recovery delivery,
durable report/sharing jobs and active WebSocket
authorization remain additional work. No store database was migrated or service
deployed during this change.
