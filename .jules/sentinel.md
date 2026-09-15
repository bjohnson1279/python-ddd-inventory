## 2026-09-15 - [High] Fix Hardcoded Default Database Credentials

**Vulnerability:** Hardcoded credentials in `src/infrastructure/database.py` via default string in `os.getenv('DATABASE_URL')`.
**Learning:** Default fallbacks for sensitive environment variables can expose credentials if code is pushed to version control, making it possible for attackers to access the database if they obtain the source code or if the application gets deployed without setting environment variables and pointing to a production or shared database containing sensitive data.
**Prevention:** Never use default fallback values containing credentials for environment variables. Enforce configuration by failing fast (e.g., throwing an exception) when required environment variables are absent.
