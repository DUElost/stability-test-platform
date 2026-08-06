from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from backend.core.database import Base


class Script(Base):
    __tablename__ = "script"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    display_name = Column(String(256))
    category = Column(String(64))
    script_type = Column(String(16), nullable=False)
    version = Column(String(32), nullable=False)
    nfs_path = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    # ADR-0020: entry-file sha lives in content_sha256; companion modules in the
    # same version directory (e.g. _adb.py) are tracked here for scan/verify.
    support_files_manifest = Column(JSONB, nullable=False, default=dict, server_default="{}")
    # #171: 脚本能力元数据（如 ["progress_stamps"]），由版本目录 capabilities.json
    # 在 scan_script_root 时登记；Plan 的 stall_seconds 门禁依赖此列而非硬编码白名单。
    capabilities = Column(JSONB, nullable=False, default=list, server_default="[]")
    param_schema = Column(JSONB, nullable=False, default=dict)
    default_params = Column(JSONB, nullable=False, default=dict, server_default="{}")
    is_active = Column(Boolean, nullable=False, default=True)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_script_name_version"),
        Index("idx_script_active_name", "is_active", "name"),
        Index("idx_script_category", "category"),
    )
