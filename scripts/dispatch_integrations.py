"""Run one bounded delivery pass for an explicitly configured tenant.

Schedule externally; use the ordinary restricted runtime database login.
"""
import asyncio
import os
from sqlalchemy import select
from src.infrastructure.database import async_session
from src.infrastructure.integration_models import IntegrationConnection
from src.application.integration_service import dispatch_head
from src.application.workers.outbox_worker import set_worker_tenant


async def main():
    tenant = os.environ['INTEGRATION_WORKER_TENANT']
    if not tenant:
        raise ValueError('INTEGRATION_WORKER_TENANT is required')
    async with async_session() as session:
        if session.bind.dialect.name == 'postgresql':
            await set_worker_tenant(session, tenant)
        ids = list(await session.scalars(select(IntegrationConnection.id).where(IntegrationConnection.tenant_id == tenant,
            IntegrationConnection.provider == 'EVENT_SINK', IntegrationConnection.active.is_(True)).limit(100)))
        await session.rollback()
    for identifier in ids:
        async with async_session() as session:
            if session.bind.dialect.name == 'postgresql':
                await set_worker_tenant(session, tenant)
            await dispatch_head(session, tenant, identifier)


if __name__ == '__main__':
    asyncio.run(main())
