from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.domain.cycle_count.entity import CycleCountPlan
from src.domain.cycle_count.services import ABCClassificationService, CycleCountScheduler
from src.infrastructure.cycle_count.repository import SQLAlchemyCycleCountRepository

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
async def create_plan(req: PlanCreateDTO, db: AsyncSession = Depends(get_db_session)):
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
async def schedule_audits(req: ScheduleRequestDTO, db: AsyncSession = Depends(get_db_session)):
    repo = SQLAlchemyCycleCountRepository(db)
    scheduler = CycleCountScheduler()
    
    plans = await repo.get_active_plans(req.tenant_id)
    # Mocking last execution dates empty for now
    audits = scheduler.generate_audits(plans, {})
    
    for audit in audits:
        await repo.save_record(audit)
        
    return {"scheduled": len(audits), "audits": audits}

@router.post('/classify')
async def classify_sku(req: ClassifyRequestDTO):
    service = ABCClassificationService()
    abc_class = service.classify_sku(req.total_usage_value, req.total_org_value, req.thresholds)
    freq = service.get_recommended_frequency(abc_class)
    return {"class": abc_class, "frequency_days": freq}
