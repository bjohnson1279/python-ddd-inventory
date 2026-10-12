from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field, AliasChoices, AwareDatetime, StrictInt, StringConstraints, model_validator
from typing import Annotated
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.auth.security import require_purchase_cost_permission, stock_response
from src.infrastructure.auth.rbac import requires_roles
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.application.stock_service import StockService, StockConflict, stock_dto
from src.infrastructure.transfer_models import UomConfigModel

UnitQuantity = StrictInt | Annotated[str, StringConstraints(pattern=r'^\d+(?:\.\d{1,6})?$', max_length=32)]
UnitCode = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]{0,19}$')]

router = APIRouter(prefix='/inventory', tags=['inventory'])


class StockMutationDTO(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    locationId: str = Field(default='default', min_length=1, max_length=100,
                            validation_alias=AliasChoices('locationId', 'location_id'))
    quantity: UnitQuantity = Field(validation_alias=AliasChoices('quantity', 'amount'))
    unit: UnitCode | None = None
    uomVersion: int | None = Field(default=None, ge=0, strict=True)
    mutationId: str | None = Field(default=None, min_length=1, max_length=100)
    expectedVersion: int | None = Field(default=None, ge=0)
    tenantId: str | None = None
    unitCostCents: int | None = Field(default=None, ge=0, le=2_147_483_647, strict=True)
    lotNumber: str | None = Field(default=None, min_length=1, max_length=100)
    expirationDate: AwareDatetime | None = None
    receivedAt: AwareDatetime | None = None
    quarantined: bool = False

    @model_validator(mode='after')
    def quantity_contract(self):
        if not self.unit and (type(self.quantity) is not int or self.quantity <= 0):
            raise ValueError('Quantity must be a positive integer when no unit is supplied')
        if self.unit and (self.uomVersion is None or self.mutationId is None):
            raise ValueError('Unit-bearing mutations require uomVersion and mutationId')
        return self


@router.get('')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def list_stock(request: Request, session: AsyncSession = Depends(get_db_session),
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    rows = await session.scalars(select(StockModel).where(
        StockModel.tenant_id == current_tenant(request)).order_by(StockModel.sku, StockModel.location_id)
        .limit(limit).offset(offset))
    units = {row.sku: row.base_unit for row in await session.scalars(select(UomConfigModel).where(UomConfigModel.tenant_id == current_tenant(request)))}
    return [{**stock_dto(row), 'baseUnit': units.get(row.sku, 'ea')} for row in rows]


@router.get('/movements')
@requires_roles(['ADMIN', 'MANAGER'])
async def movements(request: Request, session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(StockMovementModel).where(
        StockMovementModel.tenant_id == current_tenant(request)).order_by(StockMovementModel.occurred_at).limit(1000))
    return [{'id': row.id, 'mutationId': row.mutation_id, 'actorId': row.actor_id,
             'operation': row.operation, 'amount': row.amount, 'sku': row.sku,
             'locationId': row.location_id, 'result': row.result} for row in rows]


def register_mutation(operation):
    @requires_roles(['ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR'])
    async def mutate(data: StockMutationDTO, request: Request,
                     session: AsyncSession = Depends(get_db_session)):
        tenant = current_tenant(request)
        if data.tenantId is not None and data.tenantId != tenant:
            raise HTTPException(403, 'Tenant ID mismatch')
        require_purchase_cost_permission(request, data.unitCostCents)
        try:
            service = StockService(session, tenant, request.state.user_id)
            result = await service.mutate(operation, data.sku, data.locationId, data.quantity,
                                        data.mutationId, data.expectedVersion, data.unitCostCents,
                                        data.lotNumber, data.expirationDate, data.receivedAt, data.quarantined,
                                        unit=data.unit, uom_version=data.uomVersion)
            return stock_response(request, result)
        except StockConflict as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    router.add_api_route('/' + operation, mutate, methods=['POST'], name=operation)


for operation in ['receive', 'dispatch', 'allocate', 'release-allocation', 'fulfill-allocation']:
    register_mutation(operation)
