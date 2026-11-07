"""
Unit tests for security routines: bcrypt password hashing and JWT token handling.
"""

from datetime import timedelta
import pytest
from fastapi import HTTPException

from backend.auth.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
)
from backend.core.config import Settings


def test_password_hash_and_verify_roundtrip():
    """
    Test that passwords hash correctly and verify accurately.
    """
    password = "SuperSecretPassword123!"
    hashed = hash_password(password)

    assert hashed != password
    assert hashed.startswith("$2b$")
    assert verify_password(password, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_token_creation_and_decoding():
    """
    Test creating an access token and successfully decoding its claims.
    """
    sub = "user-uuid-123"
    business_id = "biz-uuid-456"
    role = "owner"

    token = create_access_token(
        subject=sub,
        business_id=business_id,
        role=role,
        expires_delta=timedelta(minutes=15),
    )
    assert isinstance(token, str)

    payload = decode_access_token(token)
    assert payload.sub == sub
    assert payload.business_id == business_id
    assert payload.role == role
    assert payload.exp > payload.iat


def test_expired_token_rejected():
    """
    Expired tokens must raise a 401 HTTPException with detail 'Token has expired'.
    """
    expired_token = create_access_token(
        subject="user-uuid-123",
        business_id="biz-uuid-456",
        role="viewer",
        expires_delta=timedelta(seconds=-10),
    )

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(expired_token)

    assert exc_info.value.status_code == 401
    assert "Token has expired" in exc_info.value.detail


def test_tampered_token_rejected():
    """
    Tokens with altered payload or signature must raise 401 HTTPException.
    """
    valid_token = create_access_token(
        subject="user-123",
        business_id="biz-456",
        role="admin",
    )
    # Alter the last 4 characters of the signature
    tampered_token = valid_token[:-4] + "wxyz"

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(tampered_token)

    assert exc_info.value.status_code == 401
    assert "Could not validate credentials" in exc_info.value.detail


def test_production_guard_rejects_placeholder_secret():
    """
    Refuse to initialize settings in production if JWT_SECRET_KEY is insecure.
    """
    with pytest.raises(ValueError, match="FATAL SECURITY VIOLATION"):
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="CHANGE_ME_IN_PRODUCTION",
            DATABASE_URL="postgresql+psycopg://user:pass@host:5432/db",
        )

