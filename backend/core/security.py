"""Security utilities for authentication and authorization."""
import os
from datetime import datetime, timedelta
from typing import Optional, Union

from jose import JWTError, jwt
from passlib.context import CryptContext

# Security configuration
_PLACEHOLDER = "your-secret-key-here-change-in-production"
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
if not SECRET_KEY or SECRET_KEY == _PLACEHOLDER:
    if os.getenv("TESTING") == "1":
        SECRET_KEY = "test-secret-key-for-testing"
    else:
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable must be set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480
REFRESH_TOKEN_EXPIRE_DAYS = 30

# Password hashing context
# 使用 bcrypt 并设置 truncate_error=False 以自动截断超过 72 字节的密码
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__truncate_error=False,
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hashed password."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Generate a hashed password."""
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token.

    Args:
        data: Data to encode in the token
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def create_refresh_token(data: dict) -> str:
    """Create a JWT refresh token with longer expiration.

    Args:
        data: Data to encode in the token

    Returns:
        Encoded JWT refresh token string
    """
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT token.

    Args:
        token: JWT token string

    Returns:
        Decoded token payload or None if invalid
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None
