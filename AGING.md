# Inventory Aging & Dead Stock Analysis

This subsystem provides inventory aging calculations, dead stock detection, and liquidation recommendations based on stock movement history and cost layer valuation.

## Overview

Inventory aging evaluates how long on-hand stock has remained idle in a facility. The aging calculation tracks historical stock movements (receipts, dispatches, kit assembly, and serial transactions) to determine days since last activity and categorizes stock into standard duration buckets.

The Dead Stock Recommendation Engine identifies slow-moving or obsolete items and suggests disposition strategies (such as markdown, donation, or liquidation) to recover working capital.

All aging endpoints support multi-tenancy and require appropriate Role-Based Access Control (RBAC) permissions.

## Aging Buckets and Rules

Stock age is categorized into five chronological buckets:
- `0_30_DAYS`: Fast-moving or recently received stock (0 to 30 days since last movement).
- `31_60_DAYS`: Normal velocity stock (31 to 60 days).
- `61_90_DAYS`: Slowing stock (61 to 90 days).
- `91_180_DAYS`: At-risk inventory (91 to 180 days). Markdown recommended if overstocked.
- `OVER_180_DAYS`: Dead stock (>180 days since last movement).

### Recommended Actions
When stock is classified as dead stock (>180 days):
- **LIQUIDATE**: Triggered when remaining quantity exceeds 10 units.
- **DONATE**: Triggered when remaining quantity is 10 units or fewer.
- **MARKDOWN**: Triggered for stock aged 91 to 180 days with excess inventory.
- **NONE**: Standard condition with no immediate disposition required.

## API Specification

Endpoints are available at both root `/aging` and `/api/aging` prefixes.

### 1. Generate Aging Report Snapshot

Generates or retrieves a point-in-time inventory aging snapshot across all active stock in the tenant.

- **Route:** `GET /aging/report` (or `GET /api/aging/report`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `ACCOUNTANT`

#### Request Parameters
- **Headers:**
  - `Authorization` (*Required*): Bearer token containing user identity and tenant claims.
  - `X-Tenant-ID` (*Optional*): Tenant identifier; must match token's tenant claim if provided.

#### Response
- **Status 200 OK**:
```json
{
  "type": "INVENTORY_AGING",
  "asOf": "2026-10-31T09:00:00Z",
  "rows": [
    {
      "sku": "AGING-SKU",
      "locationId": "store",
      "quantity": 50,
      "age0To30": 20,
      "age31To60": 15,
      "age61To90": 10,
      "age91To180": 5,
      "ageOver90": 5,
      "valueCents": 50000
    }
  ]
}
```
*Note: Value information is redacted for non-accounting / warehouse operator queries in accordance with security policies.*

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: User does not have an authorized role or tenant mismatch.

---

### 2. Analyze SKU Aging and Dead Stock

Performs a detailed movement-based aging and dead stock evaluation for a specific SKU and warehouse location.

- **Route:** `GET /aging/{sku}` (or `GET /api/aging/{sku}`)
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `ACCOUNTANT`

#### Request Parameters
- **Path Parameters:**
  - `sku` (*Required*, string): Unique stock keeping unit identifier.
- **Query Parameters:**
  - `location_id` (*Required*, string): Warehouse or store location identifier (e.g., `store`, `default`).
  - `tenant_id` (*Optional*, string): Optional explicit tenant selector; must match token tenant claim.
- **Headers:**
  - `Authorization` (*Required*): Bearer token.
  - `X-Tenant-ID` (*Optional*): Tenant identifier.

#### Response
- **Status 200 OK**:
```json
{
  "sku": "AGING-SKU",
  "is_dead_stock": false,
  "aging_bucket": "0_30_DAYS",
  "days_since_last_movement": 12,
  "locked_capital_cents": 20000,
  "recommended_action": "NONE",
  "potential_scrap_emissions_kg": null
}
```

#### Error Status Codes
- `401 Unauthorized`: Missing or invalid Bearer token.
- `403 Forbidden`: Insufficient role permissions or cross-tenant query attempted.
- `404 Not Found`: No stock record exists for the given SKU and location.

## Practical Workflows

### Evaluating Slow-Moving Stock for Markdown
1. Call `GET /api/aging/report` to scan across warehouse locations for rows where `age91To180` or `ageOver90` is nonzero.
2. For identified candidate SKUs, call `GET /api/aging/{sku}?location_id={locationId}` to inspect `days_since_last_movement`, `locked_capital_cents`, and the `recommended_action`.
3. If `recommended_action` is `MARKDOWN` or `LIQUIDATE`, merchandising teams can initiate promotional campaigns or clearance workflows.
