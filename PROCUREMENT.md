# Procurement and replenishment

The Python backend now persists suppliers, purchase orders, receipts, supplier
shipping notices and reorder policies. All private routes use verified tenant
identity. Managers/admins manage procurement; readers/accountants can inspect
orders and policies. Warehouse operators cannot manage POs or change purchase
prices. Supplier users access only their administrator-bound active supplier.

## Suppliers and purchase orders

Store routes below also have `/api` aliases. Write commands require a stable
`mutationId`; replays return the saved result and changed payloads return 409.
Audit actions retain actor, timestamp, request and result. A replay from another
actor is rejected. Supplier names are unique within a tenant.
Repeated approve/send commands already in that state return without another
issue event, including when submitted with a new mutation ID.

| Endpoint | Behavior |
|---|---|
| `GET/POST /suppliers` | List/create `{name,email?,leadTimeDays?,mutationId}` |
| `PATCH /suppliers/{id}` | Partial update with `expectedVersion` and `mutationId`; omitted fields stay unchanged |
| `GET/POST /purchase-orders` | List/create real server-owned orders |
| `GET /purchase-orders/{id}` | Read owned order, line receipts, costs and outstanding quantity |
| `POST /purchase-orders/{id}/approve` | Draft → approved |
| `POST /purchase-orders/{id}/send` | Approved → sent/issued |
| `POST /purchase-orders/{id}/receive` | Persist a partial or final delivery |
| `POST /purchase-orders/{id}/cancel` | Cancel the remaining balance, with a reason |
| `GET /purchase-orders/{id}/asns` | Inspect the supplier's persisted shipping notices |

PO creation accepts `{supplierId,locationId?,items,expectedDeliveryDate?,
mutationId,currency?}`. Items are `{sku,quantity,unitCostCents}`. SKUs must exist
in the current tenant, each SKU occurs once, quantities are positive integer
units and costs are nonnegative integer cents. Explicit zero cost is supported.
Only USD is accepted. Costs and quantities are bounded by the existing inventory
limits; the total must fit JavaScript's exact integer-cent range.

Order items are immutable after creation, preventing receipt cost from changing
after approval. Cancel/recreate a wrong order. Commands accept optional
`expectedVersion`; stale versions return 409. Draft/approved orders cannot receive
stock. Partial receipts preserve each line's outstanding balance. Cancelling a
partially received order leaves its received stock/costs intact and stops the
remaining balance from counting as incoming supply.

`send` persists the issued state and enqueues an event. The application does not
email a PO or contact a supplier. Asynchronous Kafka publication is now
implemented in [event delivery](EVENT_DELIVERY.md); supplier communication still
requires a downstream consumer. No response claims successful remote delivery.

## Atomic receiving

Receipts require `{receiptId,items:[{sku,quantity,lotNumber?,expirationDate?,
quarantined?}],expectedVersion?}`. Use the same receipt ID and exact payload when
retrying a delivery. Receipt IDs are scoped to the PO; a changed payload returns
409. Separate real deliveries require different IDs.

All lines are validated against the outstanding balance before mutation.
Over-receipts, duplicate/unknown SKUs, nonpositive/fractional quantities and
receiving a closed/cancelled order are rejected. Stock location and unit cost come
from the PO, not receipt input. Aware expiration timestamps/lot metadata enter the
same FIFO/LIFO/WAC/FEFO machinery as ordinary receipts.

PO balances/status, stock, cost layers, movement references, retry records and
outbox events commit together. An error on any line rolls everything back.
Concurrent deliveries serialize with the tenant inventory write lock. Identical
retries receive once; competing deliveries cannot exceed the order. Completed
receipt replays remain valid after the order closes. No financial payable/journal
or tax entry is posted by receipt.

## Replenishment

`GET/POST /reorder-policies` persists one policy per SKU/location. Supply
`supplierId`, `reorderPoint`, optional `safetyStock`, `targetStock`,
`economicOrderQuantity`, `leadTimeDays`, `casePack`, `minimumOrderQuantity`,
`unitCostCents` and `mutationId`. Updating an existing policy requires its
`expectedVersion`. Lead time defaults to the supplier's configured lead time at
policy creation/update. Explicit target stock overrides the compatibility default
of reorder point + safety stock + configured order increment. The increment is
not a statistical EOQ calculation.

`GET /reorder-policies/suggestions` and `POST /reorder-policies/evaluate` return
explanations and suggested quantities, without placing an order:

- Usable available stock excludes allocations and expired/quarantined layers.
- Incoming stock sums only unreceived lines of sent, acknowledged, shipped and
  partially received orders at the same location. Draft/approved/cancelled orders
  are excluded; supplier acknowledgment/ASN never adds stock.
- Lead-time demand is the ceiling of actual dispatched/fulfilled units in the
  last 30 days × lead days ÷ 30. It is an inventory-dispatch proxy for sales,
  not a POS demand forecast; seasonal/returns-aware forecasting is future work.
- Effective reorder point is the greater of configured point and lead-time
  demand + safety stock. Target is at least that point. Below/at the point, the
  shortage is rounded upward to meet MOQ and case-pack multiples.

`POST /reorder-policies/{id}/draft` takes `expectedVersion` and `mutationId`,
re-evaluates current stock/incoming supply under the write lock, and creates an
explicit manager-requested draft. Unknown policy cost or no shortage returns 409;
inactive suppliers cannot receive new orders. Suggestions flag inactive suppliers.
Drafts do not count as incoming, so managers should approve/send or discard their
chosen draft before asking for another. No autonomous ordering is enabled.

## Supplier portal, client and migration

The `/api/supplier-portal` uses the current user's persisted supplier binding.
Only issued/received POs for that supplier appear; drafts and other suppliers'
orders return no data/not-found. Acknowledgments accept aware expected delivery
dates and `mutationId`. ASNs accept positive shipped quantities, tracking/estimated
delivery metadata and `mutationId`; cumulative shipping cannot exceed ordered
quantities. Neither acknowledgment nor ASN adjusts warehouse stock. Performance
uses completed measured PO deliveries; unknown quality rates remain null.

The Python client now lists server orders without a browser ID cache and surfaces
fetch failures. Its shared supplier-name form resolves existing owned suppliers,
or creates a new named supplier before creating the PO; ambiguous/inactive names
are rejected. Pending creation/receipt IDs survive uncertain network failures in browser
session storage. Partial-receipt UI defaults to outstanding lines only.
Dedicated supplier-contact and replenishment workbench controls remain future UI
work; APIs and adapter methods expose those operations now.

Before deployment, run `python -m scripts.migrate_identity_catalog` with the
migration account. It creates the five procurement tables and forced RLS; PO and
policy policies verify supplier ownership, ASN policies verify PO/supplier
ownership. Use a non-superuser/non-BYPASSRLS runtime login. Migration is repeatable.
No existing store database was migrated during development.

Supplier returns are now implemented; see `RETURNS.md`. Remaining work includes
invoice matching/payables, taxes,
foreign currencies, native PO packaging/invoice price conversion, email/EDI
delivery and demand forecasts beyond the
dispatch-history proxy. Existing stock/count and integration delivery limitations
remain documented in `COSTING.md`, `COUNTS.md` and `SECURITY.md`.

Named-unit stock operations and transfers are implemented in `TRANSFERS_UOM.md`.
PO lines/receipts and policy thresholds remain integral base-unit contracts and
reject named-unit fields. Usable dispatched transfers now count as incoming
supply at their destination; transfers are excluded from sales demand.

## Verification

80 distinct focused Python tests passed across procurement/replenishment,
stock/costing/count regressions, supplier authorization, identity and migration
checks, including follow-ups for partial updates and repeated state commands.
The three dedicated PostgreSQL scenarios passed with real concurrent requests,
repeatable migrations and a non-owner/non-superuser runtime role. They prove
duplicate deliveries receive once, competing deliveries cannot over-receive,
and forged cross-supplier parent links fail database policies. An initial database
attempt hit temporary container bootstrap shutdown; all scenarios passed after
the final TCP listener/database was confirmed ready.

22 distinct client adapter/panel tests passed across the focused runs, including
creation/receipt network retries and outstanding-line defaults. TypeScript,
Python compilation, CI YAML parsing and Git whitespace checks passed. This was
a targeted regression selection, not a new whole-repository coverage measurement.
No application database was migrated or service deployed.
