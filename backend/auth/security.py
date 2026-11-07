"""
Security utilities: password hashing with bcrypt and JWT creation/validation with python-jose.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import bcrypt
from fastapi import HTTPException, status
from jose import ExpiredSignatureError, JWTError, jwt

from backend.auth.schemas import TokenPayload
from backend.core.config import settings


def hash_password(password: str) -> str:
    """
    Hash a plaintext password using bcrypt with automatic salt generation.
    Truncates to 72 bytes to adhere to bcrypt standard limits safely.
    """
    password_bytes = password.encode("utf-8")[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plaintext password against a bcrypt hash.
    """
    try:
        plain_bytes = plain_password.encode("utf-8")[:72]
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(plain_bytes, hashed_bytes)
    except Exception:
        return False


def create_access_token(
    subject: str,
    business_id: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a signed JWT access token containing subject, tenant ID, and role.
    """
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    claims = {
        "sub": str(subject),
        "business_id": str(business_id),
        "role": str(role),
        "exp": int(expire.timestamp()),
        "iat": int(now.timestamp()),
    }

    return jwt.encode(
        claims,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> TokenPayload:
    """
    Decode and validate a JWT access token.
    Raises clean HTTP 401 on expired, malformed, or missing required claims.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    expired_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Token has expired",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        sub: Optional[str] = payload.get("sub")
        business_id: Optional[str] = payload.get("business_id")
        role: Optional[str] = payload.get("role")
        exp: Optional[int] = payload.get("exp")
        iat: Optional[int] = payload.get("iat")

        if not sub or not business_id or not role or exp is None or iat is None:
            raise credentials_exception

        return TokenPayload(
            sub=sub,
            business_id=business_id,
            role=role,
            exp=exp,
            iat=iat,
        )
    except ExpiredSignatureError:
        raise expired_exception
    except JWTError:
        raise credentials_exception

