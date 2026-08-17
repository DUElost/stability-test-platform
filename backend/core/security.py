"""Security utilities for authentication and authorization."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from typing import Annotated, Optional

import bcrypt
import jwt
from jwt import InvalidTokenError
from pydantic import AfterValidator, StringConstraints
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

# 生产类环境判定:production 与 internal 都视为生产(#281 P0)。
# internal 是既有生产部署使用的环境标识(仓库根 .env.backend 为 ENV=internal);
# 此前护栏只认 production,导致 internal 部署绕过安全 Cookie/CSRF、注册策略
# 与匿名 SocketIO 的全部护栏。唯一例外:AUTH_COOKIE_SECURE=1 强制仅限
# ENV=production(internal = 无 TLS 内网部署,见
# validate_production_auth_cookie_settings 注释),其余护栏两者同等强制。
PRODUCTION_LIKE_ENVS = frozenset({"production", "internal"})


def is_production_like_env() -> bool:
    """是否生产类环境(ENV=production 或 ENV=internal)。"""
    return os.getenv("ENV", "").strip().lower() in PRODUCTION_LIKE_ENVS


def is_auth_cookie_secure() -> bool:
    return os.getenv("AUTH_COOKIE_SECURE", "0") == "1"


def _get_cookie_samesite() -> str:
    cookie_samesite = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if cookie_samesite not in {"lax", "strict", "none"}:
        return "lax"
    return cookie_samesite


def is_public_register_allowed() -> bool:
    """Whether ``POST /auth/register`` is permitted.

    Blocked in production-like environments (``ENV=production/internal``,
    unless ``STP_ALLOW_REGISTER=1``) or when ``STP_ALLOW_REGISTER=0`` in any
    environment.
    """
    allow_raw = os.getenv("STP_ALLOW_REGISTER", "").strip().lower()
    if allow_raw in {"0", "false", "no", "off"}:
        return False
    if allow_raw in {"1", "true", "yes", "on"}:
        return True
    return not is_production_like_env()


def validate_production_auth_cookie_settings() -> None:
    if not is_production_like_env():
        return
    env = os.getenv("ENV", "").strip().lower()
    # AUTH_COOKIE_SECURE 强制仅限 ENV=production(#281 部署决策,操作者选定):
    # ENV=internal 是「无 TLS 内网部署」标识——Secure cookie 在纯 HTTP 下
    # 被浏览器直接拒绝(不发送),强制它等于必然拒启且无安全收益。internal
    # 保留其余全部生产级护栏:CSRF 强制、SameSite 取值校验与 none 拒绝、
    # 注册默认关闭、STP_SKIP_INFRA_CHECK 禁用、SocketIO 强制认证。
    # 上 TLS 后应将 AUTH_COOKIE_SECURE 置 1,届时再议是否收紧。
    if env == "production" and not is_auth_cookie_secure():
        raise RuntimeError("AUTH_COOKIE_SECURE=1 required when ENV=production")
    # 校验原始环境变量(#281 CR Minor):无效显式值不得被 _get_cookie_samesite
    # 静默回落为 lax 而通过启动校验。
    samesite_raw = os.getenv("AUTH_COOKIE_SAMESITE", "lax").strip().lower()
    if samesite_raw not in {"lax", "strict", "none"}:
        raise RuntimeError(
            f"AUTH_COOKIE_SAMESITE={samesite_raw!r} is invalid; use lax or strict"
        )
    if _get_cookie_samesite() == "none":
        raise RuntimeError(
            "AUTH_COOKIE_SAMESITE=none is not supported in production without CSRF protection"
        )
    csrf_raw = os.getenv("STP_CSRF_ENABLED", "1").strip().lower()
    if csrf_raw in {"0", "false", "no", "off"}:
        raise RuntimeError(
            "STP_CSRF_ENABLED must remain enabled in production-like environments"
        )


# bcrypt 只使用前 72 字节;与旧 passlib 配置(truncate_error=False)语义一致,
# 存量 $2b$ 哈希可直接校验。passlib 1.7.4 读取新版 bcrypt 版本号会触发
# __about__ 缺失警告,这里直连 bcrypt 不再依赖 passlib(依赖清单暂保留
# passlib 固定版本,待 py3.11 下重建 lock 时清理)。
_BCRYPT_MAX_BYTES = 72


def _bcrypt_bytes(password: str) -> bytes:
    # 保持 [:72] 切片仅作为「存量哈希/历史数据」的兼容兜底;#281 CR Major:
    # 新密码入口(注册/建户/改密)一律经 PasswordStr 在哈希前拒绝
    # >72 UTF-8 字节的密码,不再允许静默截断产生等价哈希。
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def _bcrypt_compatible_password(value: str) -> str:
    """密码入口校验(#281 CR Major):拒绝超过 72 个 UTF-8 字节的密码。

    8–128 字符校验允许两个不同密码(如 71 个 ASCII + 1 个多字节字符 vs
    72 个 ASCII)在 bcrypt 截断后产生同一输入;静默截断使「不同密码同一
    哈希」成为可能。校验在哈希之前进行,超限走 Pydantic 422。
    """
    if len(value.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise ValueError(
            "password must not exceed 72 bytes when UTF-8 encoded (bcrypt limit)"
        )
    return value


# 所有密码入口(register / 用户 CRUD / 改密)共用的密码字段类型。
PasswordStr = Annotated[
    str,
    StringConstraints(min_length=8, max_length=128),
    AfterValidator(_bcrypt_compatible_password),
]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    try:
        return bcrypt.checkpw(_bcrypt_bytes(plain_password), hashed_password.encode("utf-8"))
    except ValueError:
        # 非法盐/哈希(如非 bcrypt 字符串)一律视为校验失败,不抛异常
        return False


def get_password_hash(password: str) -> str:
    """Generate a bcrypt password hash."""
    return bcrypt.hashpw(_bcrypt_bytes(password), bcrypt.gensalt()).decode("utf-8")


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
    to_encode.update({"exp": expire, "type": "access", "jti": uuid.uuid4().hex})
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
    to_encode.update({"exp": expire, "type": "refresh", "jti": uuid.uuid4().hex})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(
    token: str,
    *,
    expected_type: Optional[str] = None,
) -> Optional[dict]:
    """Decode and validate a JWT token.

    Args:
        token: JWT token string
        expected_type: When provided (e.g. "access" / "refresh"), payloads whose
            type claim does not match are rejected. ADR-0024 P0 fix: without
            this guard a refresh token could be replayed as an access token,
            bypassing the logout blacklist (which is consulted only at the
            /auth/refresh endpoint).

    Returns:
        Decoded token payload or None if invalid / type mismatch.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except InvalidTokenError:
        return None
    if expected_type is not None and payload.get("type") != expected_type:
        return None
    return payload


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
