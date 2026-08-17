"""Authentication API routes."""
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.core.login_lockout import AccountLocked, InvalidCredentials, guarded_attempt
from backend.core.security import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    is_public_register_allowed,
    set_auth_cookies,
    verify_password,
)
from backend.models.user import User
from backend.services.token_blacklist import is_revoked, revoke

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def verify_agent_secret(x_agent_secret: Optional[str] = Header(None)) -> bool:
    """Verify agent secret for callback endpoints.

    secrets.compare_digest 防时序攻击。
    """
    try:
        expected = require_agent_secret()
    except AgentSecretNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    provided = x_agent_secret or ""
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid agent secret",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True


class UserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    # role is intentionally excluded to prevent privilege escalation
    # new users are always created with "user" role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SessionOut(BaseModel):
    ok: bool = True


class TokenRefresh(BaseModel):
    refresh_token: str


def _authenticate_user(db: Session, username: str, password: str) -> User:
    # 锁定键 = 数据库账户身份(#281 P1):已注册账户用 user.id,未注册用户名
    # 用 "unknown:<原样用户名>"。users.username 为大小写敏感普通 String,
    # 因此不再按 username.lower() 归一——大小写不同的两个账户既不共享锁定
    # 桶,也无法用变体拼写互相触发锁定。
    user = db.query(User).filter(User.username == username).first()
    lock_key = str(user.id) if user else f"unknown:{username}"

    def _verify() -> bool:
        return user is not None and verify_password(password, user.hashed_password)

    try:
        # 单锁内完成「检查锁定→密码验证→失败计数/成功清零」:并发请求无法
        # 同时通过初始锁定检查后在同一批次越阈值试密(#281 CR Major)。
        guarded_attempt(lock_key, _verify)
    except AccountLocked as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed attempts. Retry in {exc.remaining}s.",
            headers={"Retry-After": str(exc.remaining)},
        ) from None
    except InvalidCredentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    if user.is_active != "Y":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is disabled",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return user


def _issue_token_pair(user: User) -> tuple[str, str]:
    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    refresh_token = create_refresh_token(data={"sub": user.username})
    return access_token, refresh_token


def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Get current user from JWT token.

    Returns None only when neither bearer header nor auth cookie is present.
    Raises 401 if token is invalid.
    """
    if not token:
        token = request.cookies.get(ACCESS_COOKIE_NAME)

    if not token:
        return None

    # ADR-0024 P0: expected_type="access" 防止 refresh token 被当 access 重放
    # → 绕过 logout 黑名单(blacklist 只在 /auth/refresh 端点检查)。
    payload = decode_token(token, expected_type="access")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username: str = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.username == username).first()
    if not user or user.is_active != "Y":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def _refresh_unauthorized(detail: str) -> JSONResponse:
    response = JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )
    clear_auth_cookies(response)
    return response


def get_current_active_user(
    current_user: Optional[User] = Depends(get_current_user),
) -> User:
    """Get current active user, requiring authentication."""
    if not current_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return current_user


def require_admin(current_user: User = Depends(get_current_active_user)) -> User:
    """Require admin role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user


@router.post("/register", response_model=UserOut)
def register(payload: UserCreate, request: Request, db: Session = Depends(get_db)):
    """Register a new user."""
    if not is_public_register_allowed():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is disabled",
        )
    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    user = User(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        role="user",  # Force default role to prevent privilege escalation
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    record_audit(
        db,
        action="register",
        resource_type="user",
        resource_id=user.id,
        username=user.username,
        user_id=user.id,
        request=request,
    )
    # 审计与主变更同事务提交:get_db 不自动 commit,post-commit 的审计会随
    # 会话关闭被回滚(#281 CR 意见)。
    db.commit()
    return user


@router.post("/login", response_model=SessionOut)
def login(
    response: Response,
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Login and establish a browser session via HttpOnly cookies."""
    try:
        user = _authenticate_user(db, form_data.username, form_data.password)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            record_audit(
                db,
                action="login_failed",
                resource_type="user",
                resource_id=form_data.username,
                username=form_data.username,
                details={"reason": exc.detail},
                request=request,
            )
            # 失败审计独立落库(#281 P1):get_db 不自动 commit,此处若
            # 不提交,审计行会随异常抛出后的会话关闭整体回滚。
            db.commit()
        raise
    access_token, refresh_token = _issue_token_pair(user)
    set_auth_cookies(response, access_token, refresh_token)
    record_audit(
        db,
        action="login",
        resource_type="session",
        resource_id=user.id,
        username=user.username,
        user_id=user.id,
        request=request,
    )
    # _authenticate_user 已 commit(last_login),此处需再 commit 落审计
    db.commit()
    return {"ok": True}


@router.post("/token", response_model=TokenOut)
def issue_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Issue bearer tokens for Swagger, scripts, and manual API clients."""
    try:
        user = _authenticate_user(db, form_data.username, form_data.password)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            record_audit(
                db,
                action="token_failed",
                resource_type="user",
                resource_id=form_data.username,
                username=form_data.username,
                details={"reason": exc.detail},
                request=request,
            )
            # 失败审计独立落库(#281 P1),同 login_failed。
            db.commit()
        raise
    access_token, refresh_token = _issue_token_pair(user)
    record_audit(
        db,
        action="token_issued",
        resource_type="session",
        resource_id=user.id,
        username=user.username,
        user_id=user.id,
        request=request,
    )
    db.commit()
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }


@router.post("/refresh", response_model=SessionOut)
def refresh(
    request: Request,
    response: Response,
    payload: TokenRefresh | None = None,
    db: Session = Depends(get_db),
):
    """Refresh access token using refresh token."""
    refresh_token = payload.refresh_token if payload else None
    if not refresh_token:
        refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        return _refresh_unauthorized("Invalid refresh token")

    payload_data = decode_token(refresh_token, expected_type="refresh")
    if not payload_data:
        return _refresh_unauthorized("Invalid refresh token")

    jti = payload_data.get("jti")
    if jti:
        if is_revoked(db, jti):
            record_audit(
                db,
                action="refresh_rejected",
                resource_type="session",
                resource_id=jti,
                details={"reason": "jti_revoked"},
                request=request,
            )
            # 拒绝路径审计独立落库(#281 P1):返回前提交,否则随会话关闭回滚。
            db.commit()
            return _refresh_unauthorized("Invalid refresh token")
    else:
        # ADR-0024 grace 期已于 2026-06-21 结束:本提交之前签发、无 jti 的
        # refresh token 一律拒绝(黑名单机制对无 jti token 本就无效,
        # 继续放行会让存量旧 token 无限期可重放)。
        record_audit(
            db,
            action="refresh_rejected",
            resource_type="session",
            details={"reason": "missing_jti_after_grace"},
            request=request,
        )
        logger.warning("refresh_token_missing_jti rejected_after_grace sub=%s", payload_data.get("sub"))
        # 拒绝路径审计独立落库(#281 P1)。
        db.commit()
        return _refresh_unauthorized("Invalid refresh token")

    username: str = payload_data.get("sub")
    if not username:
        return _refresh_unauthorized("Invalid refresh token")

    user = db.query(User).filter(User.username == username).first()
    if not user or user.is_active != "Y":
        return _refresh_unauthorized("Invalid refresh token")

    access_token, refresh_token = _issue_token_pair(user)
    set_auth_cookies(response, access_token, refresh_token)
    return {"ok": True}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    payload: TokenRefresh | None = None,
    db: Session = Depends(get_db),
):
    """Clear auth cookies and blacklist the presented refresh jti.

    幂等:重复 logout / 已黑 jti / 解码失败的 token 都返回 200,不暴露细节给探测。
    """
    refresh_token = payload.refresh_token if payload else None
    if not refresh_token:
        refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)

    if refresh_token:
        decoded = decode_token(refresh_token, expected_type="refresh")
        if decoded:
            jti = decoded.get("jti")
            exp_ts = decoded.get("exp")
            if jti and exp_ts:
                expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
                revoke(db, jti=jti, expires_at=expires_at, reason="logout")
                record_audit(
                    db,
                    action="logout",
                    resource_type="session",
                    resource_id=jti,
                    username=decoded.get("sub"),
                    details={"reason": "logout"},
                    request=request,
                )
                # 与 revoke 同事务提交审计(#281 CR 意见)
                db.commit()

    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_active_user)):
    """Get current user info."""
    return current_user
