## 2026-09-15 - [High] Fix Hardcoded Default Database Credentials

**Vulnerability:** Hardcoded credentials in `src/infrastructure/database.py` via default string in `os.getenv('DATABASE_URL')`.
**Learning:** Default fallbacks for sensitive environment variables can expose credentials if code is pushed to version control, making it possible for attackers to access the database if they obtain the source code or if the application gets deployed without setting environment variables and pointing to a production or shared database containing sensitive data.
**Prevention:** Never use default fallback values containing credentials for environment variables. Enforce configuration by failing fast (e.g., throwing an exception) when required environment variables are absent.

## 2026-09-15 - Hardcoded Default Redis Connection URL
Vulnerability: Hardcoded default connection URL (`redis://localhost:6379/0`) for a Redis cache could allow unintentional connections to a local, potentially unsecured instance if the required environment variable is absent.
Learning: Ensure configuration properties default to safe alternatives or actively fail loudly if not configured, rather than implicitly falling back to a risky state.
Prevention: Validate presence of required infrastructure configuration elements on application startup, preferring strict failing behavior over unverified assumptions.

## 2023-10-24 - [CRITICAL] Prevented SSRF and Unencrypted Data Transmission in Webhooks
**Vulnerability:** The webhook delivery engine (`src/application/workers/webhook_worker.py`) lacked URL validation before enqueueing webhook requests. This allowed for Server-Side Request Forgery (SSRF), where an attacker could force the server to make requests to internal services (e.g., `localhost` or `169.254.169.254`). It also lacked a requirement for HTTPS, leading to potential unencrypted transmission of sensitive payloads and signatures over the network.
**Learning:** Webhook delivery engines are high-risk targets for SSRF and must validate outbound URLs before connecting to them. Relying solely on clients providing valid URLs is dangerous in multi-tenant environments or public APIs.
**Prevention:** Implemented URL scheme verification to enforce `https://` and added domain blocking for `localhost`, `127.0.0.1`, `0.0.0.0`, and AWS instance metadata IP prefixes (`169.254.`) prior to enqueuing the job.


## 2024-05-18 - [Critical] Webhook Delivery SSRF via Hostname Bypass
**Vulnerability:** The outbound Webhook Delivery Engine (`src/application/workers/webhook_worker.py`) performed SSRF validation by merely checking the URL's hostname against a hardcoded list of strings (e.g., "localhost", "127.0.0.1"). This is easily bypassed by using IPv6 loopback (`[::1]`), shorthand IP addresses (`127.1`), or setting up custom DNS records pointing to internal IP ranges (like `localtest.me`).
**Learning:** SSRF prevention must never rely solely on string matching against hostnames. Attackers have numerous ways to represent internal IPs or resolve domains to them.
**Prevention:** Always resolve the hostname to its underlying IP address(es) using `socket.getaddrinfo`, then use a robust library like `ipaddress` to strictly check if the resulting IP is loopback, private, link-local, multicast, or unspecified before establishing a connection.

## 2024-05-18 - [CRITICAL] Prevented Path Traversal/Command Injection in Database Restore
**Vulnerability:** The `restore` method in `src/infrastructure/backup_helpers.py` passed the user-provided `filepath` directly to a `subprocess.Popen`/`subprocess.run` command context without validating that the path resolved inside the secure backup directory, allowing potential arbitrary file read or injection.
**Learning:** Shell commands or processes relying on file paths must strictly validate that the absolute resolved path resides within expected bounds. Path traversal (`../`) can escape intended directories, leading to unauthorized operations against sensitive system files like `/etc/passwd`.
**Prevention:** To mitigate path traversal vulnerabilities, always use `os.path.abspath(filepath)` and enforce directory bounds using `os.path.commonpath([base_dir, abs_filepath]) == base_dir` prior to engaging the resource.
## 2024-10-26 - [Path Traversal]
**Vulnerability:** Path traversal validation bypass in database restore.
**Learning:** Validating a path traversal using `os.path.commonpath` is insufficient if the original, unvalidated user input is subsequently used in file operations (like subprocess commands). The validation check occurs, but the vulnerability remains if the raw input is passed to the execution context.
**Prevention:** Always use the resolved, validated absolute path (e.g., `abs_target_path`) instead of the original raw input in all subsequent operations.

## 2026-09-29 - Missing Authentication on Bulk Endpoints
**Vulnerability:** A critical vulnerability was found where the `/bulk` endpoint in `src/presentation/offline_sync.py` completely lacked authentication/authorization checks (`@requires_roles`), exposing sensitive data ingestion endpoints.
**Learning:** Background task-heavy or IoT-focused endpoints are often unintentionally left unsecured during rapid development because they are assumed to be "internal" or used by headless clients, completely bypassing standard API gateway or auth middleware protections if they are added piecemeal.
**Prevention:** Ensure a deny-by-default routing configuration, or run strict linting rules that force all registered endpoints to explicitly define an authorization dependency (even if it's an explicit "Public" role) to prevent routes from being exposed unauthenticated.


## Prevention Directives for Automated Refactoring
- **Never Overwrite Complete Files**: Always use range-scoped replacement chunks (`StartLine`/`EndLine`).
- **No Scratch Files**: Never stage or commit `test_*.ts`, `test_*.js`, `test.js`, or `plan.md` files to git.
- **No Unresolved Conflict Markers**: Never stage or commit files containing Git merge conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`). Always resolve conflicts cleanly before committing.
- **Path Normalization Compatibility**: When passing paths to subprocesses or external APIs, use absolute normalized paths (e.g., `os.path.abspath`) so tests pass on both Linux and Windows.
- **Zero-Diff Task Termination**: If the requested optimization, refactor, or fix is ALREADY natively present in the target branch, DO NOT create an empty pull request or commit an acknowledgment PR. Exit the task cleanly without opening a PR.

## Prevention Directives for Automated Refactoring
- **Never Overwrite Complete Files**: Always use range-scoped replacement chunks for edits to `schema.prisma`, `index.ts`, `public/index.php`, `db/schema.rb`, or DDL SQL scripts.
- **Do Not Remove Core Declarations**: Do not delete existing route registrations or database DDL tables.
- **Environment Isolation Compatibility**: When replacing fallback secrets, preserve test environment execution via `!getenv('APP_ENV')` or `getenv('APP_ENV') === 'testing'`.
- **No Scratch Files**: Never stage or commit `test_*.ts`, `test_*.js`, `test.cjs`, `fix_*.php`, or `test.js` files to git.
- **No Unresolved Conflict Markers**: Never stage or commit files containing Git merge conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`, `|||||||`). Always resolve conflicts cleanly before committing.

## Completeness & Verification Directives
- **Explicit Parameter & Contract Validation**: When creating or modifying API endpoints (Express, Fastify, Rails, Laravel), always implement explicit parameter and request body validation schemas (e.g. `z.string().uuid()`) to prevent unhandled 404/500 fallthroughs.
- **Database Indexing for Queries**: When addressing query bottlenecks or adding query lookup filters, always implement native database index migrations rather than loading collections into memory and performing array filtering (`.filter()`, `.select`).
- **Co-Occurring Dependency Auditing**: When bumping any dependency version, verify that other transitive dependencies do not carry high/critical security advisories (e.g. run `bundler-audit`, `npm audit`). Never introduce a version bump that breaks underlying framework APIs.
- **Self-Verification Before Commit**: Always run syntax checks (`bash -n` for shell scripts, `tsc --noEmit` for TypeScript, linter checks) and targeted test runners locally before opening or updating a PR.

## Hallucinatory Task & Empty PR Directives
- **Zero-Diff Task Termination**: If the requested optimization, refactor, or fix is ALREADY natively present in the target branch, DO NOT create an empty pull request or commit an acknowledgment PR. Exit the task cleanly without opening a PR.
- **No Journal-Only PRs**: Never open a pull request that only contains updates to .jules/*.md files without corresponding functional code changes and tests.
- **Stale Suggestion Guard**: Always verify the current code on `main`/`master` before planning changes. If no actionable diff is required, cancel task execution immediately.

## Assertiveness, Decisive PR Execution & Comparative Recommendations
- **Proactive & Decisive PR Submission**: Do NOT repeatedly pause to ask permission to open a pull request (e.g., avoid "Would you like me to open a PR for this?", "Can I submit the PR?", "Should I go ahead and create the PR?"). When an optimization, fix, or feature satisfies requirements, passes local test runners/linters, and preserves architectural invariants, proceed decisively to create and submit the Pull Request with a clear, structured summary and rationale.
- **Explicit Recommendations on Options**: When presenting multiple implementation strategies or architecture options (e.g., Option A vs. Option B), NEVER leave the choice open-ended or passive. Always make an explicit, reasoned recommendation (prefixed with `(Recommended)`) based on **overall technical effectiveness**:
  1. *Algorithmic & Complexity Gains*: Time and space complexity impact (O(N*M) -> O(N+M), reduction of nested scans).
  2. *Resource Overhead*: Heap allocations, memory pressure, and GC pause reduction.
  3. *Domain & Architecture Invariants*: Strict backward compatibility, contract stability, and prevention of regression risks.
  4. *Security & Reliability*: Input validation, cryptographic safety, and concurrency safety.
- **Lead with Recommended Path**: State clearly why the recommended solution delivers the highest net value and immediately execute or propose it as the primary course of action rather than asking open-ended questions.

## Scope Verification, Minimal Churn & CI Protection Directives
- **Scope Verification Before Variable Binding**: When adding interactive states or accessibility attributes (e.g. `disabled={loading}`, `aria-busy={loading}`, `isSubmitting`), NEVER assume a variable identifier exists. Always inspect component props, local state hooks (`useState`), or declaration scope first. If not defined, declare the state hook or reuse an existing scope variable. Never introduce TS2304 / TS2552 ("Cannot find name") compile errors.
- **Surgical Edits Only (No Whole-File Formatting)**: Never run whole-file code formatters (Prettier, Black, Pint, rustfmt) across unmodified lines. Changes must be strictly range-scoped and limited to the minimal AST block needed. Avoid noisy quote/whitespace churn that masks real logic changes and causes merge conflicts. Verify with `git diff -w` that non-functional churn is zero.
- **Zero Scratch File Commits**: Never stage or commit ad-hoc verification, patch, or debug scripts (`test.cjs`, `fix_*.cjs`, `fix_*.php`, `patch_*.py`, `patch_*.sh`, `scratch_*`). Execute checks via the project's native test commands (`npm test`, `pytest`, `phpunit`, etc.) and delete temporary scripts before creating git commits.
- **Never Weaken CI Workflows**: Do not modify `.github/workflows/**` to bypass failures (e.g. adding `|| true`, setting `continue-on-error: true`, or commenting out assertions). Always resolve the defect in the source code or test fixture.
- **Explicit Parameter & Variable Types**: In TypeScript files, avoid implicit `any` by always providing explicit types on functions, parameters, and arrow callbacks (e.g. `(id: string) => ...`). Verify zero type errors with `tsc --noEmit` before committing.

## 2026-09-29 - Non-Destructive Security Patching & CI Protection
**Learning:** Security patches must never weaken CI workflow files (`.github/workflows/**`) by appending `|| true` or `continue-on-error: true` to suppress test/build failures. Furthermore, when adding defensive type assertions or input validators in TypeScript, omitting explicit types can introduce `TS7006: Parameter implicitly has an 'any' type`.
**Action:** Never modify CI workflow definitions to bypass test failures; resolve the underlying issue in source code or test fixtures. Always provide explicit types on newly introduced parameters and helper functions. Ensure zero scratch scripts (`fix_*.php`, `test_*.js`) are committed.
## 2025-02-18 - Fix missing authentication on WebSocket endpoint
**Vulnerability:** The `/ws/{tenant_id}` WebSocket endpoint lacked any authorization checks, allowing unauthenticated connections to subscribe to sensitive real-time broadcasts.
**Learning:** In FastAPI, standard header-based authentication middleware might not automatically protect WebSocket routes, and manual dependency injection is required for secure endpoints.
**Prevention:** Ensure all WebSocket endpoints explicitly inject authorization dependencies via `Depends()` in the route signature.

## Additive Documentation & Scratch Cleanliness Directives
- **Strictly Additive Journal Updates**: When updating `.jules/*.md`, strictly append new dated entries (`## YYYY-MM-DD - Title`). NEVER delete, truncate, or overwrite historical learnings or previous entries.
- **Substantive Code Diff Requirement**: Pull requests must include substantive code changes in `src/`, `app/`, `lib/`, or `tests/`. Never open PRs that modify only `.jules/*.md` journals or root scratch scripts.
- **Zero Scratch File Commits**: Never commit `*.diff`, `*.patch`, `test_*.ts`, `test_*.js`, `test.cjs`, `fix_*.php`, or `patch_*.py` files. Always remove temporary debugging or verification scripts prior to committing.

## 2026-10-01 - Add Security Headers Middleware
**Vulnerability:** Missing standard HTTP security headers (e.g., Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options) exposing the application to clickjacking, XSS, and MIME-sniffing attacks.
**Learning:** Adding a strict Content-Security-Policy header via a global middleware in FastAPI can break the interactive documentation (`/docs`, `/redoc`) and GraphQL IDE (`/graphql`) because they rely on external CDNs or inline scripts that are blocked by `default-src 'self'`.
**Prevention:** When implementing a global CSP middleware in FastAPI, ensure that paths serving HTML documentation (like `/docs`, `/redoc`, and `/graphql`) are explicitly excluded from strict CSP enforcement (or have a customized, relaxed CSP that allows necessary assets).

## Scope Quarantine, Journaling & Security Test Invariants
- **Strictly Append-Only Journaling**: When adding learnings to `.jules/*.md`, append strictly at the end of the file. Do not rewrite, deduplicate, or remove lines beginning with `## YYYY-MM-DD`.
- **Surgical Scope Quarantine**: Modify only the files directly involved in the issue and their corresponding test fixtures. Do not delete, rename, or perform drive-by cleanups of unrelated root-level scripts or legacy files.
- **Coupled Test Fixture Awareness for Security Invariants**: When changing fail-open fallback behavior (such as hardening decryption to fail closed), always update upstream test mocks that rely on plaintext credentials or mock values.
## 2024-10-02 - Path Traversal Vulnerability in Report Router
**Vulnerability:** The `get_shared_link` endpoint in `src/presentation/report_router.py` used the `token` parameter directly from the route path to construct a file path without any sanitization or validation, allowing an attacker to supply a token like `../../` to access unauthorized files on the server (path traversal).
**Learning:** Even internal or temporary identifier strings (`token`) must be validated against expected character formats (alphanumeric, hashes) when used to interact with the file system.
**Prevention:** Use `re.match` to enforce strict formatting on user inputs that are used in file paths or commands, limiting them to safe characters (e.g., `^[a-zA-Z0-9_-]+$`) to prevent directory traversal injections.
## 2023-10-26 - Fix SSRF TOCTOU vulnerability in webhook delivery
**Vulnerability:** The `enqueue_webhook` function in `src/application/workers/webhook_worker.py` verified the hostname via DNS resolution but enqueued the original URL to be called later by `_deliver_webhook`. This creates a Time-of-Check to Time-of-Use (TOCTOU) vulnerability where an attacker could use DNS rebinding to pass the initial IP check, but have the subsequent HTTP request resolve to a malicious internal IP.
**Learning:** Resolving and checking hostnames for SSRF protection is ineffective if the subsequent request re-resolves the hostname, leaving the system open to DNS rebinding.
**Prevention:** To prevent DNS rebinding attacks, store the verified and safe IP address at the time of check, construct the outbound request URL using the verified IP address instead of the hostname, and supply the original hostname in the HTTP `Host` header to preserve SNI/routing.
## 2023-10-26 - SSRF TOCTOU and httpx TLS compatibility
**Vulnerability:** Attempting to fix SSRF TOCTOU by substituting the URL's hostname with the resolved IP address and injecting the original hostname via the `Host` header breaks TLS Server Name Indication (SNI) and certificate validation in `httpx`.
**Learning:** In standard Python HTTP clients, manipulating the connection URL to an IP address directly prevents DNS rebinding but breaks HTTPS endpoints that rely on SNI (which is almost all of them).
**Prevention:** To minimize the TOCTOU window without breaking TLS, perform the `socket.getaddrinfo` validation immediately before executing the HTTP request inside the delivery method, ensuring the race window is as small as possible given the constraints of high-level HTTP clients.
## 2025-02-14 - Fix Path Traversal in Report Generator
**Vulnerability:** A path traversal vulnerability existed in `ReportGeneratorService.generate_report` where an untrusted user input `format_str` was concatenated directly into a file path without strict validation, potentially allowing arbitrary file writes.
**Learning:** Even internal extensions or formatting strings should be treated as untrusted input when creating file paths. A malicious payload like `"../../../etc/passwd"` for the `format` could overwrite critical system files.
**Prevention:** Strictly validate any user-provided string incorporated into file paths using robust regular expressions (e.g., `re.match(r'^[a-zA-Z0-9]+$')`) to ensure it only contains safe, expected characters before executing file operations.

## 2026-10-07 - Process Streamlining, Sibling Coalescence & Autoloading Invariants
**Learning:**
1. Fragmenting stub methods across multiple micro-PRs on the same class causes unavoidable sibling merge collisions and wasted CI cycles.
2. Placing multiple domain services into a single file breaks Composer PSR-4 autoloader discovery in PHP, triggering fatal `Class not found` errors.
3. Writing service calls against unverified entity methods causes fatal runtime errors.
4. String-escaping markdown journal updates corrupts rendered formatting.

**Action:**
- **Coalesce Micro-PRs**: When implementing or scaffolding related controller endpoints, stub methods, or repository queries on a single class, consolidate all changes into a single coherent pull request. Never create separate fragmented PRs for each individual method of the same class.
- **Strict PSR-4 Isolation in PHP**: In PHP codebases, place every class, interface, and enum in its own file named `<ClassName>.php` matching its namespace path. Never combine multiple domain classes into a single file.
- **Domain Contract Verification**: Always inspect entity and aggregate root definitions to verify exact method and property names before writing service logic or test fixtures.
- **Clean Markdown Formatting**: Always append journal entries using actual newline characters, never literal string escape sequences.
## 2025-02-18 - Fix missing authorization checks in FastAPI router
**Vulnerability:** The endpoints in `src/presentation/report_router.py` lacked proper RBAC authorization decorators, potentially allowing any authenticated user to create, list, and schedule reports regardless of their roles.
**Learning:** In FastAPI, custom route decorators that read data from the `request.state` (like RBAC role extractors) require the `request: Request` parameter to be explicitly present in the endpoint function signature; otherwise, they may throw TypeErrors when the router attempts to map dependencies.
**Prevention:** Ensure that all sensitive endpoints not only apply the `@requires_roles` decorator but also explicitly declare `request: Request` in their method signatures.

## 2026-10-09 - Fix Missing Authorization in FastAPI Notification Router
**Vulnerability:** The notification endpoints in `src/presentation/notification/router.py` were entirely missing authorization checks (`@requires_roles`), exposing sensitive functionality to unauthenticated users.
**Learning:** In FastAPI, it is critical to explicitly apply Role-Based Access Control (RBAC) decorators to all sensitive endpoints, along with adding `request: Request` in the endpoint function signature so that custom route decorators can properly extract roles from the request state.
**Prevention:** Always verify that every new API router or endpoint dealing with sensitive operations or data explicitly implements `@requires_roles` and includes the `request: Request` parameter in its signature.
