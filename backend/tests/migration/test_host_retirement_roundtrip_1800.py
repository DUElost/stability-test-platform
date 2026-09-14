"""#1800 / ADR-0038 ①：host 退役四列的迁移往返与结构契约。

覆盖验收：additive nullable、无回填、单 head（离线读）、upgrade head →
downgrade 至本迁移的父 revision → 再 upgrade 往返；并断言 ORM 与迁移两侧
列名/类型一致（check_schema_sync 的运行时对偶）。

#1935 修正：原断言假设本迁移 == head、用 `downgrade -1` 撤列；#1890/#1907
在其上续接迁移后 head 前移，两处都误红。现改为「本迁移仍在 head 祖先链上」
+ 显式 downgrade 到本迁移的父 revision（revision 不可变，父版本稳定）。

测试基建对齐 tools/dev/check_pr_migrate.py 与 #935 先例：docker 可用 →
postgres:16 一次性容器真跑 alembic；不可用 → SKIP（不假绿）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

IMAGE = "postgres:16"
PGUSER = "postgres"
PGPASSWORD = "postgres"
PGDB = "stp_1800_retire"
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
PY = sys.executable

#: 本次迁移的 revision（新列挂在它上面）
RETIRE_REV = "f3a4b5c6d7e8"
#: 本迁移的父 revision——撤列目标（已发布 revision 不可变，此值稳定；
#: 不用相对 `-1`：#1890/#1907 之后 head 前移，`-1` 撤的是别的迁移）。
RETIRE_DOWN_REV = "a3b2c1d0e9f8"
RETIRE_COLUMNS = ("retired_at", "retired_by", "retire_reason", "retire_alerted_at")

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


def _host_columns(url: str) -> dict[str, tuple[str, str]]:
    """列名 → (data_type, is_nullable)（information_schema 直读）。"""
    import psycopg

    dsn = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'host'"
        )
        return {name: (dtype, nullable) for name, dtype, nullable in cur.fetchall()}


def test_single_head_offline():
    """单 head，且本迁移仍在 head 的祖先链上（离线读脚本目录，不需要 DB）。

    不断言 ``RETIRE_REV == head``——#1890/#1907 在链上续接迁移后 head 前移，
    「本迁移仍在链上」才是本测试的本意（原断言在 head 前移后误红，#1935）。
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(Path(BACKEND_DIR) / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(BACKEND_DIR) / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    heads = script.get_heads()
    assert len(heads) == 1, f"alembic heads 必须单头，实际：{heads}"

    lineage: set[str] = set()
    pending = [heads[0]]
    while pending:
        rev = script.get_revision(pending.pop())
        lineage.add(rev.revision)
        down = rev.down_revision
        if isinstance(down, tuple):
            pending.extend(down)
        elif down:
            pending.append(down)
    assert RETIRE_REV in lineage, f"{RETIRE_REV} 不在 head {heads[0]} 的祖先链上"


@docker_ready
def test_retirement_columns_roundtrip():
    """upgrade head → 四列存在且可空 → downgrade 到父 revision 撤列 → 再 upgrade 恢复。"""
    container, url = _start_pg()
    try:
        up = _alembic(url, "upgrade", "head")
        assert up.returncode == 0, up.stderr
        columns = _host_columns(url)
        for name in RETIRE_COLUMNS:
            assert name in columns, f"{name} 未建：{sorted(columns)}"
            _dtype, nullable = columns[name]
            assert nullable == "YES", f"{name} 必须可空（additive nullable）"

        # 无回填：升级后插入的行 retired_at 默认 NULL（列可空、无 server_default）
        import psycopg

        dsn = url.replace("postgresql+psycopg://", "postgresql://")
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO host (id, hostname, status, cpu_quota, "
                "watcher_admin_active, created_at, boot_id, last_agent_instance_id) "
                "VALUES ('h-1800', 'h-1800', 'OFFLINE', 2, true, now(), '', '')"
            )
            cur.execute("SELECT retired_at FROM host WHERE id = 'h-1800'")
            assert cur.fetchone() == (None,)

        down = _alembic(url, "downgrade", RETIRE_DOWN_REV)
        assert down.returncode == 0, down.stderr
        columns_after = _host_columns(url)
        for name in RETIRE_COLUMNS:
            assert name not in columns_after, f"{name} 未被 downgrade 撤销"

        again = _alembic(url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        assert set(RETIRE_COLUMNS) <= set(_host_columns(url))
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
