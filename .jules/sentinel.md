## 2026-10-01 - Add Security Headers Middleware
**Vulnerability:** Missing standard HTTP security headers (e.g., Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options) exposing the application to clickjacking, XSS, and MIME-sniffing attacks.
**Learning:** Adding a strict Content-Security-Policy header via a global middleware in FastAPI can break the interactive documentation (`/docs`, `/redoc`) and GraphQL IDE (`/graphql`) because they rely on external CDNs or inline scripts that are blocked by `default-src 'self'`.
**Prevention:** When implementing a global CSP middleware in FastAPI, ensure that paths serving HTML documentation (like `/docs`, `/redoc`, and `/graphql`) are explicitly excluded from strict CSP enforcement (or have a customized, relaxed CSP that allows necessary assets).

## 2026-10-01 - Add Security Headers Middleware
**Vulnerability:** Missing standard HTTP security headers (e.g., Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options) exposing the application to clickjacking, XSS, and MIME-sniffing attacks.
**Learning:** Adding a strict Content-Security-Policy header via a global middleware in FastAPI can break the interactive documentation (`/docs`, `/redoc`) and GraphQL IDE (`/graphql`) because they rely on external CDNs or inline scripts that are blocked by `default-src 'self'`.
**Prevention:** When implementing a global CSP middleware in FastAPI, ensure that paths serving HTML documentation (like `/docs`, `/redoc`, and `/graphql`) are explicitly excluded from strict CSP enforcement (or have a customized, relaxed CSP that allows necessary assets).

## 2026-10-01 - Add Security Headers Middleware
**Vulnerability:** Missing standard HTTP security headers (e.g., Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options) exposing the application to clickjacking, XSS, and MIME-sniffing attacks.
**Learning:** Adding a strict Content-Security-Policy header via a global middleware in FastAPI can break the interactive documentation (`/docs`, `/redoc`) and GraphQL IDE (`/graphql`) because they rely on external CDNs or inline scripts that are blocked by `default-src 'self'`.
**Prevention:** When implementing a global CSP middleware in FastAPI, ensure that paths serving HTML documentation (like `/docs`, `/redoc`, and `/graphql`) are explicitly excluded from strict CSP enforcement (or have a customized, relaxed CSP that allows necessary assets).

## 2026-10-01 - Add Security Headers Middleware
**Vulnerability:** Missing standard HTTP security headers (e.g., Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options) exposing the application to clickjacking, XSS, and MIME-sniffing attacks.
**Learning:** Adding a strict Content-Security-Policy header via a global middleware in FastAPI can break the interactive documentation (`/docs`, `/redoc`) and GraphQL IDE (`/graphql`) because they rely on external CDNs or inline scripts that are blocked by `default-src 'self'`.
**Prevention:** When implementing a global CSP middleware in FastAPI, ensure that paths serving HTML documentation (like `/docs`, `/redoc`, and `/graphql`) are explicitly excluded from strict CSP enforcement (or have a customized, relaxed CSP that allows necessary assets).
