# Python serials and kits

Serial lifecycle commands and kit operations are now durable, tenant-scoped
workflows. Stock, cost layers, history/manifests, actor-bound retry records and
outbox events commit together. Run `python -m scripts.migrate_identity_catalog`
with the migration account before deploying. No running store database was
migrated or service deployed during this implementation.

## Serial identities

Create a catalog variant with `trackingMode: "serial"`, or register the first
serial for an unused SKU. Tracking requires discrete eaches and cannot be enabled
over historical quantity movements, existing stock or a quantity kit/component.
Existing quantity stock needs an explicit identity migration; the application
does not invent serial numbers or reinterpret its receipt history.

Serial numbers are case-sensitive and unique within a tenant across SKUs. They
accept 1–100 characters from letters, digits, dot, underscore, colon and hyphen,
beginning with a letter or digit. Separate tenants may use the same number.

| Endpoint (also under /api) | Purpose |
|---|---|
| GET /serials | List owned serials; optional sku, limit and offset |
| GET /serials/trace/{number} | Identity, version, status, location, reservation and actor/reason history |
| POST /serials | Register sku, serialNumber, locationId, mutationId and reason; optional referenceId |
| POST /serials/{number}/{action} | Versioned lifecycle command |

Every lifecycle command requires `expectedVersion`, `mutationId`, `reason` and
`referenceId`. Initial receipt may include `unitCostCents` and `expirationDate`
(an ISO timestamp with timezone). Actors and tenants come from verified identity.
Unknown IDs return 404, stale versions/invalid transitions return 409, and invalid
fields fail explicitly. Repeating the identical actor/command ID returns its
saved response, including after a later lifecycle transition; changed payloads
or actors cannot reuse that ID.

| Action | Effect |
|---|---|
| receive | pending -> in_stock; transferred -> in_stock at its destination |
| reserve / release | Reserve an in-stock serial for referenceId, or release the matching reservation |
| sell | in_stock -> sold; consume the selected identity's layer and recognize its actual cost |
| return | sold -> returned; restore one quarantined unit at its original cost and expiration |
| restock | returned/quarantined -> in_stock, without another receipt or cost change |
| quarantine / damage | Hold physical stock and prevent sale |
| write-off | Remove physical stock and carrying value; written_off is terminal |
| transfer | in_stock -> transferred; remove source on-hand and retain owned value in transit until receipt |
| price | Establish a known cost for an unpriced serial physically on hand |

The transition graph also permits pending quarantine/cancellation, returned
damage/write-off, quarantined damage/write-off and damaged quarantine/write-off.
Pending quarantine represents a real receipt. Pending write-off cancels an
unreceived identity without creating stock. Physical-state changes require
release of a reservation first; selling a reserved serial requires its matching
reference. Expired, quarantined and damaged items cannot sell. Returning goods
preserves their expiry and never makes expired goods saleable.

Serials use **specific identification**, including when the tenant's quantity
stock uses WAC. A sale consumes that serial's one-unit layer rather than another
unit's FIFO/LIFO layer or an average price. Unknown prices remain null; accounting
can use the explicit `price` action. Returns reverse the last sale's recorded cost.
Valuation reports label serialized SKUs SPECIFIC_IDENTIFICATION and retain their
value in transit. Partial aggregate valuation previews reject unidentified serial
selections; trace the selected serial for its carrying cost.

Admins/managers register identities. Warehouse operators can perform operational
serial commands but cannot supply/read costs or write off goods. Accounting can
price unpriced serials. Write-offs require the existing manager/admin returns
permission. Readers can trace/list; supplier identities cannot access these APIs.

Generic quantity receiving, sales, allocations, adjustments, PO receipts, returns,
barcode stock scans and transfers reject mutations of serial-tracked SKUs. Use
identified serial commands so physical balances and identity histories cannot
diverge. Native serial lists on PO/RMA/count/scanning contracts and cancellation
of dispatched serial transfers remain additional work. A matching aggregate
physical count does not establish that each serial identity was scanned.

## Kit formulas and operations

Kit output must be an existing quantity SKU in discrete eaches. A formula contains
1–100 distinct owned component SKUs with positive integer base-unit quantities.
Components may use their own weight/volume base units. Self-reference, nested kits
and serial components are rejected. Serial components need explicit identity
selection, which this quantity kit contract does not offer.

| Endpoint (also under /api) | Purpose |
|---|---|
| GET /kits, GET /kits/{sku} | List or inspect owned formulas |
| POST /kits | Create/replace sku, name and components with expectedVersion, mutationId and reason |
| POST /kits/assemble | Consume components and create physical kit stock |
| POST /kits/disassemble | Remove traceable assembled stock and restore its saved components |
| POST /kits/sale | Sell a virtual bundle by consuming components directly |

Operations require `kitSku`, `locationId`, `quantity` (1–100), formula
`expectedVersion`, `stockVersions`, `mutationId`, `reason` and `referenceId`.
Assembly requires current versions for every component and the kit output (zero
when the output row does not exist). Bundle sale requires component versions.
Disassembly requires the current kit stock version. Destination increases
serialize using the tenant inventory lock. A late component failure rolls back
all earlier component/stock/cost changes.

Assembly uses the configured FIFO/LIFO/WAC/FEFO rules, protects reservations and
rejects expired/quarantined or unpriced components. It records each assembled
unit's actual component quantities, lots, expiries and consumed value. The kit
expires with its earliest expiring component. Assembly/disassembly recognize
zero sale COGS; value moves between components and kits without rounding loss.
Virtual bundle sales recognize component COGS and create no kit quantity.
Physical assembled kits can be sold through the ordinary kit-SKU stock dispatch.

Serial and virtual-bundle component sales feed replenishment demand; usable
serialized goods in transit count as incoming supply at their destination.
Virtual bundle component movements retain their consumed layers and COGS, so
the existing customer RMA workflow can return those components. Physical aging
also reads the new stock movement types.

Formula edits increment their version and apply to future assemblies. Disassembly
uses saved manifests, preserving the original composition after an edit. It
restores only unsold, eligible, unreserved units with a surviving assembly layer;
it cannot fabricate components from externally purchased or returned kit stock.
Restored lots retain their original expiration and receipt dates. Under WAC, the
kit's current carrying value is allocated across its original components before
restoring their pools; this preserves total value rather than reversing later
pool averaging. Fractional base-unit prices retain exact integer-cent totals.

Managers/admins define formulas; warehouse operators can assemble, disassemble
and sell bundles. Costs are redacted for non-accounting roles. Assembled-kit
location transfers require manifest relocation support and are explicitly
rejected by the generic transfer workflow. Nested/serial kits, manufacturing
labor/overhead, partial-unit disassembly and financial journal posting remain
outside this implementation.

## Persistence, client and verification

The repeatable migration creates `python_serial_tracking`, `python_serials`,
`python_kits` and `python_kit_builds`. PostgreSQL forces tenant policies and checks
owned product, variant, cost-layer, kit and manifest-component parents. Runtime
logins use transaction-local tenant context with neither superuser nor BYPASSRLS.

The Python React adapter exposes registration, versioned serial commands,
formula management and versioned kit operations. Existing serial tracing uses
real history. Existing assembly/disassembly methods capture server formula/source
versions and retain an identical command across uncertain responses and repeated
references. The server always records the authenticated actor. Dedicated serial
action and kit-formula editing screens remain client follow-up work.

Tests cover lifecycle/reservation rules, serial-specific costs, unpriced pricing,
expiry after returns, terminal states, value in transit, all four kit costing
methods, fractional base costs, historical manifests, roles/redaction, foreign
identities, stale versions and transaction rollback on enqueue/late-component
failure. Independent-request contracts exercise duplicate receipts, competing
sales/assemblies and restricted-role PostgreSQL parent/RLS rejection. The latter
is included in backend CI. Client transport tests and TypeScript checks verify
version/retry payloads and history transport.

Local verification on 2026-10-12:

- Final serial/kit, costing, procurement and return API selection: 60 passed.
- Corrected and expanded catalog/serial variant suite: 4 passed.
- Broader PostgreSQL regression selection: 13 passed. Final expanded serial/kit
  SQLite/PostgreSQL concurrency, demand and parent-policy contracts: 2 passed.
- Shared Python client adapter: 22 passed; TypeScript `--noEmit` passed.
- Python compilation, CI YAML parsing and Git whitespace checks passed.
- The broad Python run passed 483 tests and skipped 9 opt-in contracts; its only
  failure was the old assertion that serial variants must be rejected. That
  assertion was updated, and the complete catalog suite then passed as above.
  The final integration edits were checked by the focused API/database selections.
