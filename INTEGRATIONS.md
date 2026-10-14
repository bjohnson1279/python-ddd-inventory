# Python retail integrations

Python now has persisted tenant-owned connections, signed Shopify/WooCommerce
order ingestion, shipment/cancellation commands, and an independent acknowledged
HTTPS delivery queue for inventory and financial events. These replace the
tenantless no-op webhook path and fabricated Shopify connection list.

## Configure before using

Run `python -m scripts.migrate_identity_catalog` as the migration account. It adds
four integration tables and forced PostgreSQL tenant/parent policies. The ordinary
runtime login needs access to these tables because event producers check for
destinations within their existing transaction, even when none are registered.
Use a runtime role without superuser/BYPASSRLS and transaction-local tenant
context. No running store database was migrated or external subscription created
as part of this implementation.

The operator assigns environment variable **names** to tenants with
`INTEGRATION_TENANT_SETTINGS`, for example:

```json
{"store-a":["INTEGRATION_STORE_A_KEY","INTEGRATION_CONNECTOR_KEY","INTEGRATION_CONNECTOR_URL"]}
```

Set each referenced key to its actual signing secret (at least 32 bytes), and the
connector URL to an operator-reviewed HTTPS endpoint. Endpoint URLs cannot be
supplied through the API; they come from assigned deployment settings. Redirects
and proxy environment variables are not followed. The operator must vet endpoint
destinations and network access, including any DNS changes. This is a trusted
deployment configuration, not a general URL-fetch service.

Secrets and endpoint URLs are not returned by the API, recorded in command
requests, or copied into delivery payloads/errors. A connection records references
and a signing-key fingerprint. Connections require distinct signing secrets;
shared Shopify app secrets across tenants are deliberately unsupported because
the body signature alone does not bind editable routing headers to a store.
Provision separate store credentials. For inbound key rotation, pause the old
connection and register a replacement with a new assigned setting/key. Pending
outbound deliveries retain their destination/secret **references**; their operator
settings must remain usable until the queue drains.

Only ADMIN/MANAGER have integration read/manage permissions. Financial journal
subscriptions additionally require financial access. Commands retain actor,
reason, exact request, version and mutation ID in the existing action journal.
Foreign connection/order/delivery IDs cannot be read or operated on. PostgreSQL
policies also enforce ownership of connection, order and outbox-event parents.

## Signed store orders

Create an inbound connection:

```json
{
  "name":"Main shop",
  "provider":"SHOPIFY",
  "origin":"main-shop.myshopify.com",
  "locationId":"default",
  "secretRef":"INTEGRATION_STORE_A_KEY",
  "mutationId":"register-main-shop",
  "reason":"Register the owned store"
}
```

`POST /integrations/connections` returns the webhook path
`/webhooks/integrations/{tenant}/{connectionId}/orders`. Register its full public
HTTPS URL in the provider. The tenant/path are routing hints; authorization comes
from the matching active connection's signature. Payload tenant selectors cannot
override ownership. Set the exact canonical origin, including the WooCommerce
URL's trailing slash when its source header includes it.

Shopify uses `X-Shopify-Hmac-Sha256`, `X-Shopify-Webhook-Id`,
`X-Shopify-Shop-Domain` and topic `orders/create`. WooCommerce uses
`X-WC-Webhook-Signature`, `X-WC-Webhook-Delivery-ID`, `X-WC-Webhook-Source`
and topic `order.created`. Signatures are base64 HMAC-SHA256 over the unmodified
body, validated before parsing or mutations. These conventions follow the
[Shopify verification documentation](https://shopify.dev/docs/apps/build/webhooks/verify-deliveries)
and [WooCommerce webhook documentation](https://developer.woocommerce.com/docs/apis/rest-api/v2/webhooks).

WooCommerce's initial unsigned form ping `webhook_id=<number>` receives
`{"status":"PING_ONLY"}` for an active owned WooCommerce connection. It only
acknowledges reachability and cannot create orders or reserve stock; other
unsigned payloads fail. The ping behavior is defined in
[WooCommerce's webhook implementation](https://github.com/woocommerce/woocommerce/blob/trunk/plugins/woocommerce/includes/class-wc-webhook.php).

Orders require an external `id` and 1–100 `line_items` with explicit local `sku`
and positive integer `quantity`. Repeated SKUs are consolidated. Bodies are capped
at 256 KiB. Missing SKUs, unsupported serial/kit tracking, fractional quantities,
insufficient available stock and mismatched delivery IDs fail without partial
reservations. There is no implicit external-SKU mapping or silently skipped line.

An accepted order **reserves** stock; it does not claim payment or physical
shipment. Order records, reservations, financial-source records and outbox events
commit atomically. Concurrent copies reserve once. Both external-order identity
and every observed delivery-ID alias are persisted. A changed stock request using
either identity returns conflict. A replay returns the original acknowledgement,
including after fulfillment/cancellation; GET orders shows the current state.

`POST /integrations/orders/{id}/fulfill` confirms physical shipment and fulfills
the entire reservation through the existing costing/financial transaction.
`POST /integrations/orders/{id}/cancel` releases it without COGS. Both require
`expectedVersion`, `mutationId`, `reason`, and support actor-bound replay. Pausing
a connection stops new orders but allows existing reservations to be completed
or released. Automatic provider updates/cancellations, partial shipments, serial
selection, virtual bundles and UOM mapping remain follow-up work. Actual selling
price/payment remains the financial-document workflow; no payment is inferred.

## Acknowledged inventory/accounting exports

Register `provider: EVENT_SINK`, `secretRef`, `endpointRef`, and `topics` containing
`StockChanged`, `JournalEntryPosted`, or both. No shop origin is accepted. New
matching outbox events create destination deliveries in the producer's database
transaction. Core financial inserts explicitly use the same enqueue path.
Producer rollback also rolls back deliveries and sequence counters. Registration
does not backfill historical events or imply an initial external reconciliation.

The destination receives this inventory-owned contract:

```json
{"eventId":"durable-outbox-id","tenantId":"store-a","type":"JournalEntryPosted","data":{}}
```

`data` is the committed event snapshot, including exact integer cents for journal
lines. Stock events include quantity, allocated/available quantity and stock
version. A receiver should use stock identity/version to apply absolute available
levels and map journal accounts explicitly. Specialized transfer/serial/kit
events are not subscribed to by this two-topic connector yet.

Requests carry stable `Idempotency-Key`/`X-Inventory-Delivery-Id` headers and
`X-Inventory-Signature` (hex HMAC-SHA256 over exact request bytes). The receiver
must verify signatures and persist deduplication before returning:

```json
{"eventId":"durable-outbox-id","accepted":true,"externalId":"actual-remote-record"}
```

Only a 2xx response with this matching acknowledgement marks DELIVERED. A bare
success response, wrong event ID, missing external ID, timeout or failure stays
visible. Remote error bodies, URLs and secret values are never saved. A successful
acknowledgement means the connector accepted responsibility; it proves a final
ERP posting only if that connector waits for the ERP before acknowledging.

Delivery is at least once. Unknown outcomes repeat the same delivery ID and
payload; remote idempotency is required. Workers lease an ordered head per
destination and release database locks during the bounded HTTPS request. Lease
tokens fence stale acknowledgements. A pending/leased/failed head prevents later
deliveries overtaking it. Three attempts per cycle lead to FAILED, with bounded
backoff. Explicit versioned retry resets the cycle while retaining total attempts
and the original snapshot. A paused connection holds its queue.

Run `INTEGRATION_WORKER_TENANT=<tenant> python -m scripts.dispatch_integrations`
from an externally scheduled job using the restricted runtime login. Each pass
handles at most one head for each of at most 100 active destinations. The API/UI
also supports sending the next head manually. This destination queue operates
independently of Kafka acknowledgement, so an ERP outage does not mark a stock
event unpublished or require republishing it to Kafka.

## API and client

The authenticated routes also have `/api` aliases:

| Endpoint | Behavior |
|---|---|
| GET/POST /integrations/connections | List/register owned configuration |
| POST /integrations/connections/{id}/active | Pause/enable with current version |
| GET /integrations/orders | Paginated current order states |
| POST /integrations/orders/{id}/fulfill or /cancel | Versioned shipment/release |
| GET /integrations/deliveries | Paginated durable delivery/error history |
| POST /integrations/connections/{id}/dispatch | Send one ready ordered head |
| POST /integrations/deliveries/{id}/retry | Versioned failed-head recovery |

The Python integrations panel registers references, displays actual webhook
paths/current orders, confirms shipment, releases reservations, pauses connections
and manages destination recovery. The adapter paginates orders/deliveries and
surfaces errors. It no longer fabricates a mock Shopify store. Commands preserve
their mutation IDs across uncertain responses.

Legacy tenantless provider webhooks validate their old protocol but return 501
for order processing. Amazon setup/ingestion and logistics/ERP placeholder adapters
now explicitly reject unsupported work instead of reporting fabricated success.
Direct Shopify inventory mutation, Amazon SP-API, native QuickBooks/NetSuite/Xero
OAuth/account mapping/posting, fulfillment notifications and external stock
reconciliation are still separate adapter work. EVENT_SINK is a real signed
HTTP connector contract, not a claim that those native adapters are complete.

## Verification (2026-10-12)

The full regression run passed 518 tests with 11 optional integration skips.
Final scoped validation passed 40 tests, covering the completed order/receipt,
legacy-webhook and financial concurrency contracts. Eight additional selected
database cases passed across SQLite and restricted PostgreSQL, including repeatable
migration and outbox compatibility. Client adapter/panel checks passed 34 tests;
TypeScript, Python compilation and CI YAML checks passed.

The regression pack exercises signed body validation, provisioning pings,
duplicate and changed delivery identities, multi-line rollback, cancellation,
cash/accrual COGS without invented customer payment, simultaneous last-unit orders,
leases, acknowledgement validation, stable retries after an acknowledgement whose
database commit fails, version/actor checks, operator setting assignments and
forced tenant/parent policies. Remote HTTP responses were simulated; no live
Shopify/WooCommerce/ERP account was connected or mutated.
