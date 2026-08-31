"""TestProject + Specialty ORM — ADR-0029.

TestProject 是「项目登记簿」（v2 决策转向）：身份单层 + 正交 facet
（客户 / 平台 / 形态 / 产品线），承载 jira 项目关键字映射——adb 设备指纹
读不到、必须人工登记于平台的知识层。执行差异由脚本端设备路由吸收，
本表不做派发门禁（D5 挂起）。

v2 最小形态：不含 ``variables``（D4 挂起）与 ``storage_key``（D7 挂起）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)

from backend.core.database import Base


# P1 脚本灌入的 key：工作台不展示，也不算「已映射项目」。
SEED_PROJECT_KEYS = frozenset({
    "HONOR-MLD",
    "HONOR-ELA",
    "ZTE-Z258",
    "ODM-DAM",
    "TRANSSION-X110",
    "LEGACY",
})


class TestProject(Base):
    """项目登记簿行。source=USER 为人工项目；SEED 为 P1 回填，工作台不展示。"""

    __tablename__ = "test_project"

    id               = Column(Integer, primary_key=True)
    # 一经对外使用即不可变（URL / API / 日志 / 审计全链路统一，F2）。
    # 字符集 [a-z0-9-]；storage_key（D7）挂起期间不建，复议时由此派生。
    project_key      = Column(String(64), nullable=False)
    display_name     = Column(String(256), nullable=False)
    # 提交 jira 时自动带出的项目关键字（R4 唯一硬需求）；可空起步，P3 填齐。
    jira_project_key = Column(String(32), nullable=True)

    # facet：正交、可空、可枚举、可组合筛选；不建层级树（D2 两条理由）。
    # v2.5 D12：product_line / form_factor 已删列（生产无稳定值）；
    # platforms 由设备派生（P1-B 起）。customer 保留自由文本。
    customer         = Column(String(64), nullable=True)

    status           = Column(String(16), nullable=False, default="ACTIVE",
                              server_default="ACTIVE")
    # USER = 人工登记（工作台可见）；SEED = P1 脚本回填，不是客户/项目/机型。
    source           = Column(String(16), nullable=False, default="USER",
                              server_default="USER")
    created_at       = Column(DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(timezone.utc))
    updated_at       = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED')",
                        name="ck_test_project_status"),
        CheckConstraint("source IN ('USER', 'SEED')",
                        name="ck_test_project_source"),
        UniqueConstraint("project_key", name="uq_test_project_key"),
        Index(
            "uq_test_project_key_lower",
            text("lower(project_key)"),
            unique=True,
        ),
    )


class Specialty(Base):
    """Plan.specialty 的配套字典表（D6）——下拉与聚合用，与 Script.category 不同层。"""

    __tablename__ = "specialty"

    id           = Column(Integer, primary_key=True)
    key          = Column(String(32), nullable=False, unique=True)
    display_name = Column(String(64), nullable=False)
    sort_order   = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("key", name="uq_specialty_key"),
        Index("idx_specialty_sort", "sort_order"),
    )
