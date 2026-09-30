## 2025-02-18 - Fix missing authentication on WebSocket endpoint
**Vulnerability:** The `/ws/{tenant_id}` WebSocket endpoint lacked any authorization checks, allowing unauthenticated connections to subscribe to sensitive real-time broadcasts.
**Learning:** In FastAPI, standard header-based authentication middleware might not automatically protect WebSocket routes, and manual dependency injection is required for secure endpoints.
**Prevention:** Ensure all WebSocket endpoints explicitly inject authorization dependencies via `Depends()` in the route signature.
