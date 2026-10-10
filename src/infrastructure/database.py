from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import text
from fastapi import Header, Request, HTTPException, status
import os

# Security: Do not add fallback default credentials here to prevent hardcoded secrets
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

async def get_db_session(request: Request, x_tenant_id: str = Header(default=None)):
    # Mitigate Tenant Authorization Bypass (IDOR)
    user_tenant = getattr(request.state, "tenant_id", None)

    if x_tenant_id and user_tenant and x_tenant_id != user_tenant:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant ID mismatch: You are not authorized to access this tenant's data."
        )

    active_tenant = x_tenant_id or user_tenant

    # Implement Dynamic Multi-Database Tenant Provisioning (SaaS) via schema partitioning
    options = {}
    if active_tenant:
        options["schema_translate_map"] = {None: f"tenant_{active_tenant}"}
        
    async with async_session(**({"execution_options": options} if options else {})) as session:
        if active_tenant:
            # Enforce Row-Level Security via Postgres session configuration for shared tables
            await session.execute(text("SELECT set_config('rls.tenant_id', :tenant, false)"), {"tenant": active_tenant})
        yield session
