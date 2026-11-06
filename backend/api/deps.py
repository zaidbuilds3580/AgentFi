"""
FastAPI route dependencies: database sessions, user authentication, tenant isolation, and RBAC.
"""

from typing import Callable
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from backend.auth.schemas import TokenPayload
from backend.auth.security import decode_access_token
from backend.core.config import settings
from backend.database.session import get_db  # Re-exported for convenience

# OAuth2 scheme pointing to our login endpoint for Swagger UI authorization
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_PREFIX}/auth/login",
    auto_error=True,
)


def get_current_user(token: str = Depends(oauth2_scheme)) -> TokenPayload:
    """
    Validate incoming Bearer token and return the decoded token claims.
    Raises 401 if token is invalid or expired.
    """
    return decode_access_token(token)


def get_current_business_id(
    current_user: TokenPayload = Depends(get_current_user),
) -> str:
    """
    Extract the tenant business_id claim from the authenticated user token.
    Enforces tenant context for downstream service layers.
    """
    return current_user.business_id


def require_role(*allowed_roles: str) -> Callable[[TokenPayload], TokenPayload]:
    """
    Dependency factory to enforce Role-Based Access Control (RBAC).
    Example: Depends(require_role("owner", "admin"))
    """
    def role_checker(
        current_user: TokenPayload = Depends(get_current_user),
    ) -> TokenPayload:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation forbidden. Required role: one of {list(allowed_roles)} (current: '{current_user.role}')",
            )
        return current_user

    return role_checker

