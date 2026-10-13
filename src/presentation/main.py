from fastapi import FastAPI, Request, Depends
from src.presentation.product.router import router as product_router
from src.presentation.cycle_count.router import router as cycle_count_router
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.infrastructure.auth.identity import jwt_secret
    jwt_secret()
    from src.infrastructure.messaging.settings import outbox_enabled
    app.state.outbox_enabled = outbox_enabled()
    app.state.outbox_worker = None
    if not app.state.outbox_enabled:
        yield
        return
    publisher, worker = build_event_delivery()
    try:
        await publisher.start()
        await worker.start()
        app.state.outbox_worker = worker
        yield
    finally:
        try:
            await worker.stop()
        finally:
            await publisher.stop()
            app.state.outbox_worker = None

from src.infrastructure.messaging.kafka_publisher import KafkaPublisher
from src.application.workers.outbox_worker import OutboxWorker
from src.infrastructure.messaging.settings import OutboxSettings


def build_event_delivery():
    settings = OutboxSettings.from_env()
    publisher = KafkaPublisher.from_env(timeout=settings.publish_timeout)
    return publisher, OutboxWorker(publisher, settings=settings)
from src.infrastructure.telemetry import setup_telemetry
from src.presentation.graphql import graphql_app

from src.infrastructure.auth.security import authorize_request
app = FastAPI(title='Python DDD Inventory API', lifespan=lifespan, dependencies=[Depends(authorize_request)])
import os
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        'CORS_ORIGINS', 'http://localhost:3000,http://localhost:5173').split(',') if origin.strip()],
    allow_methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'],
    allow_headers=['Authorization', 'Content-Type', 'X-Tenant-ID'])

from src.presentation.auth_router import router as auth_router
from src.infrastructure.auth.identity import decode_token
from fastapi.responses import JSONResponse
import jwt

app.include_router(auth_router)
app.include_router(auth_router, prefix='/api')
from src.presentation.users_router import router as users_router
app.include_router(users_router)
app.include_router(users_router, prefix='/api')
from src.presentation.workforce_router import router as workforce_router
app.include_router(workforce_router)
app.include_router(workforce_router, prefix='/api')
from src.presentation.stock_router import router as stock_router
app.include_router(stock_router)
app.include_router(stock_router, prefix='/api')
from src.presentation.procurement_router import router as procurement_router
app.include_router(procurement_router)
app.include_router(procurement_router, prefix='/api')
from src.presentation.return_router import router as return_router
app.include_router(return_router)
app.include_router(return_router, prefix='/api')
from src.presentation.transfer_router import router as transfer_router
app.include_router(transfer_router)
app.include_router(transfer_router, prefix='/api')
from src.presentation.outbox_router import router as outbox_router
app.include_router(outbox_router)
app.include_router(outbox_router, prefix='/api')
from src.presentation.serial_kit_router import router as serial_kit_router
app.include_router(serial_kit_router)
app.include_router(serial_kit_router, prefix='/api')
from src.presentation.financial_router import router as financial_router
app.include_router(financial_router)
app.include_router(financial_router, prefix='/api')
from src.presentation.integration_router import router as integration_router, webhooks as integration_webhooks
app.include_router(integration_router)
app.include_router(integration_router, prefix='/api')
app.include_router(integration_webhooks)
from src.presentation.catalog_router import router as catalog_router
app.include_router(catalog_router)
app.include_router(catalog_router, prefix='/api')
from src.presentation.accounting_router import router as accounting_router
app.include_router(accounting_router)
app.include_router(accounting_router, prefix='/api')

@app.middleware('http')
async def authenticate(request: Request, call_next):
    authorization = request.headers.get('Authorization')
    if authorization:
        scheme, _, token = authorization.partition(' ')
        try:
            if scheme.lower() != 'bearer' or not token:
                raise jwt.InvalidTokenError()
            claims = decode_token(token)
            if not isinstance(claims['tenantId'], str) or not claims['tenantId']:
                raise jwt.InvalidTokenError()
            if not isinstance(claims['roles'], list):
                raise jwt.InvalidTokenError()
            if not isinstance(claims['sub'], str) or not isinstance(claims['jti'], str) or type(claims['ver']) is not int:
                raise jwt.InvalidTokenError()
        except jwt.InvalidTokenError:
            return JSONResponse({'detail': 'Invalid or expired token'}, status_code=401)
        request.state.tenant_id = claims['tenantId']
        request.state.claims = claims
        request.state.user_id = claims['sub']
        request.state.roles = claims['roles']
        request.state.permissions = claims.get('permissions', [])
    for supplied_tenant in request.headers.getlist('X-Tenant-ID') + request.query_params.getlist('tenantId') + request.query_params.getlist('tenant_id'):
        if supplied_tenant != getattr(request.state, 'tenant_id', None):
            return JSONResponse({'detail': 'Tenant ID mismatch'}, status_code=403)
    return await call_next(request)

# Setup Distributed Tracing & Observability
setup_telemetry(app)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

    # Do not apply strict CSP to FastAPI docs and GraphQL UI
    if not request.url.path.startswith(("/docs", "/redoc", "/graphql")):
        response.headers["Content-Security-Policy"] = "default-src 'self'"

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers['Cache-Control'] = 'no-store'
    return response



app.include_router(product_router)
app.include_router(cycle_count_router)
from src.presentation.report_router import router as report_router
app.include_router(report_router)
app.include_router(report_router, prefix='/api')
from src.presentation.alert_router import router as alert_router
app.include_router(alert_router)
app.include_router(alert_router, prefix='/api')
from src.presentation.supplier_portal.router import router as supplier_portal_router
app.include_router(supplier_portal_router)
app.include_router(graphql_app, prefix="/graphql")

from src.presentation.webhooks.shopify_router import router as shopify_webhook_router
from src.presentation.webhooks.woocommerce_router import router as woocommerce_webhook_router
from src.presentation.webhooks.amazon_router import router as amazon_webhook_router

app.include_router(shopify_webhook_router)
app.include_router(woocommerce_webhook_router)
app.include_router(amazon_webhook_router)

from src.presentation.notification.router import router as notification_router
app.include_router(notification_router)
app.include_router(notification_router, prefix='/api')

from src.presentation.aging.router import router as aging_router
app.include_router(aging_router)
app.include_router(aging_router, prefix='/api')

@app.get('/health')
async def health_check():
    enabled = getattr(app.state, 'outbox_enabled', False)
    worker = getattr(app.state, 'outbox_worker', None)
    running = bool(worker and worker.running)
    error = worker.last_poll_error if worker else None
    result = {'status': 'healthy' if not enabled or (running and not error) else 'degraded',
              'eventDelivery': {'enabled': enabled, 'running': running, 'pollingError': error}}
    return JSONResponse(result, status_code=200 if result['status'] == 'healthy' else 503)



