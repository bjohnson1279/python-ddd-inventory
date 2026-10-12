from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field, AwareDatetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.auth.rbac import requires_roles
from src.infrastructure.procurement_models import PurchaseOrderModel, ASNModel
from src.application.procurement_service import ProcurementService, ProcurementNotFound, po_dto, OPEN
from src.application.stock_service import StockConflict
from src.domain.product.layer_costing import utc

router = APIRouter(prefix='/api/supplier-portal', tags=['SupplierPortal'])

class POAcknowledgementDTO(BaseModel):
    expected_delivery_date: AwareDatetime
    mutationId: str = Field(min_length=1, max_length=100)

class ASNLineItemDTO(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    shipped_quantity: int = Field(gt=0, le=2_147_483_647, strict=True)
    lot_number: str | None = Field(default=None, min_length=1, max_length=100)

class ASNSubmitDTO(BaseModel):
    po_id: str
    tracking_number: str = Field(min_length=1, max_length=200)
    estimated_delivery_date: AwareDatetime
    items: list[ASNLineItemDTO] = Field(min_length=1, max_length=1000)
    mutationId: str = Field(min_length=1, max_length=100)

async def supplier_service(request: Request, db: AsyncSession = Depends(get_db_session)):
    supplier_id = getattr(request.state, 'supplier_id', None)
    if not supplier_id:
        raise HTTPException(403, 'Supplier identity is not bound to this user')
    svc = ProcurementService(db, current_tenant(request), request.state.user_id, supplier_id)
    try:
        await svc.supplier(supplier_id)
    except ProcurementNotFound as exc:
        raise HTTPException(404, str(exc))
    return svc

async def execute(svc, operation, dto, po_id=None):
    data = dto.model_dump(mode='json')
    key = data.pop('mutationId')
    if po_id:
        data['po_id'] = po_id
    try:
        return await svc.execute(operation, data, key)
    except ProcurementNotFound as exc:
        raise HTTPException(404, str(exc))
    except StockConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@router.get('/pos')
@requires_roles(['SUPPLIER_USER'])
async def list_supplier_pos(request: Request, svc=Depends(supplier_service),
                            limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    rows = await svc.session.scalars(select(PurchaseOrderModel).where(
        PurchaseOrderModel.tenant_id == svc.tenant, PurchaseOrderModel.supplier_id == svc.supplier_id,
        PurchaseOrderModel.status.in_((*OPEN, 'RECEIVED'))).order_by(PurchaseOrderModel.created_at.desc()).limit(limit).offset(offset))
    return {'pos': [po_dto(row) for row in rows]}

@router.post('/pos/{po_id}/acknowledge')
@requires_roles(['SUPPLIER_USER'])
async def acknowledge_po(po_id: str, dto: POAcknowledgementDTO, request: Request, svc=Depends(supplier_service)):
    return await execute(svc, 'acknowledge', dto, po_id)

@router.post('/asns')
@requires_roles(['SUPPLIER_USER'])
async def submit_asn(dto: ASNSubmitDTO, request: Request, svc=Depends(supplier_service)):
    return await execute(svc, 'asn', dto)

@router.get('/asns')
@requires_roles(['SUPPLIER_USER'])
async def asns(request: Request, svc=Depends(supplier_service), limit: int = Query(100, ge=1, le=1000)):
    rows = await svc.session.scalars(select(ASNModel).where(ASNModel.tenant_id == svc.tenant,
                                                           ASNModel.supplier_id == svc.supplier_id).limit(limit))
    return {'asns': [{'id': row.id, **row.payload} for row in rows]}

@router.get('/performance')
@requires_roles(['SUPPLIER_USER'])
async def performance(request: Request, svc=Depends(supplier_service)):
    rows = list(await svc.session.scalars(select(PurchaseOrderModel).where(
        PurchaseOrderModel.tenant_id == svc.tenant, PurchaseOrderModel.supplier_id == svc.supplier_id,
        PurchaseOrderModel.status == 'RECEIVED')))
    measured = [row for row in rows if row.expected_delivery_date and row.received_at]
    on_time = sum(utc(row.received_at) <= utc(row.expected_delivery_date) for row in measured)
    return {'performance': {'completed_orders': len(rows), 'measured_orders': len(measured),
        'otif_percentage': 100 * on_time / len(measured) if measured else None,
        'defect_rate_percentage': None}}
