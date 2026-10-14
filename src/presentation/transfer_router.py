from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.transfer_models import TransferModel
from src.presentation.stock_router import UnitQuantity, UnitCode
from src.application.transfer_service import InventoryWorkflow, transfer_dto, transfer_response
from src.application.uom_service import configured, uom_dto
from src.application.stock_service import StockConflict
from src.domain.uom import STANDARD_UNITS, factor, decimal_fraction, decimal_text
from src.domain.product.layer_costing import half_up, validate_total_value

AnnotatedVersion = Annotated[int, Field(ge=0, strict=True)]
LiteralCategory = Literal['discrete', 'weight', 'volume']

router = APIRouter(tags=['transfers and units of measure'])


class Command(BaseModel):
    mutationId: str = Field(min_length=1, max_length=100)
    expectedVersion: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')


class TransferLine(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    quantity: UnitQuantity
    unit: UnitCode | None = None
    uomVersion: int | None = Field(default=None, ge=0, strict=True)
    lotNumber: str | None = Field(default=None, min_length=1, max_length=100)


class CreateTransfer(BaseModel):
    sourceLocationId: str = Field(min_length=1, max_length=100)
    destinationLocationId: str = Field(min_length=1, max_length=100)
    items: list[TransferLine] = Field(min_length=1, max_length=1000)
    reference: str | None = Field(default=None, min_length=1, max_length=200)
    mutationId: str = Field(min_length=1, max_length=100)


class TransferCommand(Command):
    sourceVersions: dict[str, AnnotatedVersion] | None = None


class Rule(BaseModel):
    unit: UnitCode
    factorToBase: str = Field(min_length=1, max_length=32)


class UomConfiguration(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    baseUnit: UnitCode = 'ea'
    category: LiteralCategory = 'discrete'
    purchaseUnit: UnitCode | None = None
    saleUnit: UnitCode | None = None
    rules: list[Rule] = Field(default_factory=list, max_length=100)
    expectedVersion: int = Field(ge=0, strict=True)
    mutationId: str = Field(min_length=1, max_length=100)


class Conversion(BaseModel):
    quantity: UnitQuantity
    fromUnit: UnitCode
    toUnit: UnitCode | None = None
    unitCostCents: int | None = Field(default=None, ge=0, le=2_147_483_647, strict=True)


async def execute(request, session, operation, data):
    payload = data.model_dump(mode='json') if isinstance(data, BaseModel) else dict(data)
    key = payload.pop('mutationId')
    try:
        result = await InventoryWorkflow(session, current_tenant(request), request.state.user_id).execute(operation, payload, key)
        return transfer_response(request, result)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except StockConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post('/inventory/transfers', status_code=201)
async def create(data: CreateTransfer, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'create_transfer', data)


@router.get('/inventory/transfers')
async def list_transfers(request: Request, session: AsyncSession = Depends(get_db_session),
                         limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    rows = await session.scalars(select(TransferModel).where(TransferModel.tenant_id == current_tenant(request))
        .order_by(TransferModel.created_at.desc(), TransferModel.id).limit(limit).offset(offset))
    return transfer_response(request, [transfer_dto(row) for row in rows])


@router.get('/inventory/transfers/{transfer_id}')
async def get_transfer(transfer_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        row = await InventoryWorkflow(session, current_tenant(request), request.state.user_id).owned_transfer(transfer_id)
        return transfer_response(request, transfer_dto(row))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


def register(action, target):
    async def transition(transfer_id: str, data: TransferCommand, request: Request, session: AsyncSession = Depends(get_db_session)):
        return await execute(request, session, 'transition_transfer', {'transferId': transfer_id, 'target': target, **data.model_dump(mode='json')})
    router.add_api_route('/inventory/transfers/{transfer_id}/' + action, transition, methods=['POST'])


for action, target in [('dispatch', 'DISPATCHED'), ('receive', 'RECEIVED'), ('cancel', 'CANCELLED')]:
    register(action, target)


@router.get('/uom/units')
async def units():
    return STANDARD_UNITS


@router.post('/uom/configurations')
async def configure(data: UomConfiguration, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'configure_uom', data)


@router.get('/uom/configurations/{sku}')
async def get_configuration(sku: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    row = await configured(session, current_tenant(request), sku)
    if not row:
        raise HTTPException(404, 'UOM configuration not found')
    return uom_dto(row)


@router.post('/uom/configurations/{sku}/convert')
async def convert(sku: str, data: Conversion, request: Request, session: AsyncSession = Depends(get_db_session)):
    row = await configured(session, current_tenant(request), sku)
    if not row:
        raise HTTPException(404, 'UOM configuration not found')
    try:
        amount = decimal_fraction(data.quantity)
        if amount < 0 or amount > 2_147_483_647:
            raise ValueError('Quantity is outside the supported limit')
        base = amount * factor(row, data.fromUnit)
        target = data.toUnit or row.base_unit
        result = {'sku': sku, 'quantity': decimal_text(base / factor(row, target)), 'unit': target,
                'baseUnit': row.base_unit, 'baseQuantity': decimal_text(base),
                'ledgerRepresentable': base.denominator == 1 and 0 <= base <= 2_147_483_647, 'uomVersion': row.version}
        if data.unitCostCents is not None:
            price = data.unitCostCents * factor(row, target) / factor(row, data.fromUnit)
            rounded_price = half_up(price.numerator, price.denominator)
            validate_total_value(rounded_price)
            result.update(exactUnitCostCents=decimal_text(price), roundedUnitCostCents=rounded_price)
        return result
    except ValueError as exc:
        raise HTTPException(400, str(exc))
