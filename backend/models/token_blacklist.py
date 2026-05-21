"""Revoked refresh token blacklist.

Why: refresh token 30 天有效期 + 无 server-side 撤销手段时,用户登出/封号后
旧 refresh 仍可换 access token。这里以 jti 为主键持久化"已吊销 jti",
refresh 端点在 decode_token 之后用 PK lookup 判断;过期行由 APScheduler
每日清理。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String

from backend.core.database import Base


class RevokedRefreshToken(Base):
    __tablename__ = "revoked_refresh_token"

    jti = Column(String(64), primary_key=True)
    revoked_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    reason = Column(String(64), nullable=True)

    __table_args__ = (
        Index("idx_revoked_refresh_token_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<RevokedRefreshToken jti={self.jti} reason={self.reason}>"
