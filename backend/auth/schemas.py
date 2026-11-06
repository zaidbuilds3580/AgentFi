"""
Authentication and user-related Pydantic schemas.
"""

from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class Token(BaseModel):
    """
    Standard OAuth2 / Bearer token response.
    """
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """
    Decoded JWT claims payload.
    Enforces multi-tenancy (business_id) and RBAC (role).
    """
    sub: str = Field(description="Subject identifier (user UUID)")
    business_id: str = Field(description="Tenant business UUID")
    role: str = Field(description="User role: owner, admin, or viewer")
    exp: int = Field(description="Expiration timestamp (epoch)")
    iat: int = Field(description="Issued at timestamp (epoch)")


class LoginRequest(BaseModel):
    """
    User login request body.
    """
    email: EmailStr
    password: str = Field(min_length=8)


class RegisterRequest(BaseModel):
    """
    User registration request body (initial tenant creation).
    """
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: Optional[str] = None
    business_name: str = Field(min_length=2)
    role: str = Field(default="owner", pattern="^(owner|admin|viewer)$")


class UserPublic(BaseModel):
    """
    Public user profile representation.
    """
    id: str
    email: EmailStr
    full_name: Optional[str] = None
    business_id: str
    role: str

