import re
import uuid
from typing import Literal
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from src.infrastructure.database import get_db_session
from src.infrastructure.auth.identity import current_tenant
from src.infrastructure.integration_models import IntegrationConnection as Connection, IntegrationDelivery as Delivery, IntegrationOrder as Order
from src.infrastructure.transfer_models import InventoryActionModel as Action
from src.application.costing_service import locked_config
from src.application.stock_service import StockConflict, StockService
from src.application.integration_service import setting, endpoint, allowed_settings, owned, connection_dto, delivery_dto, ingest, dispatch_head

router = APIRouter(prefix='/integrations', tags=['integrations'])
webhooks = APIRouter(prefix='/webhooks/integrations', tags=['signed integrations'])


class Command(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tenantId: str | None = None
    mutationId: str = Field(min_length=1, max_length=100, pattern=r'\S')
    reason: str = Field(min_length=1, max_length=500, pattern=r'\S')


class CreateConnection(Command):
    name: str = Field(min_length=1, max_length=100, pattern=r'\S')
    provider: Literal['SHOPIFY', 'WOOCOMMERCE', 'EVENT_SINK']
    origin: str | None = Field(default=None, max_length=200)
    locationId: str = Field(default='default', min_length=1, max_length=100)
    secretRef: str = Field(max_length=112)
    endpointRef: str | None = Field(default=None, max_length=112)
    topics: list[Literal['StockChanged', 'JournalEntryPosted']] = Field(default_factory=list, max_length=2)


class Versioned(Command):
    expectedVersion: int = Field(ge=0, strict=True)


class Toggle(Versioned):
    active: bool = Field(strict=True)


async def command(request, session, data, operation, identifier=None):
    tenant = current_tenant(request)
    payload = {'operation': operation, 'id': identifier, **data.model_dump(mode='json')}
    key = 'integration:' + data.mutationId
    try:
        config = await locked_config(session, tenant)
        target = await owned(session, Delivery if operation == 'retry' else Order if operation in ('fulfill', 'cancel') else Connection, tenant, identifier) if identifier else None
        prior = await session.scalar(select(Action).where(Action.tenant_id == tenant, Action.mutation_id == key))
        if prior:
            if prior.actor_id != request.state.user_id or prior.request != payload:
                raise StockConflict('Mutation ID belongs to another integration command')
            result = prior.result
            await session.rollback()
            return result
        if operation == 'create':
            allowed_settings(tenant, [data.secretRef, data.endpointRef])
            setting(data.secretRef, secret=True)
            if data.provider == 'EVENT_SINK':
                endpoint(data.endpointRef)
                if not data.topics or data.origin:
                    raise ValueError('Event destinations require topics and no shop origin')
                if 'JournalEntryPosted' in data.topics and 'finance:read' not in request.state.permissions:
                    raise HTTPException(403, 'Financial permission required for journal exports')
            else:
                if not data.origin or data.endpointRef or data.topics:
                    raise ValueError('Inbound shops require an exact origin and no outbound settings')
                if data.provider == 'SHOPIFY' and not re.fullmatch(r'[a-z0-9][a-z0-9-]*\.myshopify\.com', data.origin):
                    raise ValueError('Use the canonical lowercase myshopify.com shop domain')
                if data.provider == 'WOOCOMMERCE':
                    from urllib.parse import urlsplit
                    parts = urlsplit(data.origin)
                    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                        raise ValueError('WooCommerce origin must be a canonical HTTPS store URL')
            target = Connection(id=str(uuid.uuid4()), tenant_id=tenant, name=data.name, provider=data.provider,
                origin=data.origin, location_id=data.locationId, secret_ref=data.secretRef,
                endpoint_ref=data.endpointRef, topics=list(dict.fromkeys(data.topics)), active=True, version=0, last_number=0)
            import hashlib
            target.secret_digest = hashlib.sha256(setting(data.secretRef, secret=True).encode()).hexdigest()
            session.add(target)
            await session.flush()
            result = connection_dto(target)
        else:
            if target.version != data.expectedVersion:
                raise StockConflict('Integration record version changed')
            if operation in ('fulfill', 'cancel'):
                if target.status != 'RESERVED':
                    raise StockConflict('Only a reserved integration order can be fulfilled or cancelled')
                connection = await owned(session, Connection, tenant, target.connection_id)
                # Paused shops can still finish or release already reserved orders.
                for index, line in enumerate(target.request['lines']):
                    await StockService(session, tenant, request.state.user_id).mutate(
                        'fulfill-allocation' if operation == 'fulfill' else 'release-allocation', line['sku'], connection.location_id,
                        line['quantity'], mutation_id='channel-command:' + target.id + ':' + operation + ':' + str(index),
                        commit=False, config=config, reference={'integrationId': connection.id, 'externalOrderId': target.external_id})
                target.status = 'FULFILLED' if operation == 'fulfill' else 'CANCELLED'
                target.version += 1
                result = {**target.result, 'status': target.status, 'version': target.version}
            elif operation == 'toggle':
                target.active = data.active
                target.version += 1
                result = connection_dto(target)
            else:
                if target.status != 'FAILED':
                    raise StockConflict('Only failed deliveries can be explicitly retried')
                target.status, target.last_error, target.next_attempt_at = 'PENDING', None, None
                target.cycle_attempts = 0
                target.version += 1
                result = delivery_dto(target)
        session.add(Action(tenant_id=tenant, mutation_id=key, actor_id=request.state.user_id, request=payload, result=result))
        await session.commit()
        return result
    except LookupError as exc:
        await session.rollback()
        raise HTTPException(404, str(exc)) from exc
    except (StockConflict, IntegrityError) as exc:
        await session.rollback()
        raise HTTPException(409, 'Duplicate or stale integration command' if isinstance(exc, IntegrityError) else str(exc)) from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get('/connections')
async def connections(request: Request, session: AsyncSession = Depends(get_db_session)):
    return [connection_dto(row) for row in await session.scalars(select(Connection).where(Connection.tenant_id == current_tenant(request)).order_by(Connection.name, Connection.id))]


@router.post('/connections', status_code=201)
async def create(data: CreateConnection, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data, 'create')


@router.post('/connections/{identifier}/active')
async def toggle(identifier: str, data: Toggle, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data, 'toggle', identifier)


@router.get('/orders')
async def orders(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    return [{**row.result, 'status': row.status, 'version': row.version} for row in await session.scalars(select(Order).where(Order.tenant_id == current_tenant(request)).order_by(Order.created_at, Order.id).limit(limit).offset(offset))]


@router.post('/orders/{identifier}/fulfill')
async def fulfill_order(identifier: str, data: Versioned, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data, 'fulfill', identifier)


@router.post('/orders/{identifier}/cancel')
async def cancel_order(identifier: str, data: Versioned, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data, 'cancel', identifier)


@router.get('/deliveries')
async def deliveries(request: Request, limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0), session: AsyncSession = Depends(get_db_session)):
    return [delivery_dto(row) for row in await session.scalars(select(Delivery).where(Delivery.tenant_id == current_tenant(request)).order_by(Delivery.connection_id, Delivery.number).limit(limit).offset(offset))]


@router.post('/deliveries/{identifier}/retry')
async def retry(identifier: str, data: Versioned, request: Request, session: AsyncSession = Depends(get_db_session)):
    return await command(request, session, data, 'retry', identifier)


@router.post('/connections/{identifier}/dispatch')
async def dispatch(identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    try:
        return {'delivery': await dispatch_head(session, current_tenant(request), identifier)}
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/amazon/connect')
@router.post('/woocommerce/connect')
async def legacy_connect():
    raise HTTPException(501, 'Register a signed inbound connection; direct provider credential/OAuth setup is not implemented')


@webhooks.post('/{tenant}/{identifier}/orders')
async def order(tenant: str, identifier: str, request: Request, session: AsyncSession = Depends(get_db_session)):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 256 * 1024:
            raise HTTPException(413, 'Webhook body exceeds the supported limit')
    try:
        return await ingest(session, tenant, identifier, bytes(raw), request.headers)
    except (LookupError, PermissionError) as exc:
        await session.rollback()
        raise HTTPException(401, 'Invalid webhook connection or signature') from exc
    except StockConflict as exc:
        await session.rollback()
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, 'Concurrent order conflict; retry the original delivery') from exc
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        await session.rollback()
        raise
