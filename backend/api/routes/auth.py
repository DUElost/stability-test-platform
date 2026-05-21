"""Authentication API routes."""
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.database import get_db
from backend.core.security import (
    ACCESS_COOKIE_NAME,
    REFRESH_COOKIE_NAME,
    clear_auth_cookies,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
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
    username: str
    password: str
    # role is intentionally excluded to prevent privilege escalation
    # new users are always created with "user" role


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: str
    created_at: datetime

    class Config:
        from_attributes = True


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SessionOut(BaseModel):
    ok: bool = True


class TokenRefresh(BaseModel):
    refresh_token: str


def _authenticate_user(db: Session, username: str, password: str) -> User:
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

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

    payload = decode_token(token)
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
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """Register a new user."""
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
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=SessionOut)
def login(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Login and establish a browser session via HttpOnly cookies."""
    user = _authenticate_user(db, form_data.username, form_data.password)
    access_token, refresh_token = _issue_token_pair(user)
    set_auth_cookies(response, access_token, refresh_token)
    return {"ok": True}


@router.post("/token", response_model=TokenOut)
def issue_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Issue bearer tokens for Swagger, scripts, and manual API clients."""
    user = _authenticate_user(db, form_data.username, form_data.password)
    access_token, refresh_token = _issue_token_pair(user)
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

    payload_data = decode_token(refresh_token)
    if not payload_data or payload_data.get("type") != "refresh":
        return _refresh_unauthorized("Invalid refresh token")

    jti = payload_data.get("jti")
    if jti:
        if is_revoked(db, jti):
            return _refresh_unauthorized("Invalid refresh token")
    else:
        # Grace 期:本提交之前签发的 refresh 没有 jti。WARN 但放行,30 天后
        # 所有旧 token 自然过期 → 那时可将本分支改为直接 401。
        logger.warning("refresh_token_missing_jti grace_window_active sub=%s", payload_data.get("sub"))

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
        decoded = decode_token(refresh_token)
        if decoded and decoded.get("type") == "refresh":
            jti = decoded.get("jti")
            exp_ts = decoded.get("exp")
            if jti and exp_ts:
                expires_at = datetime.fromtimestamp(exp_ts, tz=timezone.utc)
                revoke(db, jti=jti, expires_at=expires_at, reason="logout")

    clear_auth_cookies(response)
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_active_user)):
    """Get current user info."""
    return current_user
