import uuid
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, Field, AwareDatetime
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.auth.rbac import requires_roles
from src.infrastructure.auth.security import require_purchase_cost_permission, stock_response
from src.infrastructure.catalog_models import CatalogModel, VariantLinkModel, BarcodeModel
from src.infrastructure.product.models import ProductModel
from src.domain.product.value_objects import SKU
from src.domain.product.exceptions import InvalidSKUError
from src.application.stock_service import StockService, StockConflict
from src.presentation.base_unit_contract import BaseUnitDTO

router = APIRouter(tags=['retail catalog'])


class CatalogDTO(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200)


class AttributeDTO(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)


class VariantDTO(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    trackingMode: Literal['quantity'] = 'quantity'
    attributes: list[AttributeDTO] = Field(default_factory=list, max_length=50)


class BarcodeDTO(BaseModel):
    sku: str
    barcodeValue: str = Field(min_length=1, max_length=200)
    symbology: str = Field(default='Code128', min_length=1, max_length=50)
    source: str = Field(default='manual', min_length=1, max_length=50)
    makePrimary: bool = False


def barcode_dto(row):
    return {'id': row.id, 'sku': row.sku, 'variantId': row.sku,
            'barcodeValue': row.value, 'symbology': row.symbology,
            'source': row.source, 'isPrimary': row.is_primary,
            'assignedAt': row.assigned_at.isoformat()}


@router.get('/inventory/products')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def catalog(request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    parents = await session.scalars(select(CatalogModel).where(CatalogModel.tenant_id == tenant))
    result = {p.id: {'id': p.id, 'name': p.name, 'variants': []} for p in parents}
    links = {link.sku: link for link in await session.scalars(select(VariantLinkModel).where(
        VariantLinkModel.tenant_id == tenant))}
    barcodes = {}
    for row in await session.scalars(select(BarcodeModel).where(BarcodeModel.tenant_id == tenant)):
        barcodes.setdefault(row.sku, []).append({'id': row.id, 'sku': row.sku,
            'barcode': {'value': row.value, 'symbology': row.symbology},
            'source': row.source, 'isPrimary': row.is_primary, 'assignedAt': row.assigned_at.isoformat()})
    products = await session.scalars(select(ProductModel).where(ProductModel.tenant_id == tenant)
                                    .order_by(ProductModel.sku))
    for variant in products:
        link = links.get(variant.sku)
        product_id = link.product_id if link else variant.id
        result.setdefault(product_id, {'id': product_id, 'name': variant.name, 'variants': []})
        result[product_id]['variants'].append({'id': variant.id, 'sku': variant.sku,
            'trackingMode': link.tracking_mode if link else 'quantity',
            'attributes': link.attributes if link else [], 'barcodes': barcodes.get(variant.sku, [])})
    return list(result.values())


@router.post('/inventory/products', status_code=201)
@requires_roles(['ADMIN', 'MANAGER'])
async def create_catalog(data: CatalogDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        session.add(CatalogModel(tenant_id=current_tenant(request), id=data.id, name=data.name))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, 'Product ID already exists')
    return {'id': data.id, 'name': data.name, 'variants': []}


@router.post('/inventory/products/{product_id}/variants', status_code=201)
@requires_roles(['ADMIN', 'MANAGER'])
async def add_variant(product_id: str, data: VariantDTO, request: Request,
                      session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    parent = await session.get(CatalogModel, (tenant, product_id))
    if not parent:
        raise HTTPException(404, 'Product not found')
    try:
        SKU(data.sku)
    except InvalidSKUError as exc:
        raise HTTPException(400, str(exc))
    variant_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    try:
        session.add(ProductModel(id=variant_id, tenant_id=tenant, sku=data.sku, name=parent.name,
                                 price_amount=0, created_at=now, updated_at=now))
        await session.flush()
        session.add(VariantLinkModel(tenant_id=tenant, sku=data.sku, product_id=product_id,
            variant_id=variant_id, tracking_mode=data.trackingMode,
            attributes=[attribute.model_dump() for attribute in data.attributes]))
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, 'SKU already exists')
    return {'id': variant_id, 'sku': data.sku}


@router.get('/barcodes')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def list_barcodes(request: Request, session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(BarcodeModel).where(BarcodeModel.tenant_id == current_tenant(request)))
    return [barcode_dto(row) for row in rows]


@router.post('/barcodes/assign', status_code=201)
@requires_roles(['ADMIN', 'MANAGER'])
async def assign_barcode(data: BarcodeDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    variant = await session.scalar(select(ProductModel.id).where(
        ProductModel.tenant_id == tenant, ProductModel.sku == data.sku))
    if not variant:
        raise HTTPException(404, 'SKU not found')
    try:
        if data.makePrimary:
            await session.execute(update(BarcodeModel).where(
                BarcodeModel.tenant_id == tenant, BarcodeModel.sku == data.sku).values(is_primary=False))
        barcode = BarcodeModel(tenant_id=tenant, sku=data.sku, value=data.barcodeValue,
                              symbology=data.symbology, source=data.source, is_primary=data.makePrimary)
        session.add(barcode)
        await session.commit()
        return barcode_dto(barcode)
    except IntegrityError:
        await session.rollback()
        raise HTTPException(409, 'Barcode already assigned')


class ScanDTO(BaseUnitDTO):
    barcodeValue: str
    context: Literal['pos', 'receiving']
    scannedAmount: int = Field(gt=0, strict=True)
    locationId: str = Field(default='default', min_length=1)
    tenantId: str | None = None
    mutationId: str | None = Field(default=None, min_length=1, max_length=100)
    unitCostCents: int | None = Field(default=None, ge=0, le=2_147_483_647, strict=True)
    lotNumber: str | None = Field(default=None, min_length=1, max_length=100)
    expirationDate: AwareDatetime | None = None


class GenerateDTO(BaseModel):
    sku: str
    tenantId: str | None = None


@router.post('/barcodes/generate', status_code=201)
@requires_roles(['ADMIN', 'MANAGER'])
async def generate(data: GenerateDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    if data.tenantId is not None and data.tenantId != current_tenant(request):
        raise HTTPException(403, 'Tenant ID mismatch')
    return await assign_barcode(BarcodeDTO(sku=data.sku, barcodeValue='INV-' + uuid.uuid4().hex,
                                           source='internal'), request=request, session=session)


@router.post('/barcodes/scan')
@requires_roles(['ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR'])
async def scan(data: ScanDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant = current_tenant(request)
    if data.tenantId is not None and data.tenantId != tenant:
        raise HTTPException(403, 'Tenant ID mismatch')
    require_purchase_cost_permission(request, data.unitCostCents)
    barcode = await session.scalar(select(BarcodeModel).where(
        BarcodeModel.tenant_id == tenant, BarcodeModel.value == data.barcodeValue))
    if not barcode:
        raise HTTPException(404, 'Barcode not found')
    try:
        result = await StockService(session, tenant, request.state.user_id).mutate(
            'receive' if data.context == 'receiving' else 'dispatch', barcode.sku,
            data.locationId, data.scannedAmount, data.mutationId,
            unit_cost_cents=data.unitCostCents, lot_number=data.lotNumber,
            expiration_date=data.expirationDate)
        return stock_response(request, result)
    except StockConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
