"""
Authentication endpoints: register, login, and current user inspection wired to Supabase PostgreSQL.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, get_db
from backend.auth.schemas import (
    LoginRequest,
    RegisterRequest,
    Token,
    TokenPayload,
    UserPublic,
)
from backend.services.auth_service import (
    authenticate_user,
    get_current_user_profile,
    register_user_and_business,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register new SME business tenant & owner user",
)
def register(
    request: RegisterRequest,
    db: Session = Depends(get_db),
) -> UserPublic:
    """
    Atomically creates a new business tenant and its initial 'owner' user.
    Enforces minimum 8-character password and lower(email) uniqueness.
    """
    return register_user_and_business(db=db, request=request)


@router.post(
    "/login",
    response_model=Token,
    status_code=status.HTTP_200_OK,
    summary="Login user & issue signed JWT",
)
def login(
    request: LoginRequest,
    db: Session = Depends(get_db),
) -> Token:
    """
    Verifies user credentials against the database, updates last_login_at,
    and returns a signed multi-tenant JWT access token.
    """
    return authenticate_user(db=db, request=request)


@router.get(
    "/me",
    summary="Get current user claims & profile from JWT",
)
def get_me(
    current_user: TokenPayload = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Validates Bearer token and returns authenticated user claims, full name,
    email, and business tenant name.
    """
    return get_current_user_profile(db=db, current_user=current_user)
