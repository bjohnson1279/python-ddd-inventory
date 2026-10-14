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
