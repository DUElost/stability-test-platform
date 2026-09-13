"""#1717 — rechain 后重放 dd44 passthrough sha 回填（真 alembic，对照 #751 模式）。

``8c4d5f47`` 把 p9q8r7s6t5u4 rechain 到 dd44ee55ff66 之后，rechain 前把 p9q8
当 head 应用过的库不会再执行 dd44。本测试在一次性 PG 上模拟该受损态：

  a. upgrade 到 f6a5b4c3d2e1 的 down_revision（x9y8z7a6b5c4）——新链库三行
     sha 本应正确（先断言基线）；
  b. 把三行改回旧 _lib sha（模拟「dd44 从未执行」的受损库）；另把无关行
     fill_storage v1.0.2 改成哨兵值作对照；
  c. upgrade head（f6a5b4c3d2e1 执行）→ 三行回正、对照行不动；
  d. downgrade -1 → no-op，行不被写回错误值；
  e. 再次 upgrade head → 幂等。

docker 不可用 → SKIP。
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
PGDB = "stp_1717_sha_replay"
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
PY = sys.executable

DOWN_REVISION = "x9y8z7a6b5c4"
BACKFILL = [
    (
        "gpu_setup", "1.0.2",
        "961f2f3929b26aae4213c879992c609bb71ae4ce88134125a74c88dce9043990",
        "a654a624a197dcbdfa626dbfd478114273a19253da779ff03fbae13f540748b1",
    ),
    (
        "powercycle_setup", "1.0.1",
        "38cb525fd5405b0c0f08c765a8d3a3bdbcea7aadf21c0733f59fedbfd9a960e3",
        "29136c9ce24f9dfcc90ad705988f9a5aa153de5a59d96e697ea278e59b9e3d7e",
    ),
    (
        "sleep_setup", "1.0.1",
        "c54ed4b371612e37af3954df07a5588a19adebc5edce74f6205b0f1557f2163e",
        "970a02133edcf75528adb74cdcf413d89f8d3a4f6f384ab3380d1c872db6db79",
    ),
]
# 对照行：p9q8 seed 的 fill_storage v1.0.2——不在回填表内，任何 sha 都不得被碰
CONTROL = ("fill_storage", "1.0.2", "cafebabecafebabecafebabecafebabecafebabecafebabecafebabecafebabe")

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


def _sha_of(cur, name: str, ver: str) -> str | None:
    cur.execute(
        "SELECT content_sha256 FROM script WHERE name = %s AND version = %s",
        (name, ver),
    )
    row = cur.fetchone()
    return row[0] if row else None


@docker_ready
def test_reapply_dd44_backfill_self_heals_damaged_db():
    container, url = _start_pg()
    try:
        # a. 新链基线：upgrade 到 f6a5 的 down_revision，三行 sha 已正确
        up = _alembic(url, "upgrade", DOWN_REVISION)
        assert up.returncode == 0, up.stderr
        with _connect(url) as conn, conn.cursor() as cur:
            for name, ver, _old, new_sha in BACKFILL:
                assert _sha_of(cur, name, ver) == new_sha, (
                    f"{name} v{ver} 在新链上应为正确 sha（dd44 已执行）"
                )

        # b. 模拟受损库：三行回退到 _lib 误写 sha；对照行设哨兵值
        with _connect(url) as conn, conn.cursor() as cur:
            for name, ver, old_sha, _new in BACKFILL:
                cur.execute(
                    "UPDATE script SET content_sha256 = %s "
                    "WHERE name = %s AND version = %s",
                    (old_sha, name, ver),
                )
                assert cur.rowcount == 1, f"{name} v{ver} 应存在于迁移后的库中"
            cur.execute(
                "UPDATE script SET content_sha256 = %s WHERE name = %s AND version = %s",
                (CONTROL[2], CONTROL[0], CONTROL[1]),
            )
            assert cur.rowcount == 1
            conn.commit()

        # c. upgrade head → 自愈
        assert _alembic(url, "upgrade", "head").returncode == 0
        with _connect(url) as conn, conn.cursor() as cur:
            for name, ver, _old, new_sha in BACKFILL:
                assert _sha_of(cur, name, ver) == new_sha
            assert _sha_of(cur, CONTROL[0], CONTROL[1]) == CONTROL[2], (
                "WHERE content_sha256 = :old 守卫：回填表之外的行不得被改写"
            )

        # d. downgrade -1 → no-op，不写回错误值
        down = _alembic(url, "downgrade", DOWN_REVISION)
        assert down.returncode == 0, down.stderr
        with _connect(url) as conn, conn.cursor() as cur:
            for name, ver, _old, new_sha in BACKFILL:
                assert _sha_of(cur, name, ver) == new_sha

        # e. 再次 upgrade head → 幂等
        assert _alembic(url, "upgrade", "head").returncode == 0
        with _connect(url) as conn, conn.cursor() as cur:
            for name, ver, _old, new_sha in BACKFILL:
                assert _sha_of(cur, name, ver) == new_sha
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
