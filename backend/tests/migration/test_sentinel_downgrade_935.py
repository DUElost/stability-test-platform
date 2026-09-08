"""#935 — 哨兵项目迁移 downgrade 守卫（带历史数据往返）。

b1c2d3e4f5a6 downgrade 重建 plan_run FK 时全表校验：历史 project_id 引用已删
哨兵（GENERIC/LEGACY）旧 id 的行会让降级在半途炸掉。守卫 = 入口孤儿计数
（任何引用不存在项目的 plan_run）→ RuntimeError 明确拒绝。

测试基建对齐 tools/dev/check_pr_migrate.py：docker 可用 → postgres:16 一次性
容器真跑 alembic 往返；不可用 → SKIP（不假绿）。
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

IMAGE = "postgres:16"
PGUSER = "postgres"
PGPASSWORD = "postgres"
PGDB = "stp_935_downgrade"
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
PY = sys.executable

docker_ready = pytest.mark.skipif(
    subprocess.run(
        ["docker", "info"], capture_output=True,
    ).returncode != 0,
    reason="docker 不可用，无法真跑迁移往返",
)


def _start_pg() -> tuple[str, str]:
    port = subprocess.run(
        ["docker", "run", "-d", "--rm", "-P", "-e", f"POSTGRES_PASSWORD={PGPASSWORD}",
         "-e", "POSTGRES_DB=" + PGDB, IMAGE],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    host_port = subprocess.run(
        ["docker", "port", port, "5432"], capture_output=True, text=True, check=True,
    ).stdout.splitlines()[0].split(":")[-1].strip()
    url = f"postgresql+psycopg://{PGUSER}:{PGPASSWORD}@127.0.0.1:{host_port}/{PGDB}"
    # 等 PG 就绪（psycopg 直连不认 +psycopg scheme，probe 用裸 postgresql://）
    import time

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
        subprocess.run(["docker", "rm", "-f", port], capture_output=True)
        pytest.fail("postgres 60s 内未就绪")
    return port, url


def _alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, DATABASE_URL=url)
    return subprocess.run(
        [PY, "-m", "alembic", *args],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True,
    )


def _insert_sentinel_orphan(url: str) -> None:
    """upgrade head 后哨兵已删、FK 已 drop——直插引用不存在项目 id 的历史
    plan_run 快照行（等价于升级前引用 GENERIC/LEGACY 旧 id 的存量）。"""
    import psycopg

    dsn = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # 回退路径上 n4o5p6q7r8s9（#902）的 downgrade 要求 plan.specialty_id
            # 非空——补 specialty 种子并挂引用，让数据只卡我们目标的那一步。
            cur.execute(
                "INSERT INTO specialty (key, display_name, sort_order) "
                "VALUES ('ops', '运维', 10) ON CONFLICT (key) DO NOTHING"
            )
            cur.execute(
                "INSERT INTO plan (name, failure_threshold, specialty_id) "
                "VALUES ('orphan-plan', 0.05, "
                "(SELECT id FROM specialty WHERE key = 'ops'))"
            )
            cur.execute(
                "INSERT INTO plan_run (plan_id, status, failure_threshold, "
                "plan_snapshot, run_type, project_id) "
                "SELECT id, 'SUCCESS', 0.05, '{}', 'MANUAL', 99999901 FROM plan "
                "ORDER BY id LIMIT 1"
            )
        conn.commit()


@docker_ready
def test_sentinel_downgrade_guard_roundtrip():
    """空库降级/升级往返成功；带孤儿引用数据降级被明确拒绝（#935 验收）。"""
    container, url = _start_pg()
    try:
        # a. 空库 upgrade head → 降过本迁移（目标 = 其 down_revision，才会
        # 真正执行 b1c2d3e4f5a6.downgrade）→ 应成功
        assert _alembic(url, "upgrade", "head").returncode == 0
        down = _alembic(url, "downgrade", "l5m6n7o8p9q0")
        assert down.returncode == 0, down.stderr
        # b. 恢复 head（往返）
        assert _alembic(url, "upgrade", "head").returncode == 0
        # c. 插入引用不存在项目 id 的历史快照行
        _insert_sentinel_orphan(url)
        # d. 同路径降级 → 守卫拒绝（exit != 0 且文案可辨识）
        guarded = _alembic(url, "downgrade", "l5m6n7o8p9q0")
        assert guarded.returncode != 0
        assert "downgrade refused" in (guarded.stdout + guarded.stderr)
        # e. schema 未被半途破坏：head 仍可正常查询（迁移停在 head）
        assert _alembic(url, "current").returncode == 0
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
