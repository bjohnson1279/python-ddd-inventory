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

## 2024-05-24 - [HIGH] Prevent Path Traversal in Database Backup Restore
**Vulnerability:** The `restore(self, filepath: str)` method in `DatabaseBackupHelper` passed the `filepath` argument directly to `gunzip` and `psql` without enforcing that the file resides within the allowed `backup_dir`. An attacker controlling this input could provide paths like `../../../etc/passwd.sql` to read unauthorized files (if uncompressed logic executes) or write over other files if logic permitted, or generally escape the backup directory.
**Learning:** File paths passed as arguments to shell-like utilities (even when using parameter lists rather than `shell=True`) can still lead to path traversal if they reference files outside intended directories.
**Prevention:** To prevent path traversal, validate user-provided file paths against an allowed base directory by resolving both paths via `os.path.abspath` and verifying the base directory remains the common path using `os.path.commonpath`.
