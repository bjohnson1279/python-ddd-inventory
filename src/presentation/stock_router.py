from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field, AliasChoices, AwareDatetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.auth.rbac import requires_roles
from src.infrastructure.stock_models import StockModel, StockMovementModel
from src.application.stock_service import StockService, StockConflict, stock_dto

router = APIRouter(prefix='/inventory', tags=['inventory'])


class StockMutationDTO(BaseModel):
    sku: str = Field(min_length=1, max_length=100)
    locationId: str = Field(default='default', min_length=1, max_length=100,
                            validation_alias=AliasChoices('locationId', 'location_id'))
    quantity: int = Field(gt=0, strict=True, validation_alias=AliasChoices('quantity', 'amount'))
    mutationId: str | None = Field(default=None, min_length=1, max_length=100)
    expectedVersion: int | None = Field(default=None, ge=0)
    tenantId: str | None = None
    unitCostCents: int | None = Field(default=None, ge=0, le=2_147_483_647, strict=True)
    lotNumber: str | None = Field(default=None, min_length=1, max_length=100)
    expirationDate: AwareDatetime | None = None
    receivedAt: AwareDatetime | None = None
    quarantined: bool = False


@router.get('')
@requires_roles(['ADMIN', 'MANAGER', 'USER', 'WAREHOUSE_OPERATOR'])
async def list_stock(request: Request, session: AsyncSession = Depends(get_db_session),
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    rows = await session.scalars(select(StockModel).where(
        StockModel.tenant_id == current_tenant(request)).order_by(StockModel.sku, StockModel.location_id)
        .limit(limit).offset(offset))
    return [stock_dto(row) for row in rows]


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
        try:
            service = StockService(session, tenant, request.state.user_id)
            return await service.mutate(operation, data.sku, data.locationId, data.quantity,
                                        data.mutationId, data.expectedVersion, data.unitCostCents,
                                        data.lotNumber, data.expirationDate, data.receivedAt, data.quarantined)
        except StockConflict as exc:
            raise HTTPException(409, str(exc))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    router.add_api_route('/' + operation, mutate, methods=['POST'], name=operation)


for operation in ['receive', 'dispatch', 'allocate', 'release-allocation', 'fulfill-allocation']:
    register_mutation(operation)
