#!/usr/bin/env python3
"""check_pr_migrate.py —— CI `pr-migrate-empty-db` 的本地等价 gate（#825/#644）。

复刻 ci.yml 同名 job 的两步：空 PostgreSQL 上 `alembic upgrade head` +
`backend.scripts.check_schema_sync`（ORM schema 比对，#644 P0 复盘口径：
diff ⊆ 基线白名单，防「迁移能跑但与模型漂移」）。

docker 可用 → 起 postgres:16 一次性容器真跑（拦迁移回归）；
docker 不可用 → **显式 SKIP**（exit 0 并大字注明）——本 gate 的设计立场是
「能拦的环境拦、拦不了的不假绿」，与 #825 修复方向（本地覆盖声称与 CI 对齐
或注明豁免）一致。SKIP 不冒充通过：输出语义明确区分。

用法:
    python tools/dev/check_pr_migrate.py           # gate 模式
    python tools/dev/check_pr_migrate.py --self-test   # 纯函数红绿自证
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

PY = sys.executable
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMAGE = "postgres:16"
CONTAINER_PREFIX = "stp-pr-migrate"
PGUSER = "postgres"
PGPASSWORD = "postgres"
PGDB = "stability_test"


def docker_available() -> bool:
    try:
        proc = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                              capture_output=True, text=True, timeout=20)
        return proc.returncode == 0 and proc.stdout.strip() != ""
    except (OSError, subprocess.TimeoutExpired):
        return False


def _docker(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kw)


def wait_ready(container: str, timeout_s: int = 60) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        proc = _docker(["exec", container, "pg_isready", "-U", PGUSER, "-d", PGDB])
        if proc.returncode == 0:
            return True
        time.sleep(2)
    return False


def build_env(port: int) -> dict:
    env = os.environ.copy()
    url = f"postgresql+psycopg://{PGUSER}:{PGPASSWORD}@127.0.0.1:{port}/{PGDB}"
    env.update({
        "TESTING": "1",
        "JWT_SECRET_KEY": "local-gate-secret",
        "DATABASE_URL": url,
        "TEST_DATABASE_URL": url,
    })
    return env


def run_check() -> int:
    if not docker_available():
        print("[SKIP] pr-migrate：docker 不可用——本地不跑空库迁移检查，"
              "以 CI `pr-migrate-empty-db` required check 为准（#825 豁免口径）。")
        return 0

    container = f"{CONTAINER_PREFIX}-{os.getpid()}"
    port = 55000 + os.getpid() % 1000
    pull = _docker(["pull", "-q", IMAGE], timeout=300)
    if pull.returncode != 0:
        print(f"[SKIP] pr-migrate：无法拉取 {IMAGE}（{pull.stderr.strip()[:120]}）——以 CI 为准。")
        return 0
    run = _docker(["run", "-d", "--rm", "--name", container,
                   "-e", f"POSTGRES_USER={PGUSER}", "-e", f"POSTGRES_PASSWORD={PGPASSWORD}",
                   "-e", f"POSTGRES_DB={PGDB}", "-p", f"{port}:5432", IMAGE])
    if run.returncode != 0:
        print(f"[SKIP] pr-migrate：容器启动失败（{run.stderr.strip()[:120]}）——以 CI 为准。")
        return 0
    try:
        if not wait_ready(container):
            print("[FAIL] pr-migrate：postgres 60s 内未就绪", file=sys.stderr)
            return 1
        env = build_env(port)
        # 步骤 1：空库 alembic upgrade head（与 CI 同 working-directory=backend）
        step1 = subprocess.run([PY, "-m", "alembic", "upgrade", "head"],
                               cwd=os.path.join(ROOT, "backend"), env=env)
        if step1.returncode != 0:
            print("[FAIL] pr-migrate：alembic upgrade head 失败", file=sys.stderr)
            return 1
        # 步骤 2：schema 与 ORM 比对（与 CI 同：不加 working-directory，
        # python -m 把 cwd 放 sys.path[0]，backend 包在仓库根）
        step2 = subprocess.run([PY, "-m", "backend.scripts.check_schema_sync"],
                               cwd=ROOT, env=env)
        if step2.returncode != 0:
            print("[FAIL] pr-migrate：schema 与 ORM 模型不一致（check_schema_sync）",
                  file=sys.stderr)
            return 1
        print("[OK] pr-migrate：空库迁移 + schema 比对通过（postgres:16 一次性容器）")
        return 0
    finally:
        _docker(["rm", "-f", container], timeout=60)


def run_self_test() -> int:
    failures: list[str] = []

    def expect(name, cond, should_pass=True):
        if cond != should_pass:
            failures.append(f"{name}: 预期{'绿' if should_pass else '红'}，实际相反")

    # build_env：端口/凭据/覆盖语义
    env = build_env(55042)
    expect("env URL 端口", "127.0.0.1:55042" in env["DATABASE_URL"])
    expect("env TESTING", env["TESTING"] == "1")
    expect("env 不泄漏原 DATABASE_URL", "DATABASE_URL" in env and env["DATABASE_URL"].endswith(PGDB))

    # SKIP 语义：docker 不可用时必须 exit 0 且输出含 SKIP（非静默、非 FAIL）
    import contextlib
    import io
    buf = io.StringIO()
    real = docker_available
    globals()["docker_available"] = lambda: False
    try:
        with contextlib.redirect_stdout(buf):
            code = run_check()
    finally:
        globals()["docker_available"] = real
    expect("SKIP 退出码", code == 0)
    expect("SKIP 输出显式", "[SKIP]" in buf.getvalue())
    expect("SKIP 声明以 CI 为准", "CI" in buf.getvalue())

    if failures:
        for f in failures:
            print(f"[SELFTEST-FAIL] {f}", file=sys.stderr)
        return 1
    print("[OK] check_pr_migrate self-test 通过（env 构造/SKIP 语义 红绿双向）")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return run_self_test()
    return run_check()


if __name__ == "__main__":
    sys.exit(main())
