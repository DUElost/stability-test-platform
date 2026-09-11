"""User management API routes."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_active_user, require_admin
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.core.security import PasswordStr, get_password_hash, verify_password
from backend.models.audit import AuditLog
from backend.models.user import User as UserModel
from backend.api.schemas import PaginatedResponse

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class UserCreate(BaseModel):
    username: str
    # PasswordStr:8–128 字符且 ≤72 UTF-8 字节(bcrypt 硬限制,#281 CR Major)
    password: PasswordStr
    role: str = "user"


class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[PasswordStr] = None
    role: Optional[str] = None
    is_active: Optional[str] = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: str
    is_active: str
    created_at: datetime
    last_login: Optional[datetime] = None


class PasswordChange(BaseModel):
    old_password: str
    new_password: PasswordStr


@router.get("", response_model=PaginatedResponse)
def list_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_admin),
):
    """List all users (admin only)."""
    query = db.query(UserModel).order_by(UserModel.id)
    total = query.count()
    users = query.offset(skip).limit(limit).all()
    items = [UserOut.model_validate(u) for u in users]
    return PaginatedResponse(items=items, total=total, skip=skip, limit=limit)


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_admin),
):
    """Get a specific user by ID (admin only)."""
    user = db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("", response_model=UserOut)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_admin),
):
    """Create a new user (admin only)."""
    existing = db.query(UserModel).filter(UserModel.username == payload.username).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists",
        )

    if payload.role not in ["admin", "user"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid role. Must be 'admin' or 'user'",
        )

    user = UserModel(
        username=payload.username,
        hashed_password=get_password_hash(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    record_audit(
        db,
        action="user_created",
        resource_type="user",
        resource_id=user.id,
        # 审计主体 = 操作者;被操作对象在 resource_id/details
        username=current_user.username,
        user_id=current_user.id,
        details={"target": user.username, "role": payload.role},
        request=request,
    )
    # 审计与主变更同事务提交(get_db 不自动 commit,#281 CR 意见)
    db.commit()
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_admin),
):
    """Update a user (admin only)."""
    user = db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if username is being changed and if it already exists
    if payload.username and payload.username != user.username:
        existing = db.query(UserModel).filter(UserModel.username == payload.username).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username already exists",
            )
        user.username = payload.username

    if payload.password:
        user.hashed_password = get_password_hash(payload.password)

    if payload.role:
        if payload.role not in ["admin", "user"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid role. Must be 'admin' or 'user'",
            )
        user.role = payload.role

    if payload.is_active is not None:
        if payload.is_active not in ["Y", "N"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid is_active value. Must be 'Y' or 'N'",
            )
        user.is_active = payload.is_active

    # R02-D2（#902）：改密/改角色/停用启用即递增会话纪元，目标用户全部在发
    # token 立即失效（ver 比对见 services/auth_session.py）。
    if payload.password or payload.role or payload.is_active is not None:
        user.token_version = (user.token_version or 1) + 1

    record_audit(
        db,
        action="user_updated",
        resource_type="user",
        resource_id=user.id,
        # 审计主体 = 操作者
        username=current_user.username,
        user_id=current_user.id,
        # 只记字段名不记值:密码/角色变更的值不落审计(防明文泄漏),
        # 但 password 字段名必须保留在 changed 里——否则仅改密时 changed
        # 为空数组,无法证明密码发生过变更(#281 P2)。
        details={"target": user.username, "changed": sorted(k for k, v in payload.model_dump(exclude_none=True).items())},
        request=request,
    )
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_admin),
):
    """Delete a user (admin only). Cannot delete yourself."""
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )

    user = db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # #937: 有审计记录的用户不可硬删除——audit.user_id FK 无 SET NULL，
    # 裸删会 IntegrityError 500。指引停用路径（保审计事实）。
    audit_refs = (
        db.query(func.count(AuditLog.id))
        .filter(AuditLog.user_id == user_id)
        .scalar() or 0
    )
    if audit_refs:
        raise HTTPException(
            status_code=409,
            detail=(
                f"用户有 {audit_refs} 条审计记录，不可硬删除；"
                "如需禁止登录请使用停用（toggle-active）"
            ),
        )

    record_audit(
        db,
        action="user_deleted",
        resource_type="user",
        resource_id=user_id,
        # 审计主体 = 操作者
        username=current_user.username,
        user_id=current_user.id,
        details={"target": user.username},
        request=request,
    )
    db.delete(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="用户仍被其它记录引用，不可硬删除（可停用）",
        ) from exc
    return None


@router.post("/{user_id}/toggle-active", response_model=UserOut)
def toggle_user_active(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(require_admin),
):
    """Toggle user active status (admin only). Cannot disable yourself."""
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot disable your own account",
        )

    user = db.get(UserModel, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = "N" if user.is_active == "Y" else "Y"
    # R02-D2（#902）：停用/启用递增会话纪元——重新启用后旧 token 也不复活。
    user.token_version = (user.token_version or 1) + 1
    record_audit(
        db,
        action="user_active_toggled",
        resource_type="user",
        resource_id=user.id,
        # 审计主体 = 操作者
        username=current_user.username,
        user_id=current_user.id,
        details={"target": user.username, "is_active": user.is_active},
        request=request,
    )
    db.commit()
    db.refresh(user)
    return user


@router.post("/change-password", response_model=UserOut)
def change_password(
    payload: PasswordChange,
    request: Request,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_active_user),
):
    """Change current user's password."""
    if not verify_password(payload.old_password, current_user.hashed_password):
        record_audit(
            db,
            action="change_password_failed",
            resource_type="user",
            resource_id=current_user.id,
            username=current_user.username,
            user_id=current_user.id,
            details={"reason": "incorrect_old_password"},
            request=request,
        )
        # 失败审计独立落库(#281 P1):get_db 不自动 commit,此处若
        # 不提交,审计行会随异常抛出后的会话关闭整体回滚。
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect old password",
        )

    current_user.hashed_password = get_password_hash(payload.new_password)
    # R02-D2（#902）：改密递增会话纪元——本会话在内的全部在发 token 立即
    # 失效，用户须以新密码重新登录。
    current_user.token_version = (current_user.token_version or 1) + 1
    record_audit(
        db,
        action="change_password",
        resource_type="user",
        resource_id=current_user.id,
        username=current_user.username,
        user_id=current_user.id,
        request=request,
    )
    db.commit()
    db.refresh(current_user)
    return current_user
