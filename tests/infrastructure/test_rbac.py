import pytest
from fastapi import Request
from unittest.mock import MagicMock, AsyncMock
from src.infrastructure.auth.rbac import (
    get_current_user_roles,
    get_current_user_permissions,
    requires_roles,
    requires_permissions,
    PermissionDeniedError,
    UnauthorizedError,
)

@pytest.fixture
def mock_request():
    request = MagicMock(spec=Request)
    request.state = MagicMock()
    # Delete the attributes so hasattr returns False by default
    del request.state.roles
    del request.state.permissions
    return request

def test_get_current_user_roles_empty(mock_request):
    roles = get_current_user_roles(mock_request)
    assert roles == []

def test_get_current_user_roles_populated(mock_request):
    mock_request.state.roles = ["admin", "warehouse_operator"]
    roles = get_current_user_roles(mock_request)
    assert roles == ["admin", "warehouse_operator"]

def test_get_current_user_permissions_empty(mock_request):
    perms = get_current_user_permissions(mock_request)
    assert perms == []

def test_get_current_user_permissions_populated(mock_request):
    mock_request.state.permissions = ["inventory:read", "inventory:write"]
    perms = get_current_user_permissions(mock_request)
    assert perms == ["inventory:read", "inventory:write"]

@pytest.mark.asyncio
async def test_requires_roles_success(mock_request):
    mock_request.state.roles = ["warehouse_operator"]
    
    @requires_roles(["admin", "warehouse_operator"])
    async def dummy_endpoint(request: Request):
        return "success"
    
    result = await dummy_endpoint(request=mock_request)
    assert result == "success"

@pytest.mark.asyncio
async def test_requires_roles_unauthorized(mock_request):
    mock_request.state.roles = []
    
    @requires_roles(["admin"])
    async def dummy_endpoint(request: Request):
        return "success"
    
    with pytest.raises(UnauthorizedError) as exc_info:
        await dummy_endpoint(request=mock_request)
    assert exc_info.value.status_code == 401

@pytest.mark.asyncio
async def test_requires_roles_forbidden(mock_request):
    mock_request.state.roles = ["read_only"]
    
    @requires_roles(["admin", "warehouse_operator"])
    async def dummy_endpoint(request: Request):
        return "success"
    
    with pytest.raises(PermissionDeniedError) as exc_info:
        await dummy_endpoint(request=mock_request)
    assert exc_info.value.status_code == 403

@pytest.mark.asyncio
async def test_requires_permissions_success(mock_request):
    mock_request.state.permissions = ["inventory:read", "inventory:write", "users:read"]
    
    @requires_permissions(["inventory:read", "inventory:write"])
    async def dummy_endpoint(request: Request):
        return "success"
    
    result = await dummy_endpoint(request=mock_request)
    assert result == "success"

@pytest.mark.asyncio
async def test_requires_permissions_unauthorized(mock_request):
    mock_request.state.permissions = []
    
    @requires_permissions(["inventory:read"])
    async def dummy_endpoint(request: Request):
        return "success"
    
    with pytest.raises(UnauthorizedError) as exc_info:
        await dummy_endpoint(request=mock_request)
    assert exc_info.value.status_code == 401

@pytest.mark.asyncio
async def test_requires_permissions_forbidden(mock_request):
    mock_request.state.permissions = ["inventory:read"]
    
    @requires_permissions(["inventory:read", "inventory:write"])
    async def dummy_endpoint(request: Request):
        return "success"
    
    with pytest.raises(PermissionDeniedError) as exc_info:
        await dummy_endpoint(request=mock_request)
    assert exc_info.value.status_code == 403
    assert "inventory:write" in exc_info.value.detail
