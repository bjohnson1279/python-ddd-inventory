from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy import text
from fastapi import Header
import os

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

async def get_db_session(x_tenant_id: str = Header(default=None)):
    # Implement Dynamic Multi-Database Tenant Provisioning (SaaS) via schema partitioning
    options = {}
    if x_tenant_id:
        options["schema_translate_map"] = {None: f"tenant_{x_tenant_id}"}
        
    async with async_session(**({"execution_options": options} if options else {})) as session:
        if x_tenant_id:
            # Enforce Row-Level Security via Postgres session configuration for shared tables
            await session.execute(text("SELECT set_config('rls.tenant_id', :tenant, false)"), {"tenant": x_tenant_id})
        yield session
