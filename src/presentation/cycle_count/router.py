from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.auth.rbac import requires_roles
from src.infrastructure.auth.security import require_purchase_cost_permission
from src.infrastructure.cycle_count.models import CycleCountRecordModel, CycleCountPlanModel
from src.domain.cycle_count.services import ABCClassificationService
from src.application.count_service import CountService, CountNotFound
from src.application.stock_service import StockConflict
from src.presentation.base_unit_contract import BaseUnitDTO

router = APIRouter(prefix='/api/cycle-counts', tags=['CycleCount'])

class PlanCreateDTO(BaseModel):
    tenant_id: str
    name: str = Field(min_length=1, max_length=100)
    abc_classification: Literal['A', 'B', 'C']
    frequency_days: int = Field(gt=0, le=3650, strict=True)
    zone: str | None = Field(default=None, min_length=1, max_length=100)

class ScheduleRequestDTO(BaseModel):
    tenant_id: str

class ClassifyRequestDTO(BaseModel):
    total_usage_value: float = Field(ge=0, allow_inf_nan=False)
    total_org_value: float = Field(ge=0, allow_inf_nan=False)
    thresholds: dict | None = None

class StartDTO(BaseModel):
    name: str = Field(default='Physical count', min_length=1, max_length=100)
    location_id: str = Field(default='default', min_length=1, max_length=100)
    skus: list[str] | None = Field(default=None, min_length=1, max_length=1000)
    assigned_operator_id: str | None = None
    is_blind_count: bool = True
    mutation_id: str = Field(min_length=1, max_length=100)

class SubmitCountLineItemDTO(BaseUnitDTO):
    sku: str = Field(min_length=1, max_length=100)
    counted_quantity: int = Field(ge=0, le=2_147_483_647, strict=True)

class SubmitCountDTO(BaseModel):
    record_id: str
    items: list[SubmitCountLineItemDTO] = Field(min_length=1, max_length=1000)
    revision: int | None = Field(default=None, ge=0, strict=True)
    mutation_id: str | None = Field(default=None, min_length=1, max_length=100)

class OfflineDTO(SubmitCountDTO):
    revision: int = Field(ge=0, strict=True)
    mutation_id: str = Field(min_length=1, max_length=100)

class ReviewDTO(BaseModel):
    revision: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')
    mutation_id: str = Field(min_length=1, max_length=100)
    location_id: str | None = Field(default=None, min_length=1, max_length=100)

class AdjustmentDTO(BaseUnitDTO):
    sku: str = Field(min_length=1, max_length=100)
    location_id: str = Field(default='default', min_length=1, max_length=100)
    delta: int = Field(strict=True, ge=-2_147_483_647, le=2_147_483_647)
    expected_version: int = Field(ge=0, strict=True)
    reason_code: Literal['DAMAGE', 'THEFT', 'FOUND', 'CORRECTION', 'EXPIRED']
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')
    mutation_id: str = Field(min_length=1, max_length=100)
    unit_cost_cents: int | None = Field(default=None, ge=0, le=2_147_483_647, strict=True)
    lot_number: str | None = Field(default=None, min_length=1, max_length=100)


def service(request, db):
    return CountService(db, current_tenant(request), request.state.user_id,
                        bool(set(request.state.roles) & {'ADMIN', 'MANAGER'}))

async def execute(request, db, operation, dto):
    data = dto.model_dump() if isinstance(dto, BaseModel) else dict(dto)
    key = data.pop('mutation_id', None)
    try:
        return await service(request, db).execute(operation, data, key)
    except CountNotFound as exc:
        raise HTTPException(404, str(exc))
    except StockConflict as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))

@router.post('/plans')
@requires_roles(['ADMIN', 'MANAGER'])
async def create_plan(req: PlanCreateDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    row = CycleCountPlanModel(tenant_id=current_tenant(request), **{k: v for k, v in req.model_dump().items() if k != 'tenant_id'})
    db.add(row)
    await db.commit()
    return row

@router.post('/schedule')
@requires_roles(['ADMIN', 'MANAGER'])
async def schedule_audits(req: ScheduleRequestDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    return await execute(request, db, 'schedule', req)

@router.post('/classify')
@requires_roles(['ADMIN', 'MANAGER'])
async def classify_sku(req: ClassifyRequestDTO, request: Request):
    svc = ABCClassificationService()
    if req.thresholds is not None and (set(req.thresholds) != {'a_threshold', 'b_threshold'} or
        not all(type(v) in (int, float) for v in req.thresholds.values()) or
        not 0 <= req.thresholds['b_threshold'] <= req.thresholds['a_threshold'] <= 1):
        raise HTTPException(400, 'Invalid classification thresholds')
    classification = svc.classify_sku(req.total_usage_value, req.total_org_value, req.thresholds)
    return {'class': classification, 'frequency_days': svc.get_recommended_frequency(classification)}

@router.post('/start')
@requires_roles(['ADMIN', 'MANAGER'])
async def start(req: StartDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    if req.skus and len(set(req.skus)) != len(req.skus):
        raise HTTPException(400, 'Duplicate count SKU')
    return await execute(request, db, 'start', req)

@router.get('')
@router.get('/assigned')
@requires_roles(['ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR'])
async def list_counts(request: Request, db: AsyncSession = Depends(get_db_session),
                      limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
    svc = service(request, db)
    query = select(CycleCountRecordModel).where(CycleCountRecordModel.tenant_id == svc.tenant)
    if request.url.path.endswith('/assigned') or not svc.manager:
        query = query.where(CycleCountRecordModel.assigned_operator_id == svc.actor,
                            CycleCountRecordModel.status != 'COMPLETED')
    rows = await db.scalars(query.order_by(CycleCountRecordModel.created_at.desc()).limit(limit).offset(offset))
    return [await svc.dto(row) for row in rows]

@router.post('/submit')
@requires_roles(['ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR'])
async def submit(req: SubmitCountDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    return await execute(request, db, 'submit', req)

@router.post('/sync')
@requires_roles(['ADMIN', 'MANAGER', 'WAREHOUSE_OPERATOR'])
async def sync(req: list[OfflineDTO], request: Request, db: AsyncSession = Depends(get_db_session)):
    if not 1 <= len(req) <= 100:
        raise HTTPException(400, 'Offline batches require 1 to 100 submissions')
    return await execute(request, db, 'sync', {'submissions': [dto.model_dump() for dto in req]})

@router.post('/{record_id}/approve')
@requires_roles(['ADMIN', 'MANAGER'])
async def approve(record_id: str, req: ReviewDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    return await execute(request, db, 'approve', {'record_id': record_id, **req.model_dump()})

@router.post('/{record_id}/recount')
@requires_roles(['ADMIN', 'MANAGER'])
async def recount(record_id: str, req: ReviewDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    return await execute(request, db, 'recount', {'record_id': record_id, **req.model_dump()})

@router.post('/adjust')
@requires_roles(['ADMIN', 'MANAGER'])
async def adjust(req: AdjustmentDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    if req.delta == 0 or (req.delta < 0 and req.unit_cost_cents is not None):
        raise HTTPException(400, 'Adjustment must be nonzero; only gains accept a cost')
    if req.reason_code in ('DAMAGE', 'THEFT', 'EXPIRED') and req.delta > 0:
        raise HTTPException(400, 'Loss reason codes require a negative adjustment')
    if req.reason_code == 'FOUND' and req.delta < 0:
        raise HTTPException(400, 'Found stock requires a positive adjustment')
    require_purchase_cost_permission(request, req.unit_cost_cents)
    return await execute(request, db, 'adjust', req)
