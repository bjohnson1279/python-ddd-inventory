# Python financial accounting

Python now maintains a durable, balanced inventory financial ledger, manual
journals, commercial documents, settlements, credits and refunds. Posted entries
are immutable. Inventory postings share the stock command's database transaction;
there is no asynchronous gap between a committed stock change and its journal.
Acknowledged outbox publication remains asynchronous as described in
[event delivery](EVENT_DELIVERY.md).

## Migration and opening balances

Run `python -m scripts.migrate_identity_catalog` with the migration account before
deploying this code. It adds financial tables, forced PostgreSQL tenant/parent
policies, immutable journal/line guards and balance checks. The ordinary API
database login needs access to the new tables even before accounting activation:
stock movements capture financial source records while the ledger is inactive.
Use transaction-local tenant context and a runtime login with neither superuser
nor BYPASSRLS. No running store database was migrated or service deployed here.

Accounting is initially inactive for each tenant. `GET /accounting/financial/status`
reports its actual state. `GET /accounting/financial/opening-preview` reads current
on-hand and in-transit carrying values. Activation requires consistent, priced
inventory, including serial and ordinary goods in transit. Price/reconcile missing
costs first; the application does not invent acquisition values.

`POST /accounting/financial/activate` requires `expectedVersion`, `mutationId` and
`reason`. It serializes with stock changes, captures opening Inventory and Transit
against Opening Equity, and activates automatic posting. Zero inventory starts
an empty active ledger without a fabricated zero-value journal. Repeated identical
commands replay their saved response. Historical movements are not reposted;
this avoids recognizing old receipts/COGS again over the opening carrying balance.

Existing cash, receivables, payables, equipment, loans and equity require reviewed
manual opening entries. Historical commercial-document imports are not implemented.
The active method is pinned to the tenant's existing CASH/ACCRUAL setting; changing
it afterwards requires an explicit accounting migration. This implementation uses
USD and exact integer cents, limited to JavaScript's safe integer range.

## Automatic inventory entries

| Physical event | Financial effect |
|---|---|
| Priced receipt | Debit Inventory; credit Goods Received Not Invoiced |
| Dispatch, allocation fulfillment, serial or virtual-bundle sale | Debit COGS; credit Inventory at actual consumed cost |
| Customer return | Debit restored Inventory and any immediate Scrap Loss; credit original COGS |
| Write-off/scrap/damage loss | Debit Inventory Loss; credit Inventory |
| Count/correction gain or valuation pricing | Debit Inventory; credit Inventory Variance |
| Count/correction shortage | Debit Inventory Variance; credit Inventory |
| Transfer dispatch | Debit Transit; credit Inventory |
| Transfer arrival/cancellation | Debit Inventory; credit Transit |
| Kit assembly/disassembly | Move value through Kit Work In Progress; its balance returns to zero in the same transaction |
| Supplier return | Record expected Supplier Credits Receivable, reduce actual Inventory, and record any carrying-value variance |
| Reservations and quarantine release | No financial-value entry |

These rules use the persisted movement/cost-layer facts, including exact-value
layers and serial-specific costs. They never use a product's list price to infer
revenue, or assume that goods received were paid for. A journal/outbox failure
rolls back stock, cost layers, source records, journal numbers and entries together.
Posted journals contain actor, reference, source, effective UTC date and original
line snapshots. Numbers allocate transactionally without burning a number on rollback.

Unknown costs or unsupported movement effects create UNPRICED financial sources,
not invented amounts or successful zero-value entries. `GET /accounting/sources`
supports status/limit/offset, and `/{id}` returns an owned source. Financial reports
distinguish a mathematically balanced ledger from a complete ledger; unresolved
sources remain visible and prevent closing their accounting period.

`POST /accounting/sources/{id}/resolve` requires its current `expectedVersion`,
`mutationId`, `reason`, `referenceId`, `date` and balanced `lines`. Accounting staff
must use actual evidence to record the missing effect. Receipt resolution must
establish the goods-received liability. Pricing remaining unpriced stock separately
posts a valuation adjustment; resolution must account for that existing posting,
not recognize the inventory value twice. The reconciliation endpoint exposes
differences between book Inventory/Transit and actual carrying values.

## Commercial amounts and actual money movements

`POST /accounting/documents` records SALE or PURCHASE documents with `sourceIds`,
actual `amountCents`, `reference`, `counterparty`, `date`, `mutationId` and `reason`.
Purchase documents also require an active owned `supplierId`. Each source can
belong to one commercial document. Purchase amounts must match the recorded
receipt liability; PO-backed sources must belong to the selected supplier.
Sales amounts are explicit actual sale values, independent of their COGS.

| Method | Document recording | Confirmed settlement |
|---|---|---|
| ACCRUAL sale | Debit Receivables; credit Sales Revenue | Debit Cash; credit Receivables |
| CASH sale | Persist the document without recognizing unpaid revenue | Debit Cash; credit Sales Revenue |
| ACCRUAL purchase | Debit Goods Received Not Invoiced; credit Payables | Debit Payables; credit Cash |
| CASH purchase | Persist the document; receipt clearing remains until payment | Debit Goods Received Not Invoiced; credit Cash |

`POST /accounting/documents/{id}/settle` requires `expectedVersion`, actual
`amountCents`, unique payment `reference`, `date`, `mutationId` and `reason`.
Partial payments update outstanding balances; overpayment and stale versions
fail atomically. A lost response can replay the same command without moving
cash twice. This records a confirmed payment; it does not initiate a bank transfer.

`POST /accounting/documents/{id}/credit` adds a confirmed credit tied to an owned
customer/supplier return `sourceId` from that original document. It requires the
same version/date/amount/reference/retry/reason fields. Credits cannot exceed the
original document, and supplier credits cannot exceed their documented return
receivable. Serial returns link to their identified original sale layer. Credit
amounts are explicitly provided; acquisition costs are not retail refund prices.

Accrual customer credits debit Sales Returns and credit Receivables. Cash customer
credits update the document until an actual refund occurs. Supplier credits clear
their receivable against Payables or receipt clearing. Multiple approved credits
can use the same return source within its remaining limit.

`POST /accounting/documents/{id}/refund` records actual customer refund payments
or actual vendor refunds received. Approved credit determines the refund due;
partial refunds are supported and over-refunds fail. Document responses expose
gross, credited, refunded, net, outstanding and refund-due amounts separately.
Credit approval does not silently move cash. Unpaid credited documents reduce
their outstanding balance without inventing a cash refund.

Inventory and COGS follow physical cost movements in both modes. CASH controls
commercial revenue/settlement timing while retaining inventory capitalization;
these operational statements are not a jurisdiction-specific cash-basis tax engine.
Taxes, invoice price/fee variances, multi-currency, bank feeds, payment execution,
payroll calculations and fiscal invoice document formats remain additional work.
Reviewed manual journals can record other operating expenses and opening balances.

## Journals, reports and closing

Every endpoint also has an `/api` alias. Admins, managers and accountants can read
and manage financial records; other roles, including warehouse operators and
suppliers, cannot read or write the financial ledger. Authorized physical stock
commands still create their cost postings automatically. Ownership is rechecked
before command replay; changed actors/payloads cannot reuse a command ID.

| Endpoint | Purpose |
|---|---|
| GET /accounting/chart-of-accounts | Fixed retail chart including cash, inventory, liabilities, equity, revenue and operating expenses |
| GET /accounting/ledger or /journals | Owned posted entries with bounded limit/offset |
| GET /accounting/journals/{id} | Inspect a posted entry and immutable lines |
| POST /accounting/journals | Actual balanced manual entry; date, description, active method, lines, reason and mutationId |
| POST /accounting/journals/{id}/reverse | Separate reversal of a manual entry, with date/reason/mutationId |
| GET /accounting/trial-balance | Exact account balances, balanced/complete flags and unresolved source count |
| GET /accounting/general-ledger | Account activity, opening/running/closing balances and pagination |
| GET /accounting/income-statement | Revenue, expenses and profit for a bounded date range |
| GET /accounting/balance-sheet | Assets, liabilities, equity and current earnings as of a date |
| GET /accounting/financial/reconciliation | Book Inventory/Transit/WIP versus current physical carrying values |
| POST /accounting/financial/close-period | Lock a completed UTC day using closedThrough, current version, reason and mutationId |

Journals require 2–100 positive, known-account lines whose integer debit/credit
totals match. Floats, booleans, zero/negative amounts, unbalanced entries and unknown
accounts are rejected. SQLite/PostgreSQL guards also protect stored header/line
consistency and prohibit updates/deletes. Manual reversals retain the original;
inventory/document/settlement corrections use their business workflows instead
of reversing an asset entry without its corresponding physical/document effect.

Posting dates cannot precede activation, their linked stock/document, or an
applicable closed period, and cannot be in the future. Automatic inventory posts
use the database's current UTC day rather than a caller's backdated receipt field.
Closing is monotonic and only covers completed days. A late source resolution
does not make an earlier unresolved period appear complete. Period closing locks
posting dates; annual earnings rollover and statutory reporting are not automated.

The shared Python client now posts real manual journals, preserving the exact
command across uncertain responses, and paginates actual ledger entries. Its
general-ledger panel provides opening preview/activation, trial balance,
reconciliation and closing controls. Accountant ledger loading is independent of
warehouse/catalog permissions. Date-only journal values retain their UTC fiscal
day in the browser. Commercial documents/credits/refunds/source-resolution methods
are available in the adapter; dedicated billing/review screens and ERP consumers
remain follow-up work.

## Verification (2026-10-12)

The full Python suite passed with 506 tests and 10 optional integration skips.
The financial API and concurrent SQLite/PostgreSQL contracts passed all 21
focused cases. The broader database contracts passed 14 of 15 initially; a
Windows connection timeout during tenant setup affected the PostgreSQL outbox
case, which passed on an isolated rerun. Client adapter/panel tests passed all
28 cases, and TypeScript compilation and Python compilation passed.

Coverage includes all four costing methods, stock/journal rollback, immutable
entries, opening balances, actual partial payments, customer/supplier credits
and refunds in both methods, source resolution, period restrictions, exact cents,
actor-bound retries, journal numbering, concurrency, tenant isolation and
restricted PostgreSQL runtime roles. Tests used disposable databases; no running
store database was migrated.
