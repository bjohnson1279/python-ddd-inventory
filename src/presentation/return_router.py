from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field, field_validator, AliasChoices, model_validator
from src.presentation.base_unit_contract import BaseUnitDTO
from src.presentation.stock_router import UnitQuantity, UnitCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.return_models import ReturnModel, QuarantineModel
from src.infrastructure.stock_models import StockMovementModel
from src.application.return_service import ReturnService, ReturnNotFound, rma_dto, quarantine_dto
from src.application.stock_service import StockConflict
from src.domain.product.layer_costing import utc

router = APIRouter(prefix='/returns', tags=['returns and write-offs'])


class ReturnLineDTO(BaseUnitDTO):
    layerId: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, le=2_147_483_647, strict=True)
    disposition: Literal['RESTOCK', 'QUARANTINE', 'SCRAP', 'RETURN_TO_VENDOR'] | None = None


class CreateReturnDTO(BaseModel):
    kind: Literal['CUSTOMER', 'SUPPLIER'] = 'CUSTOMER'
    sourceMovementId: str = Field(min_length=1, max_length=100)
    items: list[ReturnLineDTO] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')
    refundReference: str | None = Field(default=None, min_length=1, max_length=200)
    mutationId: str = Field(min_length=1, max_length=100)


class CommandDTO(BaseModel):
    expectedVersion: int = Field(ge=0, strict=True)
    mutationId: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')


class ReceiptLineDTO(BaseUnitDTO):
    layerId: str = Field(min_length=1, max_length=100)
    quantity: int = Field(gt=0, le=2_147_483_647, strict=True)


class ReceiveReturnDTO(BaseModel):
    receiptId: str = Field(min_length=1, max_length=100, validation_alias=AliasChoices('receiptId', 'shipmentId'))
    expectedVersion: int = Field(ge=0, strict=True)
    items: list[ReceiptLineDTO] = Field(min_length=1, max_length=1000)


class WriteOffDTO(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    locationId: str = Field(default='default', min_length=1, max_length=100)
    quantity: UnitQuantity
    unit: UnitCode | None = None
    uomVersion: int | None = Field(default=None, ge=0, strict=True)
    expectedVersion: int = Field(ge=0, strict=True)
    layerId: str | None = Field(default=None, min_length=1, max_length=100)
    lotNumber: str | None = Field(default=None, min_length=1, max_length=100)
    reasonCode: Literal['DAMAGE', 'THEFT', 'EXPIRED', 'OBSOLETE', 'SHRINKAGE']
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')
    reference: str | None = Field(default=None, min_length=1, max_length=200)
    mutationId: str = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def quantity_contract(self):
        if not self.unit and (type(self.quantity) is not int or not 0 < self.quantity <= 2_147_483_647):
            raise ValueError('Quantity must be a positive integer when no unit is supplied')
        if self.unit and self.uomVersion is None:
            raise ValueError('Unit-bearing write-offs require uomVersion')
        return self


class ResolveDTO(CommandDTO):
    resolution: Literal['RESTOCK', 'SCRAP']

    @field_validator('resolution', mode='before')
    @classmethod
    def normalize(cls, value):
        return 'SCRAP' if isinstance(value, str) and value.upper() == 'DISPOSE' else value.upper() if isinstance(value, str) else value


def service(request, session):
    return ReturnService(session, current_tenant(request), request.state.user_id)


async def execute(request, session, operation, data, key=None):
    payload = data.model_dump(mode='json') if isinstance(data, BaseModel) else dict(data)
    key = key or payload.pop('mutationId')
    try:
        return await service(request, session).execute(operation, payload, key)
    except ReturnNotFound as exc:
        raise HTTPException(404, str(exc))
    except StockConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get('/rmas')
async def returns(request: Request, session: AsyncSession = Depends(get_db_session),
                  limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    rows = await session.scalars(select(ReturnModel).where(ReturnModel.tenant_id == current_tenant(request))
        .order_by(ReturnModel.created_at.desc(), ReturnModel.id).limit(limit).offset(offset))
    return [rma_dto(row) for row in rows]


@router.get('/rmas/{return_id}')
async def get_return(return_id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        return rma_dto(await service(request, session).owned(ReturnModel, return_id))
    except ReturnNotFound as exc:
        raise HTTPException(404, str(exc))


@router.post('/rmas', status_code=201)
async def create_return(data: CreateReturnDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'create', data)


def transition(action, target):
    async def change(return_id: str, data: CommandDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
        return await execute(request, session, 'transition', {'return_id': return_id, 'target': target, **data.model_dump(mode='json')})
    router.add_api_route('/rmas/{return_id}/' + action, change, methods=['POST'], name=action + '-return')


transition('authorize', 'AUTHORIZED')
transition('cancel', 'CANCELLED')


@router.post('/rmas/{return_id}/receive')
async def receive_return(return_id: str, data: ReceiveReturnDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'receive', {'return_id': return_id, **data.model_dump(mode='json')},
                         key='receipt:' + return_id + ':' + data.receiptId)


@router.post('/rmas/{return_id}/ship')
async def ship_return(return_id: str, data: ReceiveReturnDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'ship', {'return_id': return_id, **data.model_dump(mode='json')},
                         key='receipt:' + return_id + ':' + data.receiptId)


@router.get('/quarantine')
async def quarantine(request: Request, session: AsyncSession = Depends(get_db_session),
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    rows = await session.scalars(select(QuarantineModel).where(QuarantineModel.tenant_id == current_tenant(request),
        QuarantineModel.status == 'QUARANTINED').order_by(QuarantineModel.created_at, QuarantineModel.id).limit(limit).offset(offset))
    return [quarantine_dto(row) for row in rows]


@router.post('/quarantine/{quarantine_id}/resolve')
async def resolve(quarantine_id: str, data: ResolveDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'resolve', {'quarantine_id': quarantine_id, **data.model_dump(mode='json')})


@router.post('/write-offs')
async def write_off(data: WriteOffDTO, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await execute(request, session, 'write_off', data)


@router.get('/write-offs')
async def write_offs(request: Request, session: AsyncSession = Depends(get_db_session),
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    rows = await session.scalars(select(StockMovementModel).where(StockMovementModel.tenant_id == current_tenant(request),
        StockMovementModel.operation == 'write-off').order_by(StockMovementModel.occurred_at.desc()).limit(limit).offset(offset))
    return [{'id': row.id, 'actorId': row.actor_id, 'createdAt': utc(row.occurred_at).isoformat(), **row.result} for row in rows]
