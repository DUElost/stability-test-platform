"""#2694：`audit_logs` facets 支撑索引的迁移往返契约。

覆盖验收：两个索引 additive 出现、upgrade head → downgrade 至**本迁移的父 revision**
→ 再 upgrade 往返；并断言「索引存在」与「ORM 侧声明一致」。

沿用 #1800/#1935 的基建与教训：

- docker 可用 → postgres:16 一次性容器真跑 alembic；不可用 → SKIP（**不假绿**）；
- **不用相对 `downgrade -1`**：#1890/#1907 的教训是 head 前移后 `-1` 撤的是**别的**
  迁移；本文件显式 downgrade 到本迁移的父 revision（已发布 revision 不可变，父值稳定）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

IMAGE = "postgres:16"
PGUSER = "postgres"
PGPASSWORD = "postgres"
PGDB = "stp_2694_audit_idx"
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
PY = sys.executable

INDEX_REV = "a1b2c3d4e5f7"
#: 本迁移的父 revision（#2694 落笔时 e5f6a7b8c9d0 为 head）。
INDEX_DOWN_REV = "e5f6a7b8c9d0"
NEW_INDEXES = ("ix_audit_action_ts", "ix_audit_ts")

docker_ready = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="docker 不可用，无法真跑迁移往返",
)


def _start_pg() -> tuple[str, str]:
    container = subprocess.run(
        ["docker", "run", "-d", "--rm", "-P", "-e", f"POSTGRES_PASSWORD={PGPASSWORD}",
         "-e", "POSTGRES_DB=" + PGDB, IMAGE],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    host_port = subprocess.run(
        ["docker", "port", container, "5432"], capture_output=True, text=True, check=True,
    ).stdout.splitlines()[0].split(":")[-1].strip()
    url = f"postgresql+psycopg://{PGUSER}:{PGPASSWORD}@127.0.0.1:{host_port}/{PGDB}"
    dsn = url.replace("postgresql+psycopg://", "postgresql://")
    for _ in range(60):
        probe = subprocess.run(
            [PY, "-c",
             "import psycopg, sys; psycopg.connect(sys.argv[1], connect_timeout=2).close()",
             dsn],
            capture_output=True,
        )
        if probe.returncode == 0:
            break
        time.sleep(1)
    else:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        pytest.fail("postgres 60s 内未就绪")
    return container, url


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=url)
    return subprocess.run(
        [PY, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )


def _audit_index_names(url: str) -> set[str]:
    """`audit_logs` 上的索引名（pg_indexes 直读）。"""
    import psycopg

    dsn = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'audit_logs'")
        return {name for (name,) in cur.fetchall()}


def test_single_head_offline():
    """单 head，且本迁移在 head 的祖先链上（离线读脚本目录，不需要 DB）。"""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    # `script_location` 在 alembic.ini 里是**相对路径**（相对 CWD），故必须显式
    # 覆盖成绝对路径——否则在测试 CWD 下会报 "Path doesn't exist: alembic"
    # （与 #1800 的离线读法一致）。
    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "alembic"))
    sd = ScriptDirectory.from_config(cfg)
    head = sd.get_current_head()
    chain = [s.revision for s in sd.walk_revisions(base="base", head=head)]
    assert INDEX_REV in chain, f"{INDEX_REV} 不在 head({head}) 的祖先链上"


@docker_ready
def test_audit_indexes_roundtrip():
    """upgrade head → 两个索引在场 → downgrade 到父 → 撤净 → 再 upgrade 恢复。"""
    container, url = _start_pg()
    try:
        up = _alembic(url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        present = _audit_index_names(url)
        for name in NEW_INDEXES:
            assert name in present, f"{name} 未建：{sorted(present)}"

        # 显式降级到**本迁移的父** revision（不用 -1，见模块 docstring）
        down = _alembic(url, "downgrade", INDEX_DOWN_REV)
        assert down.returncode == 0, down.stderr
        after = _audit_index_names(url)
        for name in NEW_INDEXES:
            assert name not in after, f"{name} 未被 downgrade 撤销：{sorted(after)}"
        # 既有索引不得被误撤（additive 迁移的边界）
        assert "ix_audit_user_ts" in after and "ix_audit_resource" in after, sorted(after)

        again = _alembic(url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        restored = _audit_index_names(url)
        for name in NEW_INDEXES:
            assert name in restored, f"{name} 再 upgrade 后未恢复：{sorted(restored)}"
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)


def test_orm_declares_the_same_indexes_offline():
    """ORM 侧声明的索引名须与迁移一致（离线，无需 DB）。

    这是 `check_schema_sync` 的轻量对偶：迁移与模型两侧**索引名/列序**漂移时，
    schema-sync 会在 CI 报出差异，但那条需要 DB；本用例让漂移在**任何环境**可见。
    """
    from backend.models.audit import AuditLog

    declared = {ix.name: tuple(c.name for c in ix.columns) for ix in AuditLog.__table__.indexes}
    assert declared.get("ix_audit_action_ts") == ("action", "timestamp"), declared
    assert declared.get("ix_audit_ts") == ("timestamp",), declared
