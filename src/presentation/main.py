import asyncio
from fastapi import FastAPI, Request
from src.presentation.product.router import router as product_router
from src.presentation.cycle_count.router import router as cycle_count_router
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup/teardown events can go here. 
    # Skipping Base.metadata.create_all as migrations should handle it in production.
    yield

from src.infrastructure.messaging.kafka_publisher import KafkaPublisher
from src.application.workers.outbox_worker import OutboxWorker
from src.infrastructure.telemetry import setup_telemetry
from src.presentation.graphql import graphql_app

app = FastAPI(title='Python DDD Inventory API', lifespan=lifespan)

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



