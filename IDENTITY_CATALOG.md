# Python parity: identity and tenant catalog

This first implementation slice provides persisted tenant/admin setup, password
hashing, one-hour HS256 access tokens, role-populated request identity and tenant
catalog isolation, quantity variants, barcode assignment/scanning and stock
operations. Serial tracking and quantity kits are now implemented; see
[serials and kits](SERIALS_KITS.md).
Lot-backed costing and valuation have since been implemented; see `COSTING.md`
for methods, receipt fields, opening-stock pricing and migration instructions.

The shared React Python adapter now loads `/inventory/products` directly and
surfaces server errors rather than synthesizing unnamed products from stock or
returning an empty catalog on failure. Products with no variants are visible.
Create a parent product through `/inventory/products`, then add quantity variants
through `/inventory/products/{id}/variants`. Assign or generate a Code128 barcode
through `/barcodes/assign` or `/barcodes/generate`. Scanning supports `receiving`
and `pos`; unsupported contexts are rejected. Configure `CORS_ORIGINS` for the
client deployment; defaults allow the local clients on ports 3000 and 5173.

Stock routes at `/inventory` and `/api/inventory` support list, movement history,
receive, dispatch, allocate, release-allocation and fulfill-allocation. Mutations
accept `sku`, `locationId`, positive integer `quantity` (or `amount`), optional
`mutationId` and `expectedVersion`. Reusing a mutation ID with the same payload
returns the original result; changing its payload returns 409. Supply a stable
mutation ID when retrying requests. Concurrent version conflicts return 409.
Stock quantity, allocation, movement history and outbox enqueue share one database
transaction. Dispatch cannot consume reserved stock. A catalog SKU must exist
in the authenticated tenant. Stock uses integral base units; transfers and named
unit conversions are now implemented in `TRANSFERS_UOM.md`. Cost layers and valuation
are now implemented as described in `COSTING.md`.

Real Kafka publication, leased retries, dead-letter recovery and worker lifecycle
are now implemented; see [event delivery](EVENT_DELIVERY.md). Stock command
responses guarantee durable enqueue; downstream delivery remains asynchronous.

Configure `DATABASE_URL` (async SQLAlchemy URL) and `JWT_SECRET`. Dependencies
include PyJWT. No production secret or authentication fallback is supplied.

For a fresh Python database, run from this directory:

```powershell
python -m scripts.migrate_identity_catalog
```

For an existing Python catalog: back up the database, run
`python -m scripts.migrate_identity_catalog --identity-only`, provision the intended
tenant with `POST /auth/setup`, then run the migration with
`--legacy-tenant YOUR_TENANT_ID`. All legacy products are assigned to that tenant.
Do not use this shortcut for a catalog whose products belong to multiple tenants;
prepare an explicit ownership mapping instead. Run against the Python database,
not a sibling backend's similarly named table. PostgreSQL legacy migration is
transactional; old SQLite databases require a separate reviewed migration.

Setup accepts `tenantId`, `orgName`, `adminName`, `adminEmail`, `adminPassword`
(minimum eight characters). Existing tenant setup returns 409 and cannot replace
an administrator. Login accepts `tenantId`, `email`, `password` and returns
`token`. Both endpoints also have `/api/auth/...` aliases. Registration is now
closed by default, with operator bootstrap, persistent rate limits, current-user
permission checks and session revocation; see `SECURITY.md`.

Pass `Authorization: Bearer TOKEN` to `/products`. The authenticated tenant owns
all catalog operations; a conflicting `X-Tenant-ID` or `tenantId` query parameter
returns 403. Identical SKUs can exist in different tenants. Reader role USER can
list/read; ADMIN/MANAGER can create. The `/products` route retains its legacy
SKU/name/decimal-price contract while `/inventory/products` supplies the shared
client product/variant/barcode shape.

## Product Catalog API Specification

The `/products` endpoints provide direct CRUD access for tenant products using the domain product entities and value objects (`SKU`, `Money`).

### 1. Create Product

Creates a new product with an SKU, name, optional description, and decimal price amount for the authenticated tenant.

- **Route:** `POST /products`
- **Allowed Roles:** `ADMIN`, `MANAGER`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token containing user identity and tenant claims.
  - `X-Tenant-ID` (*Optional*, string): Tenant selector matching token's tenant claim.
- **Request Body (application/json):**
  - `sku` (*Required*, string): Unique alphanumeric Stock Keeping Unit (e.g., `ITEM-001`). Must be unique per tenant.
  - `name` (*Required*, string): Product display name.
  - `description` (*Optional*, string): Optional product description. Default is `null`.
  - `price_amount` (*Required*, string): Decimal string representation of price amount (e.g., `"20.50"`). Must be non-negative.

```json
{
  "sku": "PROD-1001",
  "name": "Industrial Widget",
  "description": "Standard heavy-duty widget",
  "price_amount": "29.99"
}
```

#### Response
- **Status 201 Created**:
```json
{
  "id": "prod_1a2b3c4d",
  "sku": "PROD-1001",
  "name": "Industrial Widget",
  "description": "Standard heavy-duty widget",
  "price_amount": "29.99"
}
```

#### Error Status Codes
- `400 Bad Request`: Invalid SKU format, negative or invalid price amount, or duplicate SKU in the tenant.
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient role permissions (requires `ADMIN` or `MANAGER`) or tenant mismatch.

---

### 2. Get Product by ID

Retrieves an existing product by its unique product identifier.

- **Route:** `GET /products/{product_id}`
- **Required Permission:** `catalog:read`

#### Request Parameters
- **Path Parameters:**
  - `product_id` (*Required*, string): Unique product identifier.
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Tenant identifier.

#### Response
- **Status 200 OK**:
```json
{
  "id": "prod_1a2b3c4d",
  "sku": "PROD-1001",
  "name": "Industrial Widget",
  "description": "Standard heavy-duty widget",
  "price_amount": "29.99"
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: User lacks `catalog:read` permission or tenant mismatch.
- `404 Not Found`: No product found matching `product_id` for the authenticated tenant.

---

### 3. List Products

Lists products belonging to the authenticated tenant with pagination support.

- **Route:** `GET /products`
- **Required Permission:** `catalog:read`

#### Request Parameters
- **Query Parameters:**
  - `limit` (*Optional*, integer): Maximum number of products to return (default: `100`).
  - `offset` (*Optional*, integer): Pagination offset count (default: `0`).
- **Headers:**
  - `Authorization` (*Required*, string): Bearer token.
  - `X-Tenant-ID` (*Optional*, string): Tenant identifier.

#### Response
- **Status 200 OK**:
```json
[
  {
    "id": "prod_1a2b3c4d",
    "sku": "PROD-1001",
    "name": "Industrial Widget",
    "description": "Standard heavy-duty widget",
    "price_amount": "29.99"
  }
]
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: User lacks `catalog:read` permission or tenant mismatch.

---

### Practical Workflows

#### Catalog Onboarding and Verification
1. Authenticate as an administrator or manager (`POST /auth/login`) to obtain a JWT Bearer token.
2. Register products via `POST /products` with an assigned SKU, name, and unit price.
3. Query `GET /products` or `GET /products/{product_id}` with `catalog:read` permissions to inspect active products across tenant facilities.

The database dependency no longer redirects models into unprovisioned tenant
schemas or sets persistent connection-level tenant state. Explicit tenant scopes
are implemented for the mounted APIs. `SECURITY.md` covers the later ownership
audit, forced PostgreSQL policies and explicit failures for unfinished features.

Tests now use actual setup/login rather than globally mocking every request as an
administrator. Serial variants now have identified stock/lifecycle commands in
`SERIALS_KITS.md`. Financial journal posting is now implemented in
`FINANCIAL_ACCOUNTING.md`; advanced identity features remain follow-up work. Reviewed count
reconciliation and offline submissions are now implemented; see `COUNTS.md`.
Revocation, throttling and restricted-role PostgreSQL checks are now implemented.

## Verification (2026-10-11)

- Full Python regression suite: 304 tests passed.
- Final focused run after constraint/auth hardening and two additional cases:
  12 tests passed across authentication, catalog/barcodes, stock and migrations.
- Shared client adapter: 2 tests passed; TypeScript `--noEmit` passed.
- Python compilation and Git whitespace checks passed.

Database-backed API tests use SQLite. PostgreSQL legacy migration and real
simultaneous buyers have not been exercised against a live database in this
first change; costing's PostgreSQL migration/concurrency checks have since passed
as recorded in `COSTING.md`. Tests do cover stale versions, duplicate
mutation IDs, insufficient available/allocated stock, tenant boundaries and
rollback on injected outbox failure. Deprecation warnings remain in existing
lifespan/test-client/time handling.

No database migration was applied to a running store, and no commit or deployment
was made in that first slice. Costing and valuation are now implemented. Remaining
parity work includes downstream integrations and durable report jobs. Procurement and replenishment are
now implemented as described in `PROCUREMENT.md`. Physical counts now
reconcile through explicit manager approval as described in `COUNTS.md`.
Customer/supplier returns, quarantine decisions and inventory write-offs are now
implemented as described in `RETURNS.md`.
