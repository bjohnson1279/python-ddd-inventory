# Python event delivery

Stock commands commit their business changes and outbox events together. A real
Kafka worker now publishes those events, waits for broker acknowledgment, and
retains the delivered rows. Publication is asynchronous: a successful stock
command means durable enqueue, not completion by every downstream consumer.

## Running delivery

Install `requirements.txt`, then run `python -m scripts.migrate_identity_catalog`
with the migration account before starting the new worker. No running store was
migrated or deployed during this implementation.

For production, run a supervised dedicated worker alongside the API:

```powershell
# Set DATABASE_URL and KAFKA_BOOTSTRAP_SERVERS for the intended environment.
python -m scripts.run_outbox_worker
```

The dedicated process does not need JWT or Redis configuration. Give its database
login SELECT access to the tenant registry and SELECT/UPDATE access to the outbox
with the installed tenant policies. It iterates registered tenants using
transaction-local tenant context; it does not require BYPASSRLS. Enqueueing API
logins also need access to the new `python_outbox_streams` table.

Alternatively, `OUTBOX_ENABLED=true` starts an embedded worker in the API
lifespan. This defaults to false. An enabled embedded worker requires working
Kafka configuration and a migrated database at startup; startup fails explicitly
if those prerequisites are missing. A dedicated worker keeps broker startup
failures independent of API availability. `/health` reports the embedded worker
and its last polling error only; supervise the dedicated process separately and
monitor pending/dead-letter counts.

`KAFKA_BOOTSTRAP_SERVERS` is required. Configure `KAFKA_SECURITY_PROTOCOL` as
PLAINTEXT, SSL, SASL_PLAINTEXT or SASL_SSL. TLS verifies certificates using system
trust or `KAFKA_SSL_CA_FILE`; optional client authentication uses
`KAFKA_SSL_CERT_FILE`, `KAFKA_SSL_KEY_FILE` and `KAFKA_SSL_KEY_PASSWORD`. SASL uses
`KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD` and `KAFKA_SASL_MECHANISM` (PLAIN,
SCRAM-SHA-256 or SCRAM-SHA-512). `KAFKA_CLIENT_ID` identifies the producer.
Provision topic permissions and broker durability settings for the environment.

| Worker setting | Default | Meaning |
|---|---|---|
| OUTBOX_POLL_SECONDS | 2 | Pause between polls |
| OUTBOX_BATCH_SIZE | 50 | Maximum claims per tenant per poll |
| OUTBOX_LEASE_SECONDS | 60 | Claim lifetime, more than publish timeout plus five seconds |
| OUTBOX_PUBLISH_TIMEOUT_SECONDS | 30 | Bounded broker operation |
| OUTBOX_MAX_ATTEMPTS | 5 | Claims allowed before dead-lettering |
| OUTBOX_RETRY_BASE_SECONDS | 2 | Initial exponential retry delay |
| OUTBOX_RETRY_CAP_SECONDS | 300 | Maximum retry delay |
| OUTBOX_TOPIC_PREFIX | inventory | Topic namespace |
| OUTBOX_TENANT_IDS | unset | Optional comma-separated registered tenant allowlist |

## Delivery contract and recovery

Events move through PENDING, INFLIGHT, RETRY, PUBLISHED or DEAD_LETTER. Workers
claim one event in a short transaction and release database locks before broker
I/O. PostgreSQL row locks and SQLite write serialization prevent concurrent
claims; lease tokens fence stale database updates. Lease and retry timing use
the database clock. Expired claims recover after process termination, including
termination between acknowledgment and the PUBLISHED commit.

Each tenant/aggregate stream receives a database-assigned sequence in the same
transaction as its business change. Rolled-back commands consume no sequence.
Only the earliest outstanding event in a stream is eligible; its retry or
dead-letter state blocks its successors. Other streams continue. This avoids
ordering by API process clocks or random UUIDs. Changing a topic's partition
count or allowing independent producers for the same stream requires a separate
consumer ordering strategy.

The topic is `<prefix>.<aggregateType>.events`. The UTF-8 Kafka key is a compact
JSON array `[tenantId, aggregateType, aggregateId]`. Headers are `event-id`,
`tenant-id` and `event-type`. The JSON value contains:

```json
{
  "schemaVersion": 1,
  "eventId": "stable-outbox-id",
  "tenantId": "retailer",
  "sequence": 1,
  "aggregateType": "Stock",
  "aggregateId": "stock-id",
  "eventType": "StockReceived",
  "occurredAt": "2026-10-12T00:00:00+00:00",
  "payload": {"tenantId": "retailer"}
}
```

Delivery is **at least once**. The producer uses `enable_idempotence=True` and
`acks='all'` ([aiokafka producer documentation](https://aiokafka.readthedocs.io/en/latest/producer.html)),
but the database and broker do not share a transaction. A crash or unknown broker
outcome can redeliver an identical event ID and sequence. Consumers must record
event IDs and apply their business effects atomically, ignoring duplicate IDs.
Use sequence to detect stale or missing stream events where ordering matters;
a delayed duplicate must not repeat a notification or inventory/accounting effect.
PUBLISHED means broker acknowledgment, not consumer completion.

Malformed events fail permanently. Broker failures/timeouts retry with bounded
backoff; exhausted attempts enter DEAD_LETTER. Stored/logged errors contain
exception class names, not raw exception text, credentials or event payloads.
Optional post-publication hooks cannot undo acknowledgment or force redelivery;
Redis cache failure is independent of delivery. This change does not implement
downstream notification, reporting, accounting or webhook-ingestion consumers.

## Operator API and migration

Admins and managers can use the following tenant-scoped endpoints, also mounted
under `/api`. Foreign event IDs return 404; other roles cannot read or retry.

| Endpoint | Purpose |
|---|---|
| GET /outbox/stats | Real pending, published, failed, retrying and inFlight counts |
| GET /outbox/dead-letter | Failed events; limit 1–1000 and offset |
| GET /outbox/{id} | Owned event status, attempts, version, sequence and payload |
| POST /outbox/{id}/retry | Requeue a current dead letter with expectedVersion, mutationId and optional reason |

Retry commands are actor-bound and replay-safe, including a lost response followed
by delivery. Changed payloads or stale versions return 409. Requeue resets the
current attempt budget and preserves total attempts and an audit record. It
cannot republish a PUBLISHED event. The shared React monitor now displays actual
failures, surfaces loading errors and preserves retry IDs across uncertain responses.

The repeatable migration adds delivery state, leases, counters, stream sequences
and forced PostgreSQL tenant/parent policies. Owned legacy events receive sequence
in historical timestamp/ID order; that cannot reconstruct missing historical
commit order. Missing, malformed or unregistered tenant ownership is quarantined,
never assigned to a guessed tenant. Invalid legacy timestamps are also quarantined.
Repair malformed legacy metadata through an explicit operator migration before
retrying; the retry API does not edit payloads. Unassigned events are deliberately
invisible to tenant APIs and require migration-account inspection.

Delivered rows are retained, and dead letters block their stream. Automatic
purging, retention policies and consumer deduplication storage remain deployment
and downstream implementation work.

## Verification

Tests cover transactional sequence rollback, clock differences, concurrent claims,
lease expiry/fencing, cancellation, publish timeout, acknowledgment followed by
database failure, stable redelivery, retry budgets, poison events, poll recovery,
startup/shutdown, role/tenant boundaries and replay-safe operator retries.

Disposable PostgreSQL tests verify migration, forced RLS with a non-superuser
runtime role, concurrent workers and stock commits during broker waits. A real
disposable Kafka test publishes and consumes two ordered events with their keys,
headers, IDs and sequences, and checks retained PUBLISHED rows. Both contracts
are wired into backend CI. Client adapter tests and TypeScript checks also run
locally. These checks use synthetic test data and do not deploy services.

Local verification on 2026-10-12:

- Final delivery selection: 30 passed, including live PostgreSQL and Kafka.
- Broader PostgreSQL/Kafka selection: 12 passed across costing, security,
  procurement, returns, transfers/UOM, delivery and migration contracts.
- Shared Python client adapter: 20 passed; TypeScript `--noEmit` passed.
- Python compilation, CI YAML parsing and Git whitespace checks passed.
