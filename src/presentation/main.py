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
app.include_router(graphql_app, prefix="/graphql")

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



