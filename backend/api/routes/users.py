"""User management API routes."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from backend.api.routes.auth import get_current_active_user, require_admin
from backend.core.database import get_db
from backend.core.security import get_password_hash, verify_password
from backend.models.user import User as UserModel
from backend.api.schemas import PaginatedResponse

router = APIRouter(prefix="/api/v1/users", tags=["users"])


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    username: Optional[str] = None
    password: Optional[str] = None
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
    new_password: str


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
    db.commit()
    db.refresh(user)
    return user


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
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

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
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

    db.delete(user)
    db.commit()
    return None


@router.post("/{user_id}/toggle-active", response_model=UserOut)
def toggle_user_active(
    user_id: int,
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
    db.commit()
    db.refresh(user)
    return user


@router.post("/change-password", response_model=UserOut)
def change_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    current_user: UserModel = Depends(get_current_active_user),
):
    """Change current user's password."""
    if not verify_password(payload.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect old password",
        )

    current_user.hashed_password = get_password_hash(payload.new_password)
    db.commit()
    db.refresh(current_user)
    return current_user
