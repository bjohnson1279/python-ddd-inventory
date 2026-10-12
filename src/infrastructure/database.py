from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from fastapi import Header, Request, HTTPException, status
import os

# Security: Do not add fallback default credentials here to prevent hardcoded secrets
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable is not set")
if DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+asyncpg://', 1)

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

    if x_tenant_id and not user_tenant:
        raise HTTPException(status_code=401, detail='Invalid or missing token')
    # Explicit row scopes are enforced by repositories. Do not redirect models
    # into unprovisioned schemas or leak a session setting through pooled connections.
    async with async_session() as session:
        yield session
