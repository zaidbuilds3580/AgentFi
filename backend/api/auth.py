"""
Authentication endpoints: register, login, and current user inspection.
"""

from fastapi import APIRouter, Depends, HTTPException, status

from backend.api.deps import get_current_user
from backend.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    Token,
    TokenPayload,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=Token,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Register new user & tenant (Day 2 Skeleton)",
)
def register(request: RegisterRequest) -> Token:
    """
    User and tenant registration endpoint.
    Will be wired to Supabase users and businesses tables on Day 2.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="User registration endpoint will be wired to the database on Day 2.",
    )


@router.post(
    "/login",
    response_model=Token,
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    summary="Login user & issue JWT (Day 2 Skeleton)",
)
def login(request: LoginRequest) -> Token:
    """
    User login endpoint verifying bcrypt password and returning signed JWT.
    Will be wired to Supabase users table on Day 2.
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="User login endpoint will be wired to the database on Day 2.",
    )


@router.get(
    "/me",
    summary="Get current user claims from JWT",
)
def get_me(current_user: TokenPayload = Depends(get_current_user)) -> dict:
    """
    Validates the Bearer token and returns authenticated user claims.
    Enforces multi-tenancy context (sub, business_id, role).
    """
    return {
        "user_id": current_user.sub,
        "business_id": current_user.business_id,
        "role": current_user.role,
        "exp": current_user.exp,
        "iat": current_user.iat,
    }

