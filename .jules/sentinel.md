## 2026-09-15 - [High] Fix Hardcoded Default Database Credentials

**Vulnerability:** Hardcoded credentials in `src/infrastructure/database.py` via default string in `os.getenv('DATABASE_URL')`.
**Learning:** Default fallbacks for sensitive environment variables can expose credentials if code is pushed to version control, making it possible for attackers to access the database if they obtain the source code or if the application gets deployed without setting environment variables and pointing to a production or shared database containing sensitive data.
**Prevention:** Never use default fallback values containing credentials for environment variables. Enforce configuration by failing fast (e.g., throwing an exception) when required environment variables are absent.

## 2026-09-15 - Hardcoded Default Redis Connection URL
Vulnerability: Hardcoded default connection URL (`redis://localhost:6379/0`) for a Redis cache could allow unintentional connections to a local, potentially unsecured instance if the required environment variable is absent.
Learning: Ensure configuration properties default to safe alternatives or actively fail loudly if not configured, rather than implicitly falling back to a risky state.
Prevention: Validate presence of required infrastructure configuration elements on application startup, preferring strict failing behavior over unverified assumptions.

