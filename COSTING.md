# Python costing and valuation

This implementation adds persisted inventory cost layers and tenant costing
configuration to the existing stock API. Receipts, dispatches, allocation
fulfillment, layer updates, movement history and outbox events share the same
database transaction. A failed cost/lot validation or outbox write rolls the
entire operation back. Stable mutation IDs protect receipt, sale and opening-cost
pricing retries.

## Methods and precision

- FIFO consumes the oldest eligible receipt first.
- LIFO consumes the newest eligible receipt first.
- FEFO consumes the earliest eligible expiration first, then receipt time and ID;
  lots without expiry follow dated lots. An expiry at or before the current UTC
  instant is ineligible. Quarantined lots cannot be dispatched or newly reserved.
- WAC uses a moving weighted average per tenant/SKU/location. It consumes physical
  lots in FIFO order unless a lot is explicitly selected. COGS rounds to integer
  cents, half up. Remaining carrying value is distributed across remaining units
  using largest remainders, preserving every cent through later receipts and full
  depletion. Original receipt unit costs remain available on the layers.

Costing accepts uppercase/lowercase method names and `weighted_average_cost` as
an alias for WAC. Specific identification is not implemented. Quantities are
discrete integers; purchase costs are nonnegative integer USD cents, including
explicit zero-cost goods. The catalog's selling price is never used as a guessed
purchase cost. Unit costs are bounded at 2,147,483,647 cents; carrying values are
bounded at JavaScript's exact-integer limit of 9,007,199,254,740,991 cents.

The tenant-level write lock serializes method changes with stock mutations.
Changing the costing method is rejected while any tenant stock is on hand;
existing stock is not silently reinterpreted. Accounting-method settings are
stored for shared-client compatibility. Cash/accrual financial journal posting,
taxes, foreign currencies, financial writeoff journals and returns cost reversal
are separate work. Inventory writeoffs and reviewed physical counts now adjust
carrying value through the workflow in `COUNTS.md`.

## API

Authenticated endpoints are available both with and without `/api`.

| Endpoint | Purpose |
|---|---|
| `GET /accounting/tenant-config/{tenantId}` | Read the owned tenant's settings; defaults FIFO/ACCRUAL/USD |
| `POST /accounting/tenant-config` | Set `{tenantId, costingMethod, accountingMethod}`; ADMIN/MANAGER |
| `POST /inventory/receive` | Receive quantity and create a layer |
| `POST /inventory/dispatch` | Consume available stock/layers and record COGS |
| `POST /inventory/fulfill-allocation` | Consume reserved stock/layers and record COGS once |
| `GET /accounting/cost-layers?sku=...&locationId=...` | Inspect owned layers, remaining value and pricing status |
| `POST /accounting/cost-layers/{id}/price` | Price a remaining unpriced layer with `{unitCostCents, mutationId}` |
| `GET /accounting/valuation-report?locationId=...` | Carrying-value report, aggregated by variant |
| `GET /accounting/valuation/{variantId}?quantity=...&locationId=...&method=...` | Read-only quantity-cost preview |

Receipt fields extend the existing `{sku, locationId, quantity, mutationId}`
payload with `unitCostCents`, optional `lotNumber`, timezone-aware
`expirationDate` and `receivedAt`, and `quarantined`. Future receipt times are
rejected. Dispatch/fulfillment may select a `lotNumber`; they cannot rewrite
receipt cost or expiry metadata. Barcode scans accept purchase-cost/lot/expiry
metadata when their context is `receiving` and run through the same transaction.

Receipts return `costLayerId` and `costStatus`. Sales return
`costOfGoodsSoldCents`, `costStatus`, `costingMethod`, and consumed layer/lot IDs
and quantities. Movement history and outbox payloads retain this result. Merely
reserving or releasing stock does not recognize COGS.

Full reports value owned stock, including expired/quarantined goods, until an
explicit disposal/writeoff workflow changes their ownership/value. Reports use
the configured carrying method; a conflicting `method` parameter returns 409.
Quantity previews can compare methods without writing layers or changing past
COGS. Partial previews include owned ineligible lots because they quote book
value, not permission to ship. WAC pools do not cross locations.

The Python React adapter now makes one server report request, honors location,
and surfaces valuation failures. It no longer reconstructs valuation through
per-variant calls or reports failed calculations as zero inventory. FEFO is
available in the shared accounting configuration selector.

## Existing stock and migration

Run `python -m scripts.migrate_identity_catalog` against the Python database
before deploying these routes. It creates the new tables and adds unpriced
opening layers for any existing stock quantities not covered by cost layers.
Running it again does not duplicate opening quantities. It does not alter
quantities or invent historical purchase costs. See `IDENTITY_CATALOG.md` for
assigning tenant ownership to older catalogs.

Unpriced layers use null purchase cost/value, distinct from explicitly free
stock. Valuation returns 409 until the remaining stock is priced. FIFO/LIFO/FEFO
can consume unpriced stock, but its COGS result remains null/UNPRICED. WAC refuses
dispatch while its pool has unpriced stock. ADMIN/MANAGER can price a remaining
unpriced layer without changing quantity; pricing recognizes the remaining
carrying value and creates an auditable movement/outbox event. This does not
retroactively fabricate COGS for previously unpriced sales.

## Verification

The full regression run passed 338 Python tests. Focused costing tests cover the
canonical 10 units at 100 cents plus 10 at 200 cents, selling 12: FIFO COGS 1,400,
LIFO 2,200, WAC 1,800; each remaining value reconciles to the 3,000-cent receipts.
They also cover FEFO order/eligibility, partial lots, reservation fulfillment,
rounding through depletion and additional receipts, tenant isolation, unpriced
stock, zero costs, retries, write rollback, repeatable opening migrations and
simultaneous buyers using independent SQLite connections.

An additional PostgreSQL 16 integration scenario passed against a disposable
container. It verifies legacy SKU-uniqueness migration, same SKUs in different
tenants, method previews, persisted COGS/value, WAC penny conservation,
simultaneous sales of the last unit, and concurrent duplicate receipts. This
scenario runs in a dedicated PostgreSQL CI job. Local opt-in runs use
`COSTING_TEST_DATABASE_URL` pointing at a disposable local `costing_test` database;
each run creates a unique test schema.

Client transport checks: 4 tests passed; TypeScript checking passed. Python
compilation and Git whitespace checks passed. Existing deprecation warnings
remain. No store database was migrated and no code was deployed.
