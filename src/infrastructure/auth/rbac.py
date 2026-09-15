import logging
from fastapi import Request, HTTPException, status
from typing import List, Callable, Any
from functools import wraps

logger = logging.getLogger(__name__)

class PermissionDeniedError(HTTPException):
    def __init__(self, detail: str):
        super().__init__(status_code=status.HTTP_403_FORBIDDEN, detail=detail)

class UnauthorizedError(HTTPException):
    def __init__(self, detail: str = "Invalid or missing token"):
        super().__init__(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

def get_current_user_roles(request: Request) -> List[str]:
    """
    Extracts the RBAC roles from the validated JWT token in the request state.
    Assumes authentication middleware has populated request.state.roles.
    """
    if not hasattr(request.state, "roles"):
        return []
    return request.state.roles

def get_current_user_permissions(request: Request) -> List[str]:
    """
    Extracts the granular permissions from the JWT claims.
    """
    if not hasattr(request.state, "permissions"):
        return []
    return request.state.permissions

def requires_roles(required_roles: List[str]):
    """
    Decorator to enforce Role-Based Access Control (RBAC) on FastAPI endpoints.
    Requires the user to have at least one of the specified roles.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, request: Request, **kwargs) -> Any:
            user_roles = get_current_user_roles(request)
            if not user_roles:
                raise UnauthorizedError()
                
            has_role = any(role in required_roles for role in user_roles)
            if not has_role:
                logger.warning(f"RBAC Denied: User roles {user_roles} lack required roles {required_roles}")
                raise PermissionDeniedError("You do not have the required role to access this resource.")
                
            return await func(*args, request=request, **kwargs)
        return wrapper
    return decorator

def requires_permissions(required_permissions: List[str]):
    """
    Decorator to enforce Granular Permission Matrix control.
    Requires the user to have all of the specified permissions.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, request: Request, **kwargs) -> Any:
            user_perms = get_current_user_permissions(request)
            if not user_perms:
                raise UnauthorizedError()
                
            has_all_perms = all(perm in user_perms for perm in required_permissions)
            if not has_all_perms:
                missing = [p for p in required_permissions if p not in user_perms]
                logger.warning(f"Permission Denied: Missing permissions {missing}")
                raise PermissionDeniedError(f"Missing required permissions: {', '.join(missing)}")
                
            return await func(*args, request=request, **kwargs)
        return wrapper
    return decorator
