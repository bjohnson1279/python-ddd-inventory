from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.report_alert_models import AlertConfig, AlertCondition
from src.infrastructure.retail_alerts import evaluate
from src.infrastructure.transfer_models import InventoryActionModel as Action
from src.application.costing_service import locked_config

router = APIRouter(prefix='/alerts', tags=['retail alerts'])

class Config(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenantId: str | None = None
    expectedVersion: int = Field(ge=0, strict=True)
    mutationId: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=1, max_length=500)
    lowStock: bool = Field(strict=True)
    expiry: bool = Field(strict=True)
    expiryDays: int = Field(ge=0, le=90, strict=True)

def dto(row):
    return {'lowStock': bool(row and row.low_stock), 'expiry': bool(row and row.expiry),
        'expiryDays': row.expiry_days if row else 7, 'version': row.version if row else 0}

@router.get('/config')
async def config(request: Request, session: AsyncSession = Depends(get_db_session)):
    return dto(await session.get(AlertConfig, current_tenant(request)))

@router.post('/config')
async def save(data: Config, request: Request, session: AsyncSession = Depends(get_db_session)):
    tenant, actor = current_tenant(request), request.state.user_id
    try:
        await locked_config(session, tenant)
        prior = await session.scalar(select(Action).where(Action.tenant_id == tenant, Action.mutation_id == 'alerts:' + data.mutationId))
        payload = data.model_dump(mode='json')
        if prior:
            if prior.actor_id != actor or prior.request != payload:
                raise HTTPException(409, 'Mutation ID belongs to another alert command')
            result = prior.result
            await session.rollback()
            return result
        row = await session.get(AlertConfig, tenant)
        if (row.version if row else 0) != data.expectedVersion:
            raise HTTPException(409, 'Alert configuration version changed')
        if not row:
            row = AlertConfig(tenant_id=tenant, version=0)
            session.add(row)
        row.low_stock, row.expiry, row.expiry_days, row.version = data.lowStock, data.expiry, data.expiryDays, row.version + 1
        await session.flush()
        evaluation = await session.run_sync(lambda sync: evaluate(sync.connection(), tenant))
        result = {**dto(row), **evaluation}
        session.add(Action(tenant_id=tenant, mutation_id='alerts:' + data.mutationId, actor_id=actor, request=payload, result=result))
        await session.commit()
        return result
    except Exception:
        await session.rollback()
        raise

@router.post('/evaluate')
async def run(request: Request, session: AsyncSession = Depends(get_db_session)):
    await locked_config(session, current_tenant(request))
    result = await session.run_sync(lambda sync: evaluate(sync.connection(), current_tenant(request)))
    await session.commit()
    return result

@router.get('')
async def conditions(request: Request, session: AsyncSession = Depends(get_db_session)):
    rows = await session.scalars(select(AlertCondition).where(AlertCondition.tenant_id == current_tenant(request), AlertCondition.active.is_(True)).order_by(AlertCondition.kind, AlertCondition.id))
    return [{'id': r.id, 'kind': r.kind, 'episode': r.episode, 'facts': r.facts, 'evaluatedAt': r.evaluated_at.isoformat()} for r in rows]
