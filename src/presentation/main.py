import asyncio
from fastapi import FastAPI, Request, Depends
from src.presentation.product.router import router as product_router
from src.presentation.cycle_count.router import router as cycle_count_router
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.infrastructure.auth.identity import jwt_secret
    jwt_secret()
    # Setup/teardown events can go here. 
    # Skipping Base.metadata.create_all as migrations should handle it in production.
    yield

from src.infrastructure.messaging.kafka_publisher import KafkaPublisher
from src.application.workers.outbox_worker import OutboxWorker
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

from src.presentation.aging.router import router as aging_router
app.include_router(aging_router)

publisher = KafkaPublisher()
worker = OutboxWorker(publisher)

@app.on_event('startup')
async def startup_event():
    await publisher.start()
    asyncio.create_task(worker.start())

@app.on_event('shutdown')
async def shutdown_event():
    await worker.stop()
    await publisher.stop()


@app.get('/health')
async def health_check():
    return {'status': 'healthy'}



