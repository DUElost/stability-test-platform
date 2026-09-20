"""`abort_scale_probe` 的两条安全/可用性判据（#2843 / #2844）。

背景：#703① 的现场压测腿是**破坏性工具**（seed 写 30 host / 510 job；cleanup 直接
DELETE 同规模）。它的 run 腿有 `_require_local`（拒绝非回环控制面），但 seed/cleanup
两条腿此前**零守卫**——而 `backend.core.database` 的 DSN 在 ambient 没有
`DATABASE_URL` 时**静默回退仓库根 `.env.backend`（生产 env 源）**：忘一条 export 就把
破坏性操作打在真生产库上（AGENTS.md 红线）。
"""
from __future__ import annotations

import pytest

from tools.dev import abort_scale_probe as probe
from tools.dev.source_anchor import SourceGuard

PROBE_REL = "tools/dev/abort_scale_probe.py"
DEV_DSN = "postgresql+psycopg://stp:change-me-local@127.0.0.1:15432/stp_dev"


def test_guard_refuses_when_database_url_is_not_explicit(monkeypatch):
    """未显式导出 DATABASE_URL ⇒ 拒绝（不许走 .env.backend 兜底）。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        probe._require_dev_db_target()
    assert "破坏性" in str(excinfo.value) and "#2844" in str(excinfo.value)


def test_guard_refuses_production_shaped_target(monkeypatch):
    """生产形态目标（本机 5432 上的业务库）⇒ 拒绝。"""
    # 注意：**不要**用 127.0.0.1:5432——本机 5432 就是生产 PG，守卫一旦失效测试会真去连它。
    # 指向未监听端口：守卫失效时得到的是「连接被拒」，而不是「打到疑似生产库」。
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://stp:secret@127.0.0.1:5599/stp"
    )
    with pytest.raises(SystemExit) as excinfo:
        probe._require_dev_db_target()
    assert "不像 dev 栈" in str(excinfo.value)


@pytest.mark.parametrize(
    "dsn",
    [
        DEV_DSN,  # 库名 + dev 端口
        "postgresql+psycopg://stp:x@127.0.0.1:15432/stp_dev_2",  # dev 端口（库名不同）
        "postgresql+psycopg://stp:x@127.0.0.1:9999/stp_dev",  # 库名（端口不同）
    ],
)
def test_guard_accepts_dev_stack_target(monkeypatch, dsn):
    monkeypatch.setenv("DATABASE_URL", dsn)
    probe._require_dev_db_target()  # 不抛即通过


def test_seed_entry_runs_the_guard_before_any_tooling(monkeypatch):
    """端到端行为：`main(["seed"])` 在目标不合规时**立即**拒绝（先于任何 ORM/DB 动作）。"""
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://stp:secret@127.0.0.1:5599/stp")
    with pytest.raises(SystemExit):
        probe.main(["seed"])


def test_probe_does_not_reference_removed_failure_threshold_column():
    """#2843 结构判据：`failure_threshold` 已随 ADR-0048/#2734 从 Plan/PlanRun 删除。

    探针的 `seed` 腿仍给它绑值会让 seed **当场崩**（`ArgumentError: Unconsumed column
    names`）——即整条 seed→run→cleanup 链不可用。CI 侧同型调用点已在 #2790 修掉，这条
    钉住探针这个调用点（removal 类迁移的消费面核对：tools/ 与 backend/ 都要查）。

    源扫描否定断言走 SourceGuard（#2639）：锚点漂移时报「用例已过期」，不会恒真空守。
    """
    guard = SourceGuard.of_repo_path(PROBE_REL).anchored("def cmd_seed(")
    guard.assert_absent(
        "failure_threshold",
        why="#2843：探针又引用已删列会让 seed 当场 ArgumentError",
    )


def test_probe_seed_and_cleanup_call_the_guard_first():
    """结构判据：破坏性入口的**第一条语句**就是守卫（惰性 import 的 ORM 在它之后）。"""
    source = SourceGuard.of_repo_path(PROBE_REL).anchored("def _require_dev_db_target()").text
    for entry in ("def cmd_seed(", "def cmd_cleanup("):
        index = source.index(entry)
        body = source[index : index + 400]
        assert "_require_dev_db_target()" in body, f"{entry} 未先过目标库守卫（#2844）"
        assert body.index("_require_dev_db_target()") < body.index("import"), (
            f"{entry} 的守卫必须在任何 import 之前（import backend.models 会连带建引擎）"
        )
