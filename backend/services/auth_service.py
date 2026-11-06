"""
Authentication and registration business logic services.
"""

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth.schemas import LoginRequest, RegisterRequest, Token, TokenPayload, UserPublic
from backend.auth.security import create_access_token, hash_password, verify_password
from backend.models.businesses import Business
from backend.models.users import User


def register_user_and_business(db: Session, request: RegisterRequest) -> UserPublic:
    """
    Atomically creates a new Business tenant and its initial Owner user.
    Enforces uniqueness on email (case-insensitive) and returns UserPublic DTO.
    """
    normalized_email = request.email.lower()

    # Check for existing user with duplicate email
    existing_user = db.query(User).filter(func.lower(User.email) == normalized_email).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email address is already registered.",
        )

    try:
        # Create Business tenant
        business = Business(
            name=request.business_name,
            is_active=True,
        )
        db.add(business)
        db.flush()  # Generates business.id for user foreign key

        # Create initial owner user
        hashed_password = hash_password(request.password)
        user = User(
            business_id=business.id,
            email=normalized_email,
            password_hash=hashed_password,
            full_name=request.full_name,
            role="owner",
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        return UserPublic(
            id=str(user.id),
            email=user.email,
            full_name=user.full_name,
            business_id=str(user.business_id),
            role=user.role,
        )
    except Exception:
        db.rollback()
        raise


def authenticate_user(db: Session, request: LoginRequest) -> Token:
    """
    Authenticates user credentials against the database.
    Updates last_login_at timestamp and returns a signed multi-tenant JWT.
    """
    normalized_email = request.email.lower()
    user = db.query(User).filter(func.lower(User.email) == normalized_email).first()

    # Generic 401 response prevents user enumeration attacks
    auth_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not user or not user.is_active:
        raise auth_exception

    if not verify_password(request.password, user.password_hash):
        raise auth_exception

    # Update last_login_at
    user.last_login_at = func.now()
    db.commit()

    access_token = create_access_token(
        subject=str(user.id),
        business_id=str(user.business_id),
        role=user.role,
    )

    return Token(access_token=access_token, token_type="bearer")


def get_current_user_profile(db: Session, current_user: TokenPayload) -> dict:
    """
    Retrieves full user and business profile for the authenticated token.
    Preserves all Day 1 response fields while adding user name and business name.
    """
    response = {
        "user_id": current_user.sub,
        "business_id": current_user.business_id,
        "role": current_user.role,
        "exp": current_user.exp,
        "iat": current_user.iat,
        "full_name": None,
        "email": None,
        "business_name": None,
    }

    try:
        user = db.query(User).filter(User.id == current_user.sub).first()
        if user:
            response["full_name"] = user.full_name
            response["email"] = user.email
            business = db.query(Business).filter(Business.id == user.business_id).first()
            if business:
                response["business_name"] = business.name
    except Exception:
        pass

    return response
