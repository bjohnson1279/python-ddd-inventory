from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel, Field, ConfigDict, AwareDatetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.auth.security import require_purchase_cost_permission
from src.infrastructure.serial_kit_models import SerialModel, KitModel
from src.application.serial_kit_service import SerialKitWorkflow, serial_dto, kit_dto
from src.application.transfer_service import transfer_response
from src.application.stock_service import StockConflict

router = APIRouter(tags=['serials and kits'])
Positive = Annotated[int, Field(gt=0, le=2147483647, strict=True)]
Version = Annotated[int, Field(ge=0, strict=True)]


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mutationId: str = Field(min_length=1, max_length=100, pattern=r'\S')
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')


class RegisterSerial(Command):
    sku: str = Field(min_length=1, max_length=100)
    serialNumber: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$')
    locationId: str = Field(default='default', min_length=1, max_length=100)
    referenceId: str | None = Field(default=None, min_length=1, max_length=100)


class SerialCommand(Command):
    expectedVersion: Version
    referenceId: str = Field(min_length=1, max_length=100, pattern=r'\S')
    locationId: str | None = Field(default=None, min_length=1, max_length=100)
    destinationId: str | None = Field(default=None, min_length=1, max_length=100)
    unitCostCents: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    expirationDate: AwareDatetime | None = None


class Component(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sku: str = Field(min_length=1, max_length=100)
    quantity: Positive


class DefineKit(Command):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=200, pattern=r'\S')
    expectedVersion: Version
    components: list[Component] = Field(min_length=1, max_length=100)


class KitCommand(Command):
    tenantId: str | None = None
    kitSku: str = Field(min_length=1, max_length=100)
    locationId: str = Field(default='default', min_length=1, max_length=100)
    quantity: int = Field(gt=0, le=100, strict=True)
    expectedVersion: Version
    stockVersions: dict[str, Version]
    referenceId: str = Field(min_length=1, max_length=100, pattern=r'\S')


async def execute(request, session, operation, data):
    payload = dict(data)
    key = payload.pop('mutationId')
    try:
        result = await SerialKitWorkflow(session, current_tenant(request), request.state.user_id).execute(operation, payload, key)
        return transfer_response(request, result)
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(404, str(exc)) from exc
    except StockConflict as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get('/serials')
async def list_serials(request: Request, sku: str | None = None, limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    query = select(SerialModel).where(SerialModel.tenant_id == current_tenant(request))
    if sku is not None:
        query = query.where(SerialModel.sku == sku)
    return transfer_response(request, [serial_dto(r) for r in await session.scalars(query.order_by(SerialModel.serial_number).limit(limit).offset(offset))])


@router.get('/serials/trace/{number}')
async def trace(number: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        row = await SerialKitWorkflow(session, current_tenant(request), request.state.user_id).serial(number)
        return transfer_response(request, serial_dto(row))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/serials', status_code=201)
async def register(data: RegisterSerial, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'define_serial', data.model_dump(mode='json'))


@router.post('/serials/{number}/{action}')
async def serial_command(number: str, action: Literal['receive', 'sell', 'return', 'restock', 'quarantine', 'damage', 'write-off', 'transfer', 'reserve', 'release', 'price'],
    data: SerialCommand, request: Request, session: AsyncSession = Depends(get_db_session)):
    require_purchase_cost_permission(request, data.unitCostCents)
    return await execute(request, session, 'change_serial', {**data.model_dump(mode='json'), 'serialNumber': number, 'action': action})


@router.get('/kits')
async def list_kits(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(KitModel).where(KitModel.tenant_id == current_tenant(request)).order_by(KitModel.sku).limit(limit).offset(offset))
    return [kit_dto(row) for row in rows]


@router.get('/kits/{sku}')
async def get_kit(sku: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    row = await session.scalar(select(KitModel).where(KitModel.tenant_id == current_tenant(request), KitModel.sku == sku))
    if row is None:
        raise HTTPException(404, 'Kit not found')
    return kit_dto(row)


@router.post('/kits', status_code=201)
async def define_kit(data: DefineKit, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'define_kit', data.model_dump(mode='json'))


@router.post('/kits/{action}')
async def kit_command(action: Literal['assemble', 'disassemble', 'sale'], data: KitCommand,
    request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'use_kit', {**data.model_dump(mode='json'), 'action': action})
