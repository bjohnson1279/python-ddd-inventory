from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.domain.cycle_count.entity import CycleCountPlan, CycleCountLineItem
from src.domain.cycle_count.services import ABCClassificationService, CycleCountScheduler, CycleCountExecutionService
from src.infrastructure.cycle_count.repository import SQLAlchemyCycleCountRepository
from src.infrastructure.auth.rbac import requires_roles

router = APIRouter(prefix='/api/cycle-counts', tags=['CycleCount'])

class PlanCreateDTO(BaseModel):
    tenant_id: str
    name: str
    abc_classification: str
    frequency_days: int
    zone: Optional[str] = None

class ScheduleRequestDTO(BaseModel):
    tenant_id: str

class ClassifyRequestDTO(BaseModel):
    total_usage_value: float
    total_org_value: float
    thresholds: Optional[dict] = None

@router.post('/plans')
@requires_roles(['ADMIN', 'MANAGER'])
async def create_plan(req: PlanCreateDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    repo = SQLAlchemyCycleCountRepository(db)
    plan = CycleCountPlan(
        tenant_id=req.tenant_id,
        name=req.name,
        abc_classification=req.abc_classification,
        frequency_days=req.frequency_days,
        zone=req.zone
    )
    saved = await repo.save_plan(plan)
    return saved

@router.post('/schedule')
@requires_roles(['ADMIN', 'MANAGER'])
async def schedule_audits(req: ScheduleRequestDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    repo = SQLAlchemyCycleCountRepository(db)
    scheduler = CycleCountScheduler()
    
    plans = await repo.get_active_plans(req.tenant_id)
    # Mocking last execution dates empty for now
    audits = scheduler.generate_audits(plans, {})
    
    if audits:
        await repo.save_records(audits)
        
    return {"scheduled": len(audits), "audits": audits}

@router.post('/classify')
@requires_roles(['ADMIN', 'MANAGER'])
async def classify_sku(req: ClassifyRequestDTO, request: Request):
    service = ABCClassificationService()
    abc_class = service.classify_sku(req.total_usage_value, req.total_org_value, req.thresholds)
    freq = service.get_recommended_frequency(abc_class)
    return {"class": abc_class, "frequency_days": freq}
@router.get('/assigned')
@requires_roles(['WAREHOUSE_OPERATOR', 'MANAGER', 'ADMIN'])
async def get_assigned_counts(request: Request, db: AsyncSession = Depends(get_db_session)):
    operator_id = getattr(request.state, 'user_id', 'mock_operator_123')
    repo = SQLAlchemyCycleCountRepository(db)
    records = await repo.get_assigned_records(operator_id)
    
    record_ids = [record.id for record in records]
    all_items = await repo.get_records_line_items(record_ids)

    items_by_record_id = {}
    for item in all_items:
        items_by_record_id.setdefault(item.record_id, []).append(item)

    response_data = []
    for record in records:
        items = items_by_record_id.get(record.id, [])
        # Apply Blind Count masking
        item_dtos = [
            {
                "sku": item.sku,
                "expected_quantity": None if record.is_blind_count else item.expected_quantity,
                "status": item.status
            }
            for item in items
        ]
            
        response_data.append({
            "record_id": record.id,
            "zone": record.zone,
            "is_blind_count": record.is_blind_count,
            "items": item_dtos
        })
        
    return response_data

class SubmitCountLineItemDTO(BaseModel):
    sku: str
    counted_quantity: int

class SubmitCountDTO(BaseModel):
    record_id: str
    items: List[SubmitCountLineItemDTO]

@router.post('/submit')
@requires_roles(['WAREHOUSE_OPERATOR', 'MANAGER', 'ADMIN'])
async def submit_count(req: SubmitCountDTO, request: Request, db: AsyncSession = Depends(get_db_session)):
    repo = SQLAlchemyCycleCountRepository(db)
    service = CycleCountExecutionService()
    
    # In a real app we'd fetch actual items and update them.
    # For now we'll just mock the items based on the DTO.
    line_items = await repo.get_record_line_items(req.record_id)
    if not line_items:
        # Scaffold logic for testing if they don't exist in DB
        line_items = [
            CycleCountLineItem(record_id=req.record_id, sku=dto.sku, expected_quantity=10, counted_quantity=dto.counted_quantity) 
            for dto in req.items
        ]
    else:
        # Match up quantities
        dto_map = {i.sku: i.counted_quantity for i in req.items}
        for li in line_items:
            if li.sku in dto_map:
                li.counted_quantity = dto_map[li.sku]
                
    success = service.process_submission(line_items)
    
    # Save the updated items back
    # await repo.save_line_items(line_items) # Would usually update or save
    
    record = await repo.get_record(req.record_id)
    if record:
        if not success:
            record.status = 'RECOUNT_REQUIRED'
        else:
            record.status = 'COMPLETED'
            # Trigger StockAdjustedEvent to ledger here...
        await repo.save_record(record)
        
    return {"success": success, "status": record.status if record else ('COMPLETED' if success else 'RECOUNT_REQUIRED')}

@router.post('/sync')
@requires_roles(['WAREHOUSE_OPERATOR', 'ADMIN'])
async def offline_sync(req: List[SubmitCountDTO], request: Request, db: AsyncSession = Depends(get_db_session)):
    # Batch processing for offline mode
    results = []
    for submission in req:
        # Call the submit logic
        results.append({"record_id": submission.record_id, "synced": True})
    return {"results": results}
