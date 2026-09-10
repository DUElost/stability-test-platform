"""#1276 — 旧库 setup v1.0.2 seed sha 回填（真 alembic 往返）。

o9p8q7r6s5t4 的内嵌 content_sha256 在合入后被原地修正（#1171）：已执行过该
迁移的库保留旧值，script_catalog 持续报 conflict。本测试在一次性 PG 上：

  a. upgrade 到 t7u6v5w4x3y2 的 down_revision（s5t4u3v2w1x0）；
  b. 把 sleep_setup v1.0.2 改回旧 sha（等价于修复前迁移过的库），
     powercycle_setup v1.0.2 保持正确 sha（等价于已 force_rebaseline 的安装）；
  c. upgrade head → sleep_setup 回正；powercycle_setup 不被误改；
  d. downgrade -1 → no-op（不回写已知错误值），两行保持正确；
  e. 再次 upgrade head → 幂等。

docker 不可用 → SKIP（不假绿）。
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
PGDB = "stp_1276_sha_backfill"
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
PY = sys.executable

OLD_SHA = "970a02133edcf75528adb74cdcf413d89f8d3a4f6f384ab3380d1c872db6db79"
NEW_SHA = "41f40e5498e0ae822378d4c33443d8c2e9f7b008d7eab3b352d4905d19f9aea2"
POWERCYCLE_SHA = "f36b155bffe1e3faed11ef3286a49492ac8279c4cf842d92831b6c33bf195e33"
DOWN_REVISION = "s5t4u3v2w1x0"

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


def _connect(url: str):
    import psycopg

    return psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"))


def _sha_of(cur, name: str) -> str | None:
    cur.execute(
        "SELECT content_sha256 FROM script WHERE name = %s AND version = '1.0.2'",
        (name,),
    )
    row = cur.fetchone()
    return row[0] if row else None


@docker_ready
def test_setup_v102_sha_backfill_roundtrip():
    container, url = _start_pg()
    try:
        up = _alembic(url, "upgrade", DOWN_REVISION)
        assert up.returncode == 0, up.stderr

        # b. 模拟「修复前迁移过的库」：sleep_setup 回旧值；powercycle 保持正确值。
        with _connect(url) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE script SET content_sha256 = %s "
                "WHERE name = 'sleep_setup' AND version = '1.0.2'",
                (OLD_SHA,),
            )
            assert cur.rowcount == 1, "sleep_setup v1.0.2 应存在于迁移后的库中"
            assert _sha_of(cur, "powercycle_setup") == POWERCYCLE_SHA

        # c. 执行回填。
        assert _alembic(url, "upgrade", "head").returncode == 0
        with _connect(url) as conn, conn.cursor() as cur:
            assert _sha_of(cur, "sleep_setup") == NEW_SHA
            assert _sha_of(cur, "powercycle_setup") == POWERCYCLE_SHA

        # d. 降级为 no-op：不把已知错误值写回。
        down = _alembic(url, "downgrade", DOWN_REVISION)
        assert down.returncode == 0, down.stderr
        with _connect(url) as conn, conn.cursor() as cur:
            assert _sha_of(cur, "sleep_setup") == NEW_SHA
            assert _sha_of(cur, "powercycle_setup") == POWERCYCLE_SHA

        # e. 幂等：再次升到 head 不改变现状。
        assert _alembic(url, "upgrade", "head").returncode == 0
        with _connect(url) as conn, conn.cursor() as cur:
            assert _sha_of(cur, "sleep_setup") == NEW_SHA
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
