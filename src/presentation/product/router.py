from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.product.repository import SQLAlchemyProductRepository
from src.application.product.services import ProductService
from src.infrastructure.auth.rbac import requires_roles
from src.domain.product.exceptions import ProductNotFoundError, InvalidSKUError, InvalidPriceError

router = APIRouter(prefix='/products', tags=['products'])

class ProductCreateDTO(BaseModel):
    sku: str
    name: str
    description: Optional[str] = None
    price_amount: str

class ProductResponseDTO(BaseModel):
    id: str
    sku: str
    name: str
    description: Optional[str]
    price_amount: str

def get_product_service(session: AsyncSession = Depends(get_db_session)) -> ProductService:
    repo = SQLAlchemyProductRepository(session)
    return ProductService(repo)

@router.post('', response_model=ProductResponseDTO, status_code=201)
@requires_roles(["ADMIN"])
async def create_product(data: ProductCreateDTO, request: Request, service: ProductService = Depends(get_product_service)):
    try:
        product = await service.create_product(
            sku=data.sku,
            name=data.name,
            description=data.description,
            price_amount=data.price_amount
        )
        return ProductResponseDTO(
            id=product.id,
            sku=product.sku.value,
            name=product.name,
            description=product.description,
            price_amount=str(product.price.amount)
        )
    except (InvalidSKUError, InvalidPriceError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get('/{product_id}', response_model=ProductResponseDTO)
async def get_product(product_id: str, service: ProductService = Depends(get_product_service)):
    try:
        product = await service.get_product(product_id)
        return ProductResponseDTO(
            id=product.id,
            sku=product.sku.value,
            name=product.name,
            description=product.description,
            price_amount=str(product.price.amount)
        )
    except ProductNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get('', response_model=List[ProductResponseDTO])
async def list_products(limit: int = 100, offset: int = 0, service: ProductService = Depends(get_product_service)):
    products = await service.list_products(limit, offset)
    return [
        ProductResponseDTO(
            id=p.id,
            sku=p.sku.value,
            name=p.name,
            description=p.description,
            price_amount=str(p.price.amount)
        ) for p in products
    ]

