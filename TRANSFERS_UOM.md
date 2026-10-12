# Python transfers and units of measure

Python now persists internal location transfers and product units, following the
GraphQL transfer lifecycle and PHP UOM model. Commands use tenant ownership,
optimistic versions, retry IDs and atomic stock/cost/outbox updates. This is
operational progress, not complete four-backend API equivalence.

## Transfers

All endpoints are mounted with and without `/api`.

| Endpoint | Purpose |
|---|---|
| `POST /inventory/transfers` | Create a DRAFT with source/destination IDs, items, optional reference and mutationId |
| `GET /inventory/transfers` | List owned transfers with bounded limit/offset |
| `GET /inventory/transfers/{id}` | Inspect a transfer and lot/cost snapshots |
| `POST /inventory/transfers/{id}/dispatch` | Dispatch all goods with expectedVersion, reason, mutationId and sourceVersions keyed by SKU |
| `POST /inventory/transfers/{id}/receive` | Receive all dispatched goods with transfer version, reason and mutation ID |
| `POST /inventory/transfers/{id}/cancel` | Cancel a draft or confirm the complete shipment physically returned to source |

Items contain sku, positive quantity, optional lotNumber, and optionally unit and
uomVersion. Each SKU occurs once and locations must differ. Location IDs are
tenant-scoped logical identifiers. States are DRAFT -> DISPATCHED -> RECEIVED,
or DRAFT/DISPATCHED -> CANCELLED. Drafts do not reserve goods. Dispatch rechecks
source versions, availability and eligible lots, preserving allocated goods.
Expired/quarantined lots cannot dispatch.

Dispatch removes on-hand stock and snapshots transit quantity, value, original
layer IDs, lot/expiry and receipt dates. Destination goods become on-hand on
receipt. Transit cannot be sold or counted as physically present. Goods expiring
in transit remain unsellable after receipt. Cancelling a dispatched shipment
means the operator confirms all goods returned. Completed/cancelled transfers
reject further transitions. Each command is atomic across all items and effects.

Transfers recognize zero sale COGS. Carrying value moves through transit into
destination layers; WAC redistributes its pool. Unknown prices remain unknown,
and WAC requires a priced source pool. Method changes are blocked during transit.
Valuation retains on-hand totalQuantity/totalValueCents and adds inTransitQuantity,
inTransitValueCents, totalOwnedQuantity and totalOwnedValueCents. Location filters
attribute transit to its destination. Quantity-specific valuation previews cover
on-hand goods. Unpriced owned inventory fails valuation explicitly. Replenishment
includes usable incoming transfers, excludes expired transit and does not count
transfers as sales demand.

Identical actor/mutation/payload retries return saved results. Changed payloads,
stale versions and competing movements fail without partial updates. Warehouse
operators can manage transfers but cannot inspect nested costs. Readers can
inspect; supplier users cannot access these workflows.

## Units and precision

| Endpoint | Purpose |
|---|---|
| `GET /uom/units` | Inspect standard weight/volume coefficients |
| `GET /uom/configurations/{sku}` | Read owned configuration |
| `POST /uom/configurations` | Create/replace configuration with expectedVersion and mutationId |
| `POST /uom/configurations/{sku}/convert` | Preview quantity and optional price conversion |

Configurations define sku, baseUnit, category (discrete/weight/volume),
purchaseUnit, saleUnit and rules. Discrete rules use a unit code and positive
decimal-string factorToBase, for example {"unit":"case","factorToBase":"24"}.
Cases, packs, dozens and pallets are product-specific. Purchase/sale preferences
must be known and never reinterpret unnamed quantities. Weight/volume use the
shared PHP table's decimal coefficients, visible in /uom/units, without overrides.
Conversion arithmetic is exact over those coefficients.

The ledger stores integral base units. Named-unit inputs accept integers or
decimal strings with at most six places. Floats, exponents, nonpositive mutations
and non-integral base results are rejected. Half a case of 24 becomes 12 eaches;
1.5 kilograms becomes 1500 grams when grams are base. Choose a base that exactly
represents operational quantities. The integer stock limit applies after conversion.

Configured base/category are immutable. Legacy stock, procurement, reorder or
transfer history cannot be reinterpreted from eaches into weight/volume. Pack
rules can change with versions. Transfer quantities are snapshotted at creation;
later pack edits cannot change an existing shipment.

Receive, dispatch, allocate, release-allocation and fulfill-allocation accept
unit/uomVersion on /inventory. Named-unit mutations require a stable mutation ID;
write-offs also support conversion. Receipt unitCostCents is per input unit. The
full invoice value rounds to cents, half up: two cases of 24 at 1001 cents/case
add 48 eaches worth 2002 cents.

Cost layers now have optional immutable initialValueCents. FIFO/LIFO/FEFO use
cumulative proportional rounding during partial depletion; WAC retains pool
rounding. Full depletion conserves receipt cents, including sub-cent prices per
gram. For exact-value layers unitCostCents is a rounded display value, so quantity
times that price is not exact valuation. Returns, transfers, write-offs and count
adjustments preserve actual value. Existing whole-cent layers retain prior behavior.

Previews return exact decimals or numerator/denominator for non-terminating
results, with ledgerRepresentable and uomVersion. Optional unitCostCents returns
exact converted price and a separately labelled rounded display price. Do not
reconstruct invoice totals from that display price.

PO ordering/receiving, reorder thresholds, physical counts, RMA quantities and
barcode scans remain integral base-unit contracts. Convert quantities first;
named-unit fields are rejected rather than ignored. Native PO packaging/invoice
pricing and UOM-labelled barcodes remain future work. A fractional-cent base price
cannot fit the PO's integer per-base-unit purchase price.

## Persistence, client and remaining scope

The explicit identity/catalog migration adds python_stock_transfers,
python_inventory_actions, python_uom_configs and nullable
python_cost_layers.initial_value_cents. Repeat runs preserve stock/value.
PostgreSQL forces tenant isolation and owned product links for configurations
and transfer items. No store database was migrated or service deployed.

The Python client exposes transfer, UOM and named-unit stock methods with caller
IDs/versions. Inventory rows show base-unit labels. Dedicated transfer/UOM panels,
partial transfer receipt, transit losses, intercompany accounting, serials and
automatic supplier-return lineage after relocation remain future work. Cancelled
shipments restore new source layers while retaining the original transfer trace.

## Verification

Focused domain/API regressions cover four costing methods, cent conservation,
rollback/retries, reservations, expiry, tenant/role controls, pack edits, exact
conversions and count/procurement/return compatibility. Independent SQLite sessions
cover competing dispatches, duplicate receipts and concurrent UOM edits. A
repeatable legacy-schema upgrade preserves original whole-cent carrying values.

Restricted-role PostgreSQL contracts are wired into CI. Local PostgreSQL checks
remain pending because Docker did not respond. Client TypeScript is checked
separately; Vitest remains unverified because its worker times out before loading
tests. This is targeted regression evidence, not full-suite coverage measurement.
