import importlib
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

import src.infrastructure.database as db_module
from src.infrastructure.database import get_db_session


class DummyState:
    def __init__(self, tenant_id=None):
        self.tenant_id = tenant_id


class DummyRequest:
    def __init__(self, tenant_id=None):
        self.state = DummyState(tenant_id)


def test_database_url_not_set_raises_value_error(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL environment variable is not set"):
        if "src.infrastructure.database" in sys.modules:
            del sys.modules["src.infrastructure.database"]
        importlib.import_module("src.infrastructure.database")

    # Restore module in sys.modules for other tests
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    importlib.import_module("src.infrastructure.database")


def test_database_url_postgresql_conversion(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/mydb")
    with patch("sqlalchemy.ext.asyncio.create_async_engine") as mock_create_engine:
        if "src.infrastructure.database" in sys.modules:
            del sys.modules["src.infrastructure.database"]
        importlib.import_module("src.infrastructure.database")
        mock_create_engine.assert_called_once_with(
            "postgresql+asyncpg://user:pass@localhost:5432/mydb", echo=False
        )

    # Restore module state with standard test sqlite URL
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    if "src.infrastructure.database" in sys.modules:
        del sys.modules["src.infrastructure.database"]
    importlib.import_module("src.infrastructure.database")


@pytest.mark.asyncio
async def test_get_db_session_tenant_mismatch_raises_403():
    req = DummyRequest(tenant_id="tenant-a")
    with pytest.raises(HTTPException) as exc_info:
        async for _ in db_module.get_db_session(request=req, x_tenant_id="tenant-b"):
            pass
    assert exc_info.value.status_code == 403
    assert (
        exc_info.value.detail
        == "Tenant ID mismatch: You are not authorized to access this tenant's data."
    )


@pytest.mark.asyncio
async def test_get_db_session_header_without_user_tenant_raises_401():
    req = DummyRequest(tenant_id=None)
    with pytest.raises(HTTPException) as exc_info:
        async for _ in db_module.get_db_session(request=req, x_tenant_id="tenant-a"):
            pass
    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or missing token"


@pytest.mark.asyncio
async def test_get_db_session_matching_tenant_success():
    req = DummyRequest(tenant_id="tenant-a")
    sessions = []
    async for session in db_module.get_db_session(request=req, x_tenant_id="tenant-a"):
        sessions.append(session)
    assert len(sessions) == 1
    assert isinstance(sessions[0], AsyncSession)


@pytest.mark.asyncio
async def test_get_db_session_no_header_tenant_success():
    req = DummyRequest(tenant_id="tenant-a")
    sessions = []
    async for session in db_module.get_db_session(request=req, x_tenant_id=None):
        sessions.append(session)
    assert len(sessions) == 1
    assert isinstance(sessions[0], AsyncSession)


@pytest.mark.asyncio
async def test_get_db_session_unauthenticated_no_header_success():
    req = DummyRequest(tenant_id=None)
    sessions = []
    async for session in db_module.get_db_session(request=req, x_tenant_id=None):
        sessions.append(session)
    assert len(sessions) == 1
    assert isinstance(sessions[0], AsyncSession)


@pytest.mark.asyncio
async def test_get_db_session_postgresql_dialect_sets_tenant_config(monkeypatch):
    req = DummyRequest(tenant_id="tenant-postgres")

    mock_session = AsyncMock()
    mock_session.bind = MagicMock()
    mock_session.bind.dialect = MagicMock()
    mock_session.bind.dialect.name = "postgresql"
    mock_session.execute = AsyncMock()

    class MockAsyncSessionContext:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_sessionmaker = MagicMock(return_value=MockAsyncSessionContext())

    monkeypatch.setattr(db_module, "async_session", mock_sessionmaker)

    sessions = []
    async for session in db_module.get_db_session(request=req, x_tenant_id="tenant-postgres"):
        sessions.append(session)

    assert len(sessions) == 1
    assert sessions[0] is mock_session
    assert mock_session.execute.called
    call_args = mock_session.execute.call_args
    assert "SELECT set_config('app.current_tenant_id', :tenant, true)" in str(
        call_args[0][0]
    )
    assert call_args[0][1] == {"tenant": "tenant-postgres"}


@pytest.mark.asyncio
async def test_get_db_session_non_postgresql_dialect_skips_tenant_config(monkeypatch):
    req = DummyRequest(tenant_id="tenant-sqlite")

    mock_session = AsyncMock()
    mock_session.bind = MagicMock()
    mock_session.bind.dialect = MagicMock()
    mock_session.bind.dialect.name = "sqlite"
    mock_session.execute = AsyncMock()

    class MockAsyncSessionContext:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_sessionmaker = MagicMock(return_value=MockAsyncSessionContext())

    monkeypatch.setattr(db_module, "async_session", mock_sessionmaker)

    sessions = []
    async for session in db_module.get_db_session(request=req, x_tenant_id="tenant-sqlite"):
        sessions.append(session)

    assert len(sessions) == 1
    assert sessions[0] is mock_session
    mock_session.execute.assert_not_called()
