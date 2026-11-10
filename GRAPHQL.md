# GraphQL API Specification

The DDD Inventory system exposes a federated GraphQL API using [Strawberry GraphQL](https://strawberry.rocks/) mounted under `/graphql`. The endpoint provides read access to the tenant product catalog with multi-tenant isolation and role-based access control.

## Overview

The GraphQL schema is served via FastAPI integration (`strawberry.fastapi.GraphQLRouter`). All GraphQL operations require authentication and enforce tenant scoping, ensuring clients only query product records belonging to their active tenant context.

## Authentication & Authorization

GraphQL requests are protected by the application authentication and authorization middleware in `src/infrastructure/auth/security.py`:

- **Route Prefix:** `/graphql`
- **Required Permission:** `catalog:read`
- **Allowed Roles:** `ADMIN`, `MANAGER`, `USER`, `VIEWER`, `WAREHOUSE_OPERATOR`
- **Headers:**
  - `Authorization` (*Required*, string): Bearer JWT access token containing valid user claims (`sub`, `tenantId`, `roles`, `ver`, `jti`).
  - `X-Tenant-ID` (*Optional*, string): Explicit tenant identifier. If provided, it must match the tenant claim in the Bearer token; mismatches return `403 Forbidden`.

Unauthenticated requests or requests with expired/invalid tokens receive `401 Unauthorized`. Users lacking `catalog:read` permission (e.g. `SUPPLIER_USER`) receive `403 Forbidden`.

## Schema & Types

### Types

#### `ProductNode`
Represents an individual product within the tenant catalog.
- `id` (`String!`): Unique internal product identifier.
- `sku` (`String!`): Stock Keeping Unit code (unique per tenant).
- `name` (`String!`): Product display name.
- `priceAmount` (`String!`): Unit selling price represented as a decimal string.

### Query Root

```graphql
type Query {
  _service: _Service!
  product(id: String!): ProductNode
  products: [ProductNode!]!
}
```

#### Fields:
- `product(id: String!)`: Look up a single product by its unique identifier. Returns `ProductNode` or `null` if not found in the authenticated tenant.
  - Parameter `id` (*Required*, `String!`): The product ID to query.
- `products`: Returns an array of up to 100 products belonging to the authenticated tenant, ordered by product ID.
  - No arguments required.

## API Specification

The GraphQL service is mounted at `/graphql`.

### 1. GraphQL Playground / Explorer (`GET /graphql`)
Provides the interactive GraphiQL / Strawberry GraphQL web interface for inspecting the schema and executing interactive queries.

- **Method:** `GET`
- **Route:** `/graphql`
- **Headers:** `Authorization` (*Required*): Bearer token with `catalog:read` permission.
- **Response:** `200 OK` (HTML interface) or `401`/`403` if unauthorized.

---

### 2. GraphQL Query Execution (`POST /graphql`)
Executes GraphQL queries and mutations against the schema.

- **Method:** `POST`
- **Route:** `/graphql`
- **Headers:**
  - `Authorization` (*Required*, string): `Bearer <token>`
  - `Content-Type` (*Required*, string): `application/json`
  - `X-Tenant-ID` (*Optional*, string): Tenant identifier matching token claims.

#### Request Body Schema (`application/json`):
- `query` (*Required*, string): The GraphQL query document.
- `variables` (*Optional*, object): Key-value dictionary of variables referenced in the query.
- `operationName` (*Optional*, string): Name of the specific operation to execute if multiple are present in `query`.

#### Example Request: Query Products
```json
{
  "query": "query GetProducts { products { id sku name priceAmount } }"
}
```

#### Example Response: 200 OK
```json
{
  "data": {
    "products": [
      {
        "id": "prod_01h8x4k2",
        "sku": "WIDGET-001",
        "name": "Standard Widget",
        "priceAmount": "19.99"
      }
    ]
  }
}
```

#### Example Request: Query Single Product by ID
```json
{
  "query": "query GetProduct($id: String!) { product(id: $id) { id sku name priceAmount } }",
  "variables": {
    "id": "prod_01h8x4k2"
  }
}
```

#### Example Response: 200 OK
```json
{
  "data": {
    "product": {
      "id": "prod_01h8x4k2",
      "sku": "WIDGET-001",
      "name": "Standard Widget",
      "priceAmount": "19.99"
    }
  }
}
```

#### Status Codes:
- `200 OK`: Query executed successfully (GraphQL errors, if any, appear in the `errors` array of the response body).
- `401 Unauthorized`: Missing, invalid, or expired Bearer token.
- `403 Forbidden`: Authenticated user lacks `catalog:read` permission or tenant header mismatch.

## Practical Workflows

### Client-Side Catalog Query
1. Authenticate with `POST /auth/login` to obtain an access token.
2. Send a `POST /graphql` request with the `Authorization: Bearer <token>` header and a GraphQL query document requesting specific product fields.
3. Consume the returned JSON payload in client dashboards or frontend product catalogs.

## Schema Export & Utility

The application includes a schema export function `generate_graphql_schema(filepath: str = 'schema.graphql')` in `src/presentation/graphql.py`.

- The export utility strictly validates file paths using `os.path.basename` and allows only alphanumeric filenames with `.`, `-`, and `_` to prevent path traversal.
