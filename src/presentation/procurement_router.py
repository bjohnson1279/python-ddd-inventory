from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field, AliasChoices, AwareDatetime
from typing import Literal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.procurement_models import SupplierModel, PurchaseOrderModel, ReorderPolicyModel, ASNModel
from src.application.procurement_service import ProcurementService, ProcurementNotFound, supplier_dto, po_dto, policy_dto
from src.application.stock_service import StockConflict
from src.presentation.base_unit_contract import BaseUnitDTO

router = APIRouter(tags=['procurement and replenishment'])
Quantity = Field(gt=0, le=2_147_483_647, strict=True)


class CommandDTO(BaseModel):
    mutationId: str = Field(min_length=1, max_length=100)
    expectedVersion: int | None = Field(default=None, ge=0, strict=True)
    reason: str | None = Field(default=None, min_length=1, max_length=500, pattern=r'\S')


class SupplierDTO(BaseModel):
    name: str = Field(min_length=1, max_length=200, pattern=r'\S')
    email: str | None = Field(default=None, max_length=254)
    leadTimeDays: int = Field(default=0, ge=0, le=3650, strict=True)
    mutationId: str = Field(min_length=1, max_length=100)


class SupplierUpdateDTO(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200, pattern=r'\S')
    email: str | None = Field(default=None, max_length=254)
    leadTimeDays: int | None = Field(default=None, ge=0, le=3650, strict=True)
    isActive: bool | None = None
    expectedVersion: int = Field(ge=0, strict=True)
    mutationId: str = Field(min_length=1, max_length=100)


class OrderItemDTO(BaseUnitDTO):
    sku: str = Field(min_length=1, max_length=100)
    quantity: int = Quantity
    unitCostCents: int = Field(ge=0, le=2_147_483_647, strict=True)


class OrderDTO(BaseModel):
    supplierId: str = Field(min_length=1, max_length=100, validation_alias=AliasChoices('supplierId', 'supplier'))
    locationId: str = Field(default='default', min_length=1, max_length=100)
    items: list[OrderItemDTO] = Field(min_length=1, max_length=1000)
    expectedDeliveryDate: AwareDatetime | None = None
    currency: Literal['USD'] = 'USD'
    mutationId: str = Field(min_length=1, max_length=100)


class ReceiptItemDTO(BaseUnitDTO):
    sku: str = Field(min_length=1, max_length=100)
    quantity: int = Quantity
    lotNumber: str | None = Field(default=None, min_length=1, max_length=100)
    expirationDate: AwareDatetime | None = None
    quarantined: bool = False


class ReceiptDTO(BaseModel):
    receiptId: str = Field(min_length=1, max_length=100)
    expectedVersion: int | None = Field(default=None, ge=0, strict=True)
    items: list[ReceiptItemDTO] = Field(min_length=1, max_length=1000)


class PolicyDTO(BaseUnitDTO):
    sku: str = Field(min_length=1, max_length=100)
    locationId: str = Field(default='default', min_length=1, max_length=100)
    supplierId: str = Field(min_length=1, max_length=100)
    reorderPoint: int = Field(ge=0, le=2_147_483_647, strict=True)
    safetyStock: int = Field(default=0, ge=0, le=2_147_483_647, strict=True)
    economicOrderQuantity: int = Field(default=1, gt=0, le=2_147_483_647, strict=True)
    targetStock: int | None = Field(default=None, ge=0, le=2_147_483_647, strict=True)
    leadTimeDays: int | None = Field(default=None, ge=0, le=3650, strict=True)
    casePack: int = Field(default=1, gt=0, le=2_147_483_647, strict=True)
    minimumOrderQuantity: int = Field(default=1, gt=0, le=2_147_483_647, strict=True)
    unitCostCents: int | None = Field(default=None, ge=0, le=2_147_483_647, strict=True)
    expectedVersion: int | None = Field(default=None, ge=0, strict=True)
    mutationId: str = Field(min_length=1, max_length=100)


def service(request, session):
    return ProcurementService(session, current_tenant(request), request.state.user_id)


async def execute(request, session, operation, data, key=None):
    payload = data.model_dump(mode='json') if isinstance(data, BaseModel) else dict(data)
    key = key or payload.pop('mutationId')
    try:
        return await service(request, session).execute(operation, payload, key)
    except ProcurementNotFound as exc:
        raise HTTPException(404, str(exc))
    except StockConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('/suppliers')
async def suppliers(request: Request, session: AsyncSession = Depends(get_db_session),
                    limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return [supplier_dto(row) for row in await session.scalars(select(SupplierModel).where(
        SupplierModel.tenant_id == current_tenant(request)).order_by(SupplierModel.name, SupplierModel.id).limit(limit).offset(offset))]


@router.post('/suppliers', status_code=201)
async def create_supplier(data: SupplierDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'create_supplier', data)


@router.patch('/suppliers/{supplier_id}')
async def update_supplier(supplier_id: str, data: SupplierUpdateDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    payload = data.model_dump(mode='json', exclude_unset=True)
    if any(payload.get(key) is None for key in ('name', 'leadTimeDays', 'isActive') if key in payload):
        raise HTTPException(422, 'Supplier name, lead time and status cannot be null')
    return await execute(request, session, 'update_supplier', {'supplier_id': supplier_id, **payload})


@router.get('/purchase-orders')
async def purchase_orders(request: Request, session: AsyncSession = Depends(get_db_session),
                          limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return [po_dto(row) for row in await session.scalars(select(PurchaseOrderModel).where(
        PurchaseOrderModel.tenant_id == current_tenant(request)).order_by(PurchaseOrderModel.created_at.desc(), PurchaseOrderModel.id).limit(limit).offset(offset))]


@router.get('/purchase-orders/{po_id}')
async def purchase_order(po_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        return po_dto(await service(request, session).po(po_id))
    except ProcurementNotFound as exc:
        raise HTTPException(404, str(exc))


@router.post('/purchase-orders', status_code=201)
async def create_purchase_order(data: OrderDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'create_po', data)


@router.get('/purchase-orders/{po_id}/asns')
async def order_asns(po_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        await service(request, session).po(po_id)
    except ProcurementNotFound as exc:
        raise HTTPException(404, str(exc))
    return [{'id': row.id, **row.payload} for row in await session.scalars(select(ASNModel).where(
        ASNModel.tenant_id == current_tenant(request), ASNModel.po_id == po_id))]


def transition(action, target):
    async def change(po_id: str, data: CommandDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
        if target == 'CANCELLED' and not data.reason:
            raise HTTPException(400, 'Cancellation requires a reason')
        return await execute(request, session, 'transition', {'po_id': po_id, 'target': target, **data.model_dump(mode='json')})
    router.add_api_route('/purchase-orders/{po_id}/' + action, change, methods=['POST'], name=action + '-po')


for action, target in [('approve', 'APPROVED'), ('send', 'SENT'), ('cancel', 'CANCELLED')]:
    transition(action, target)


@router.post('/purchase-orders/{po_id}/receive')
async def receive(po_id: str, data: ReceiptDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'receive', {'po_id': po_id, **data.model_dump(mode='json')},
                         key='receipt:' + po_id + ':' + data.receiptId)


@router.get('/reorder-policies')
async def policies(request: Request, session: AsyncSession = Depends(get_db_session),
                   limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    return [policy_dto(row) for row in await session.scalars(select(ReorderPolicyModel).where(
        ReorderPolicyModel.tenant_id == current_tenant(request)).order_by(ReorderPolicyModel.sku, ReorderPolicyModel.location_id).limit(limit).offset(offset))]


@router.post('/reorder-policies')
async def save_policy(data: PolicyDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'save_policy', data)


@router.get('/reorder-policies/suggestions')
@router.post('/reorder-policies/evaluate')
async def suggestions(request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        return await service(request, session).suggestions()
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post('/reorder-policies/{policy_id}/draft', status_code=201)
async def draft(policy_id: str, data: CommandDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    if data.expectedVersion is None:
        raise HTTPException(400, 'Policy version is required')
    return await execute(request, session, 'draft_from_policy', {'policy_id': policy_id, **data.model_dump(mode='json')})
