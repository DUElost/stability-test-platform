"""TestSuite + TestCase ORM — ADR-0030 D1 / P1 设计 §1。

**配置层实体**：粒度 = testpoint（用户视角的「一条用例」），一条用例含 1..N 个
执行描述（`exec_descs`）。不进调度模型——唯一 action 类型 `script:<name>`
不变量保持，套件只是脚本消费的配置数据。

导出一致性靠**两个计算检测器**（P1 设计 §2 总则），不靠端点置空纪律：
- `exported_content_sha256` ← 库内容规范化指纹，检测「库改了没导出」；
- `exported_sha256` ← 导出产物文件 sha，检测「导出后磁盘被人动过」。
因此本模型的任何写路径都**不需要**手工清快照列，新增写端点也漏不掉。
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from backend.core.database import Base


class TestSuite(Base):
    # 类名以 Test 开头，pytest 会试图当测试类收集——显式关掉，避免收集告警
    __test__ = False
    __tablename__ = "test_suite"

    id                     = Column(Integer, primary_key=True)
    # 管理键：外部 agent 以 `suite_key` 引用（plan_step.default_params）。
    # 全局唯一——跨项目同名不允许，分化靠 project_id 区分导出目录（§7 #7）。
    name                   = Column(String(128), nullable=False)
    display_name           = Column(String(256), nullable=True)
    # 空 = 通用套件（现 MTBF）；非空 = 项目套件（相机 MTBF，APK↔项目严格对应）。
    project_id             = Column(Integer, ForeignKey("test_project.id"), nullable=True)
    # 导出目录名；空 → 有 project_id 用 project key，否则 `legacy`（兼容 P0 部署）。
    export_dir             = Column(String(128), nullable=True)
    # 用例 APK 文件名数组；文件 sha 不存库（脚本 setup 已留 apk_sha256[] 痕）。
    apk_binding            = Column(JSONB, nullable=True)
    # runtask 根属性全量——导出渲染的权威源。
    #
    # 这三个「喂给渲染器」的列用 JSON 而非 JSONB：JSONB 会**重排对象键**
    # （按长度再字节序），而导出物要逐字节还原源文件，键序即文档序。
    # 实证：args 的 wifiName/wifiPWD 经 JSONB 往返后互换，导出即不再同构。
    # 代价是失去 JSONB 索引/操作符——这三列是不可检索的配置文档，不需要。
    root_config            = Column(JSON, nullable=False, default=dict)
    # {"sim": {...}, "test_set_attrs": {...}, "test_package_ref": "<原文或 null>"}
    global_params          = Column(JSON, nullable=True)

    source_sha256          = Column(String(64), nullable=True)   # 导入时原始文件 sha（溯源）
    exported_sha256        = Column(String(64), nullable=True)   # 磁盘漂移比对键
    exported_content_sha256 = Column(String(64), nullable=True)  # 库漂移比对键

    is_active              = Column(Boolean, nullable=False, default=True,
                                    server_default="true")
    created_at             = Column(DateTime(timezone=True), nullable=False,
                                    default=lambda: datetime.now(timezone.utc))
    updated_at             = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    project = relationship("TestProject", foreign_keys=[project_id])
    cases = relationship(
        "TestCase",
        back_populates="suite",
        cascade="all, delete-orphan",
        order_by="TestCase.ordinal",
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_test_suite_name"),
        Index("idx_test_suite_project", "project_id"),
    )


class TestCase(Base):
    """用例（粒度 = testpoint）。

    标识符原样保留（研究 §5.2）：class/method 含疑似拼写错误的名字在导入/导出
    全链路不得「修正」——库内就是设备端 APK 的真实标识符。
    """

    __test__ = False
    __tablename__ = "test_case"

    id          = Column(Integer, primary_key=True)
    suite_id    = Column(Integer, ForeignKey("test_suite.id", ondelete="CASCADE"),
                         nullable=False)
    # suite 内唯一：runtask 校验 TESTPOINT_NAME_DUPLICATE 是 error，库里先挡住。
    name        = Column(String(512), nullable=False)
    ordinal     = Column(Integer, nullable=False, default=0)
    times       = Column(Integer, nullable=False, default=1, server_default="1")
    enabled     = Column(Boolean, nullable=False, default=True, server_default="true")
    # [{type, apk, package, class, method, runner, device, args:{}, times}]
    exec_descs  = Column(JSON, nullable=False, default=list)

    suite = relationship("TestSuite", back_populates="cases")

    __table_args__ = (
        UniqueConstraint("suite_id", "name", name="uq_test_case_suite_name"),
        Index("idx_test_case_suite_ordinal", "suite_id", "ordinal"),
    )
