# Workforce refinements

Implemented 2026-10-13 for Python and its React client. Existing identity,
assignment and count permissions remain the foundation. The previous labor
calculation helpers had no persisted operator/shift API, and the example labor
dashboard used fixed scores. Python now has actual workforce records and a
panel under Operational Depth.

## Profiles and shifts

Administrators and managers configure active warehouse operators with a target
pick-task rate, maximum consecutive hours (1–16) and certifications. Profiles
have optimistic versions; profile changes cannot invalidate open assignments.
Disabling a user or changing their role is serialized with workforce commands.
There is no implicit operator creation for an arbitrary user ID.

Managers assign a location and an offset-aware start/end interval. Stored times
are UTC; the UI accepts local times and converts them explicitly. Overlapping
shifts and contiguous planned intervals beyond the operator limit are rejected.
Cancelled assignments do not block replacement shifts. Unrelated historical
shifts do not prevent future assignments after a profile limit changes.

PLANNED shifts can start within their planned interval or be cancelled by a
manager. ACTIVE shifts can complete, storing server-clock timestamps. Starting
rechecks active user status and certifications; an operator can have only one
active shift. Completed shifts cannot be restarted. Clock-out remains available
to managers even if the assigned operator has become ineligible. These are
operational records, not payroll or jurisdiction-specific labor compliance.

Assignment and cancellation produce personal inbox notices and outbox events in
the same transaction. A failed notification commit rolls back the shift command;
actor-bound retries cannot create another assignment notice.

## Activities and metrics

Operators see only their own profile, shifts, available sources and activity
records. Managers can inspect tenant operator metrics. Viewer, accountant,
supplier and general-user roles have no workforce access. No employee emails,
purchase costs or other operators' performance are exposed through self routes.

Activities link to a real source and require an active shift. PICK sources must
be dispatch/fulfillment movements performed by that operator, at that location,
after clock-in. COUNT sources must be completed reviewed counts assigned to that
operator and location. The source picker excludes already-recorded sources.
Each source can be recorded once across shifts. Reviewed count accuracy is
derived from the saved count lines, not a caller-supplied score. Task records
are immutable under both database engines.

Duration and optional traversal distance are operator-reported measurements,
labelled accordingly. Durations must be positive; recorded activities cannot
exceed the planned shift's total duration. Metrics are grouped by activity
recording date in UTC, with an optional 1–31 day window on the API. Picks count
recorded dispatch tasks, rather than SKU base units, avoiding comparisons such
as grams versus cases. The weighted rate is total pick tasks divided by their
total reported duration. For two picks taking 60 and 120 seconds, the result is
40 picks per reported hour.

Accuracy uses total accurate reviewed lines divided by total reviewed lines.
No observations means unknown, not 100% accuracy or zero productivity. Distance
is unknown if any included activity has no measurement. A completed count may
be recorded later; its performance appears on the recording day. There is no
claim of automatically measured walking distance, labor-hours utilization or
historical trend/ranking. Source records can support manager review, but reported
timing is not independently verified.

## Staffing plans

Managers create durable DRAFT plans from a target date, projected inbound and
outbound **task counts**, target tasks per hour, duration and required
certifications. The calculation is:

`ceil((inbound + outbound) / (target rate * shift hours))`

Zero workload legitimately recommends zero staff; zero/nonfinite capacity,
negative volumes and past dates are rejected. The result identifies its method
as EXPLICIT_VOLUME_HEURISTIC. It is not an ML forecast.

Plans show recommended headcount, eligible active operators and shortfall.
Eligibility means matching certifications and maximum-hour capacity; it does
not assert that staff are available or scheduled on that date. Publishing
rechecks current eligibility and retains the inputs and recommendation. A
published plan can expose a shortage; it does not silently generate assignments
or prevent a manager from acknowledging understaffing. Assign actual shifts
separately. Payroll, leave/availability, break planning, demand-forecast linking
and measured travel telemetry remain follow-up work.

## API and deployment

All endpoints have both root and `/api` aliases:

- `GET /workforce/me`, `/shifts`, `/shifts/{id}/sources` and
  `/shifts/{id}/activities`: self-scoped, with manager access.
- `GET /workforce/operators`, `/operators/{id}/metrics`, `/plans`: managers.
- `PUT /workforce/profiles/{userId}`: versioned profile configuration.
- `POST /workforce/shifts` and `/plans`: manager creation.
- `POST /workforce/shifts/{id}/start`, `/complete`, `/cancel`, `/activities`.
- `POST /workforce/plans/{id}/publish`: versioned publication.

Commands require a mutation ID and reason; transitions/profile updates also
require the expected version. Replays require the same actor and payload, and
current ownership/authorization is checked before returning saved results. The
client preserves command identity after an uncertain response and clears prior
tenant/session data when identity changes. Fetch failures show an explicit error.

The reviewed identity/catalog migration creates four new tables: operator
profiles, labor shifts, task performance and staffing plans. Forced PostgreSQL
RLS includes owned user, profile, shift, movement and count parent checks. Task
source-type consistency and duplicate prevention also have database constraints.
Deploy using the existing migration-owner/runtime-role separation in SECURITY.md
and confirm grants for these tables. No running store database was migrated or
service deployed during this implementation.

## Verification

Coverage exercises weighted and unknown metrics, reviewed count accuracy,
certifications, active-user eligibility, overlap/consecutive limits, strict
inputs, clock transitions, ownership, source deduplication, actor-bound retries,
notification rollback and database immutability. Dedicated contracts use SQLite
and a disposable PostgreSQL database with a non-superuser, non-owner runtime
login, testing concurrent assignment/replay, forced RLS, foreign-parent rejection
and repeatable migration. Client tests cover real source selection, role-specific
controls, unknown data, explicit failures and stable retry identities. Final
test counts are recorded in the implementation result.
