## 2024-05-24 - Missing RBAC in Report Router
**Vulnerability:** The report_router.py endpoints (create, execute, schedule) were completely missing authorization checks, exposing sensitive data and functionality to unauthenticated users.
**Learning:** In FastAPI, it's easy to create a new router and forget to apply standard security decorators.
**Prevention:** Always ensure all sensitive endpoints use the `@requires_roles` decorator.
