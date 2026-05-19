"""Security utilities for authentication and authorization."""
import os
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Optional, Union

import jwt
from jwt import InvalidTokenError
from passlib.context import CryptContext
from starlette.responses import Response

# Security configuration
_PLACEHOLDER = "your-secret-key-here-change-in-production"
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
if not SECRET_KEY or SECRET_KEY == _PLACEHOLDER:
    if os.getenv("TESTING") == "1":
        SECRET_KEY = "test-secret-key-for-testing-32-bytes-ok"
    else:
        raise RuntimeError(
            "JWT_SECRET_KEY environment variable must be set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480
REFRESH_TOKEN_EXPIRE_DAYS = 30
ACCESS_COOKIE_NAME = os.getenv("AUTH_ACCESS_COOKIE_NAME", "stp_access_token")
REFRESH_COOKIE_NAME = os.getenv("AUTH_REFRESH_COOKIE_NAME", "stp_refresh_token")
AUTH_COOKIE_PATH = os.getenv("AUTH_COOKIE_PATH", "/")

# Password hashing context
# 使用 bcrypt 并设置 truncate_error=False 以自动截断超过 72 字节的密码
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__truncate_error=False,
)


def is_auth_cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "0") == "1"


def _get_cookie_samesite() -> str:
    cookie_samesite = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if cookie_samesite not in {"lax", "strict", "none"}:
        return "lax"
    return cookie_samesite


def validate_production_auth_cookie_settings() -> None:
    if os.getenv("ENV", "").strip().lower() != "production":
        return
    if not is_auth_cookie_secure():
        raise RuntimeError("AUTH_COOKIE_SECURE=1 required when ENV=production")
    if _get_cookie_samesite() == "none":
        raise RuntimeError(
            "AUTH_COOKIE_SAMESITE=none is not supported in production without CSRF protection"
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
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
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
    expire = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
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
    except InvalidTokenError:
        return None


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE_NAME,
        access_token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=is_auth_cookie_secure(),
        samesite=_get_cookie_samesite(),
        path=AUTH_COOKIE_PATH,
    )
    response.set_cookie(
        REFRESH_COOKIE_NAME,
        refresh_token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=is_auth_cookie_secure(),
        samesite=_get_cookie_samesite(),
        path=AUTH_COOKIE_PATH,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        ACCESS_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        secure=is_auth_cookie_secure(),
        samesite=_get_cookie_samesite(),
        httponly=True,
    )
    response.delete_cookie(
        REFRESH_COOKIE_NAME,
        path=AUTH_COOKIE_PATH,
        secure=is_auth_cookie_secure(),
        samesite=_get_cookie_samesite(),
        httponly=True,
    )


def extract_cookie_token(cookie_header: str | None, cookie_name: str) -> Optional[str]:
    if not cookie_header:
        return None
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    morsel = cookie.get(cookie_name)
    if morsel is None:
        return None
    return morsel.value or None
