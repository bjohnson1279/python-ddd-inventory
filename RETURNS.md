# Python returns and write-offs

Customer returns now restore inventory and reverse the original recorded cost of
goods sold. Supplier returns ship stock from the original purchase receipt and
record expected supplier credit separately from inventory carrying value.
Write-offs remove stock at its current carrying value. Each operation persists
its stock movement, cost changes, workflow state, retry result and outbox event
in one transaction.

## Workflows and API

Endpoints accept authenticated tenant identity and are mounted with and without
`/api`. ADMIN/MANAGER can write; authorized reader/accounting roles can inspect.
WAREHOUSE and SUPPLIER roles cannot manage returns or write-offs.

| Endpoint | Purpose |
|---|---|
| `POST /returns/rmas` | Request a CUSTOMER or SUPPLIER return against `sourceMovementId`, with positive integer items keyed by `layerId`, reason and stable `mutationId` |
| `GET /returns/rmas` and `GET /returns/rmas/{id}` | Inspect owned requests and processing balances |
| `POST /returns/rmas/{id}/authorize` | Authorize the requested quantities using `expectedVersion`, reason and `mutationId` |
| `POST /returns/rmas/{id}/cancel` | Release the unprocessed authorization balance |
| `POST /returns/rmas/{id}/receive` | Receive customer goods using `receiptId`, `expectedVersion` and explicit items; partial receipt is supported |
| `POST /returns/rmas/{id}/ship` | Ship supplier goods using `shipmentId`, `expectedVersion` and explicit items; partial shipment is supported |
| `GET /returns/quarantine` | List remaining pending returned stock |
| `POST /returns/quarantine/{id}/resolve` | RESTOCK or SCRAP using `expectedVersion`, reason and stable `mutationId` |
| `POST /returns/write-offs` | Remove positive `quantity` from `sku`/`locationId`, with stock `expectedVersion`, reason and stable `mutationId`; optionally select a layer or lot |
| `GET /returns/write-offs` | Inspect owned write-off history |

Write-off reasons are DAMAGE, THEFT, EXPIRED, OBSOLETE and SHRINKAGE. Requests
reject fractional, zero and negative quantities. Receipt/shipment IDs are scoped
to their RMA. Retry the same command through the same route with the same payload;
it returns the saved result without changing stock twice. Changed payloads or
stale versions return 409. Foreign tenant references return 404.

Authorization reserves return entitlement against the original movement. All
authorized and processed requests share the original per-layer quantity cap.
Cancelling releases only the unprocessed balance; completed returns cannot be
cancelled. Authorization does not reserve physical supplier stock: shipment
rechecks the exact original receipt layer and available stock. Supplier states
are PARTIALLY_SHIPPED/SHIPPED with `shippedQuantity`; customer states are
PARTIALLY_RECEIVED/RECEIVED with `receivedQuantity`.

## Cost and disposition rules

Customer sources must be sales dispatches or allocation fulfillment with a
complete consumed-layer trace. New traces store immutable per-layer `costCents`.
Partial refunds use cumulative integer-cent allocation, so returning every unit
reverses exactly the original COGS under FIFO, LIFO, WAC and FEFO. Returned layers
retain original lot/expiry and enter inventory at the return time. WAC adjusts
the remaining pool without losing pennies.

Customer disposition defaults to QUARANTINE. RESTOCK adds eligible inventory;
QUARANTINE adds inventory blocked from sale; SCRAP records the return reversal
and an equal disposal loss without adding stock. Expired or quarantined origin
goods cannot be received directly into sellable stock. Quarantine RESTOCK also
rejects expired goods and changes eligibility without increasing quantity or
reversing COGS again. SCRAP removes only the pending returned layers. Write-offs
and negative count adjustments synchronize the remaining quarantine balance.

Legacy traced sales can reconstruct costs only when their recorded aggregate
and layer data support it. WAC allocates the recorded aggregate. An originally
unpriced sale remains unpriced even if its surviving layer is priced later.
History without a complete layer trace cannot establish return entitlement.

Supplier sources must be purchase-order-backed receipts owned by the tenant.
Shipment removes the exact receipt layer; another supplier's interchangeable
stock cannot satisfy it. Expected credit uses the original receipt unit price,
while carrying loss uses the current inventory method. `creditVarianceCents`
records their difference. Original PO received totals stay intact and the return
does not reopen incoming supply. Customer-restored layers are separate layers;
automatic linkage back to a supplier receipt remains future work.

Write-offs can remove expired/quarantined goods, protect allocated quantities,
and also preserve eligible goods backing outstanding allocations. They record
inventory loss separately from sale COGS.

## Persistence and client

Run the existing identity/catalog migration before using an existing database.
It creates `python_returns`, `python_return_actions` and
`python_return_quarantine`. PostgreSQL policies force tenant isolation and check
source movement, supplier, return and stock ownership. Use a runtime login that
is neither a superuser nor BYPASSRLS. No store database was migrated here.

The Python client adapter exposes return creation, review, receipt, supplier
shipment and write-off methods. The existing quarantine panel now reads real
pending quantities and retains the same decision payload across uncertain
network responses; a deterministic conflict clears it for a fresh version.
Dedicated return creation/approval and write-off screens remain future work.

These workflows record inventory/cost effects and references. They do not issue
cash refunds, settle supplier credits, post financial journals or send vendor
messages. Tax, foreign currencies, fractional UOMs and serial lifecycles remain
outside this implementation.

## Verification

Focused API/domain and existing costing, count, procurement, stock and tenant
regressions passed. Tests cover original-cost conservation, partial return caps,
cancel/retry behavior, quarantine, expiry, write-off reservation protection,
cross-tenant/role restrictions, unknown historical costs and injected outbox
rollback. Independent SQLite sessions prove duplicate receipt processing occurs
once and competing receipts cannot exceed the remaining balance.

The same concurrency contract includes an opt-in PostgreSQL restricted-role
scenario and is wired into backend CI. Local PostgreSQL verification remains
pending because the Docker daemon did not respond during this change. This is a
targeted regression selection, not a whole-repository coverage measurement.

The earlier client adapter run passed 14 tests. After adding the stale-decision
retry case and supplier shipment method, TypeScript verification passed, but the
final 15-test rerun could not start a Vitest worker in either pool. That final
client regression run remains unverified.
