from fastapi import APIRouter, HTTPException, Body, Request, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.infrastructure.database import get_db_session
from src.infrastructure.owned_feature_models import ReportDefinitionModel
from src.infrastructure.auth.identity import current_tenant

router = APIRouter(prefix='/reports', tags=['reports'])


def dto(report):
    return {**report.payload, 'id': report.id, 'tenant_id': report.tenant_id, 'created_by': report.created_by}


async def owned_report(session, request, report_id):
    report = await session.scalar(select(ReportDefinitionModel).where(
        ReportDefinitionModel.id == report_id, ReportDefinitionModel.tenant_id == current_tenant(request)))
    if not report:
        raise HTTPException(404, 'Report not found')
    return report


@router.post('/')
async def create_report(request: Request, payload: dict = Body(...), session: AsyncSession = Depends(get_db_session)):
    if not isinstance(payload.get('name'), str) or not payload['name'].strip():
        raise HTTPException(422, 'Report name is required')
    report = ReportDefinitionModel(tenant_id=current_tenant(request), created_by=request.state.user_id,
        payload={key: payload[key] for key in ('name', 'description', 'type', 'filters', 'grouping') if key in payload})
    session.add(report)
    await session.commit()
    return {'success': True, 'report': dto(report)}


@router.get('/')
async def list_reports(request: Request, session: AsyncSession = Depends(get_db_session)):
    reports = await session.scalars(select(ReportDefinitionModel).where(
        ReportDefinitionModel.tenant_id == current_tenant(request)))
    return {'reports': [dto(report) for report in reports]}


@router.post('/{id}/execute')
async def execute_report(id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    await owned_report(session, request, id)
    raise HTTPException(501, 'Durable report generation is not implemented')


@router.post('/{id}/schedule')
async def schedule_report(id: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    await owned_report(session, request, id)
    raise HTTPException(501, 'Durable report scheduling is not implemented')


@router.get('/shared/{token}')
async def get_shared_link(token: str):
    # No persisted, verified share grants exist yet.
    raise HTTPException(404, 'Report share not found')
