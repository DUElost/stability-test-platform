"""#2322 — 重放 #2055 对两个已合入 seed revision 的原地改写（真 alembic，对照 #1717 模式）。

``02941d3c`` 在**已合入 main 之后**改写了 ``z1a2b3c4d5e6``（monkey_launch v5.0.2）
与 ``y0z1a2b3c4d5``（gpu_setup v1.0.10）的函数体却没有附重放迁移，于是对这些
revision 进 main 之后才 upgrade 的库，改后的代码永远不会执行。本测试在一次性 PG
上模拟「已执行过旧代码的库」，验证 ``d4e5f6a7b8c9`` 的幂等自愈：

  a. upgrade 到 ``d4e5f6a7b8c9`` 的 down_revision——新链基线（5.0.2 active、
     1.0.9 已停用）；
  b. 构造旧代码留下的缺陷态：monkey_launch 无任何 active 版本；
  c. upgrade head → 5.0.2 自愈为 active；
  d. downgrade -1 → no-op（不把 5.0.2 翻回 inactive）；
  e. 再次 upgrade head → 幂等；
  f. 反向对照：管理员有意停用但**另有** active 版本时不得被改写；
  g. gpu_setup 侧：仍被 ``plan_step`` 引用时 upgrade 必须**失败**（引用核对从
     「静默停用」变成部署期可见的失败），解除引用后成功。

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
PGDB = "stp_2322_seed_replay"
BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
PY = sys.executable

#: 本迁移的 down_revision——受损库的 alembic_version 可能停在这里或更早
DOWN_REVISION = "p6q7r8s9t0u1"
REPLAY_REVISION = "d4e5f6a7b8c9"

MONKEY_LAUNCH = ("monkey_launch", "5.0.2")
#: 对照行：本迁移不得触碰的其它 seed（任意哨兵 is_active 值）
CONTROL = ("sleep_check", "1.0.4")
GPU_SETUP_DISABLED = ("gpu_setup", "1.0.9")

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


def _is_active(cur, name: str, ver: str) -> bool | None:
    cur.execute(
        "SELECT is_active FROM script WHERE name = %s AND version = %s", (name, ver),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _set_active(cur, name: str, ver: str, value: bool) -> None:
    cur.execute(
        "UPDATE script SET is_active = %s WHERE name = %s AND version = %s",
        (value, name, ver),
    )
    assert cur.rowcount == 1, f"{name} v{ver} 应存在于迁移后的库中"


def _add_plan_step_referencing(cur, name: str, ver: str) -> None:
    cur.execute(
        "INSERT INTO plan (name, failure_threshold, created_at, updated_at) "
        "VALUES ('2322-probe', 0.05, now(), now()) RETURNING id"
    )
    plan_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO plan_step "
        "(plan_id, step_key, script_name, script_version, stage, sort_order, "
        " retry, enabled, created_at) "
        "VALUES (%s, 's1', %s, %s, 'init', 0, 0, true, now())",
        (plan_id, name, ver),
    )


@docker_ready
def test_replay_heals_monkey_launch_active_gap():
    container, url = _start_pg()
    try:
        # a. 新链基线：到 down_revision 为止，5.0.2 是 active 的
        up = _alembic(url, "upgrade", DOWN_REVISION)
        assert up.returncode == 0, up.stderr
        with _connect(url) as conn, conn.cursor() as cur:
            assert _is_active(cur, *MONKEY_LAUNCH) is True

        # b. 模拟旧代码留下的缺陷态：目标行存在、却没有任何 active 版本
        with _connect(url) as conn, conn.cursor() as cur:
            _set_active(cur, *MONKEY_LAUNCH, False)
            cur.execute(
                "SELECT COUNT(*) FROM script WHERE name = %s AND is_active",
                (MONKEY_LAUNCH[0],),
            )
            assert cur.fetchone()[0] == 0, "前置：该脚本此刻无 active 版本"
            conn.commit()

        # c. upgrade head → 自愈
        head = _alembic(url, "upgrade", "head")
        assert head.returncode == 0, head.stderr
        with _connect(url) as conn, conn.cursor() as cur:
            assert _is_active(cur, *MONKEY_LAUNCH) is True, "无 active 版本时必须自愈"

        # d. downgrade -1 → no-op（自愈没有逆操作，翻回去只会制造新空档）
        down = _alembic(url, "downgrade", DOWN_REVISION)
        assert down.returncode == 0, down.stderr
        with _connect(url) as conn, conn.cursor() as cur:
            assert _is_active(cur, *MONKEY_LAUNCH) is True

        # e. 再次 upgrade head → 幂等
        again = _alembic(url, "upgrade", "head")
        assert again.returncode == 0, again.stderr
        with _connect(url) as conn, conn.cursor() as cur:
            assert _is_active(cur, *MONKEY_LAUNCH) is True

        # f. 反向对照：管理员有意停用、但该脚本另有 active 版本 → 不得改写
        with _connect(url) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO script (name, display_name, category, script_type, "
                " version, nfs_path, content_sha256, param_schema, default_params, "
                " is_active, created_at, updated_at) "
                "VALUES (%s, %s, 'device', 'python', '9.9.9', '/tmp/x', %s, "
                " CAST('{}' AS jsonb), CAST('{}' AS jsonb), true, now(), now())",
                (MONKEY_LAUNCH[0], MONKEY_LAUNCH[0], "0" * 64),
            )
            _set_active(cur, *MONKEY_LAUNCH, False)
            conn.commit()
        assert _alembic(url, "upgrade", "head").returncode == 0
        with _connect(url) as conn, conn.cursor() as cur:
            assert _is_active(cur, *MONKEY_LAUNCH) is False, (
                "另有 active 版本时不得覆盖人工停用决策"
            )
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)


@docker_ready
def test_replay_fails_while_deactivated_version_still_referenced():
    container, url = _start_pg()
    try:
        assert _alembic(url, "upgrade", DOWN_REVISION).returncode == 0
        with _connect(url) as conn, conn.cursor() as cur:
            _set_active(cur, *GPU_SETUP_DISABLED, False)
            _add_plan_step_referencing(cur, *GPU_SETUP_DISABLED)
            conn.commit()

        # 仍被引用 → 升级必须失败（旧代码是静默停用）
        blocked = _alembic(url, "upgrade", "head")
        assert blocked.returncode != 0, "被引用时不得静默通过"
        assert "仍被 plan_step 引用" in (blocked.stderr + blocked.stdout)

        # 解除引用 → 升级通过（且不误伤其它行）
        with _connect(url) as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM plan_step WHERE script_name = %s AND script_version = %s",
                GPU_SETUP_DISABLED,
            )
            conn.commit()
        assert _alembic(url, "upgrade", "head").returncode == 0
        with _connect(url) as conn, conn.cursor() as cur:
            assert _is_active(cur, *GPU_SETUP_DISABLED) is False
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
