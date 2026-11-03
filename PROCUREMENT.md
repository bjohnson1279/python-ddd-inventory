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

The `/api/supplier-portal` routes provide an external portal for authenticated suppliers bound to the `SUPPLIER_USER` role. All endpoints require the user to have an active `supplier_id` binding on their identity; requests without a supplier binding return 403 Forbidden. Supplier users can only view and interact with purchase orders explicitly assigned to their bound supplier.

| Endpoint | Behavior | Roles |
|---|---|---|
| `GET /api/supplier-portal/pos` | List open/received purchase orders for the bound supplier | `SUPPLIER_USER` |
| `POST /api/supplier-portal/pos/{po_id}/acknowledge` | Acknowledge a purchase order with expected delivery date and mutationId | `SUPPLIER_USER` |
| `POST /api/supplier-portal/asns` | Submit an Advance Shipping Notice (ASN) for a purchase order | `SUPPLIER_USER` |
| `GET /api/supplier-portal/asns` | List Advance Shipping Notices submitted by the bound supplier | `SUPPLIER_USER` |
| `GET /api/supplier-portal/performance` | Retrieve delivery performance metrics (OTIF percentage) | `SUPPLIER_USER` |

### Endpoint Specifications

#### `GET /api/supplier-portal/pos`
Lists purchase orders for the authenticated supplier that are in open (`SENT`, `ACKNOWLEDGED`, `SHIPPED`, `PARTIALLY_RECEIVED`) or `RECEIVED` status. Drafts and orders belonging to other suppliers are excluded.
- **Headers:**
  - `x-tenant-id` (Optional, string): Active tenant identifier (defaults to user identity context).
- **Query Parameters:**
  - `limit` (Optional, integer, default: 100, min: 1, max: 1000): Maximum records to return.
  - `offset` (Optional, integer, default: 0, min: 0): Number of records to skip.
- **Response (200 OK):**
  - `pos` (array of objects): List of purchase orders in standard PO DTO format (`{id, poNumber, supplierId, status, items, ...}`).

#### `POST /api/supplier-portal/pos/{po_id}/acknowledge`
Allows a supplier to acknowledge an issued purchase order (`SENT` or `ACKNOWLEDGED` state) and confirm an expected delivery date. Moves the PO status to `ACKNOWLEDGED`.
- **Path Parameters:**
  - `po_id` (Required, string): Unique purchase order identifier.
- **Headers:**
  - `x-tenant-id` (Optional, string): Active tenant identifier.
- **Request Body (JSON):**
  - `expected_delivery_date` (Required, ISO-8601 aware datetime string): Confirmed expected delivery timestamp.
  - `mutationId` (Required, string, min: 1, max: 100): Idempotency key for replay protection.
- **Response (200 OK):**
  - Returns `{"success": true, ...po_dto}` with updated status `ACKNOWLEDGED` and incremented version.
- **Error Codes:**
  - `400 Bad Request`: Invalid request payload.
  - `403 Forbidden`: User lacks `SUPPLIER_USER` role or has no supplier binding.
  - `404 Not Found`: Purchase order does not exist or belongs to another supplier.
  - `409 Conflict`: Purchase order is not in `SENT` or `ACKNOWLEDGED` status.

#### `POST /api/supplier-portal/asns`
Submits an Advance Shipping Notice (ASN) recording dispatched quantities against an open purchase order. If the order is not already `PARTIALLY_RECEIVED`, the order status transitions to `SHIPPED`.
- **Headers:**
  - `x-tenant-id` (Optional, string): Active tenant identifier.
- **Request Body (JSON):**
  - `po_id` (Required, string): Target purchase order identifier.
  - `tracking_number` (Required, string, min: 1, max: 200): Shipment tracking reference.
  - `estimated_delivery_date` (Required, ISO-8601 aware datetime string): Estimated arrival timestamp.
  - `items` (Required, array of objects, min items: 1, max items: 1000):
    - `sku` (Required, string, min: 1, max: 100): Product SKU code (each SKU may appear at most once).
    - `shipped_quantity` (Required, integer, min: 1, max: 2,147,483,647): Quantity shipped in base units.
    - `lot_number` (Optional, string, min: 1, max: 100): Supplier lot/batch designation.
  - `mutationId` (Required, string, min: 1, max: 100): Idempotency key.
- **Response (200 OK):**
  - `{"success": true, "asn_id": str, "status": str}`.
- **Error Codes:**
  - `400 Bad Request`: Duplicate SKUs or invalid payload structure.
  - `403 Forbidden`: User unauthenticated or missing supplier binding.
  - `404 Not Found`: Target purchase order not found for this supplier.
  - `409 Conflict`: PO is not open for shipping, or cumulative shipped quantity exceeds ordered/outstanding quantities.

#### `GET /api/supplier-portal/asns`
Lists all Advance Shipping Notices submitted by the authenticated supplier within the tenant.
- **Headers:**
  - `x-tenant-id` (Optional, string): Active tenant identifier.
- **Query Parameters:**
  - `limit` (Optional, integer, default: 100, min: 1, max: 1000): Maximum records to return.
- **Response (200 OK):**
  - `asns` (array of objects): `[{"id": str, "po_id": str, "tracking_number": str, "estimated_delivery_date": str, "items": [...]}, ...]`.

#### `GET /api/supplier-portal/performance`
Computes fulfillment performance metrics for the authenticated supplier across completed (`RECEIVED`) purchase orders.
- **Headers:**
  - `x-tenant-id` (Optional, string): Active tenant identifier.
- **Response (200 OK):**
  - `performance` (object):
    - `completed_orders` (integer): Total number of completed (`RECEIVED`) purchase orders.
    - `measured_orders` (integer): Orders having both `expected_delivery_date` and `received_at` timestamps.
    - `otif_percentage` (float or null): On-Time In-Full percentage (orders received on or before expected delivery date); null if `measured_orders` is 0.
    - `defect_rate_percentage` (null): Reserved for future quality rate integration.

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
