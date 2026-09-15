from fastapi import FastAPI
from src.presentation.product.router import router as product_router
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup/teardown events can go here. 
    # Skipping Base.metadata.create_all as migrations should handle it in production.
    yield

app = FastAPI(title='Python DDD Inventory API', lifespan=lifespan)

app.include_router(product_router)

@app.get('/health')
async def health_check():
    return {'status': 'healthy'}

