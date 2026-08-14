#!/usr/bin/env python3
"""STP 质量门禁单一入口：本地矩阵先行，CI 侧后续逐 job 接入对应 profile。

用法:
    python scripts/run_gates.py check:quick    # 最快一轮（纯静态，含 knip）
    python scripts/run_gates.py check:pr       # 推送前默认：与 PR CI 现有检查逐项重叠
    python scripts/run_gates.py check:full     # 夜间全量：与 main 全量 CI 一致
    python scripts/run_gates.py --list

设计约束（与 ci.yml 现状一一对应，不改变任何门禁的语义）:
- 本地默认不跑 PG 套件 / vitest / build / docker —— 这些归 check:full，
  白天全量 CI 只在夜间出现（注意力优先）。
- 每个 gate 顺序执行，失败即停（单人场景默认合理）。
- 用 `python -m` 形式调用（ruff/pytest），保证落到当前解释器的工具链，
  规避「裸 pytest 落到另一套解释器」的历史坑。
- CI 侧尚未调用本脚本（接入见 docs/notes/process/2026-08-14-repo-gate-runner.md）；
  脚本不可变门禁的 base 由环境变量 STP_GATE_BASE_REF 覆盖（CI 用 PR base）。
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, "frontend")
PY = sys.executable  # 用当前解释器跑 -m，规避 PATH 落到别的 python
BASE_REF = os.environ.get("STP_GATE_BASE_REF", "origin/main")

# 仅 agent-tests 使用：部分 agent 测试模块 import 时会解析 DATABASE_URL，
# 但不会真正连接；与全量 backend-test 保持一致的环境可避免收集期 RuntimeError。
# PG 门禁（backend-tests / integration）不传 env：本地由 conftest 走
# testcontainers 隔离库（或本地配置），CI 侧由 job 级 env 自行设置。
AGENT_TEST_ENV = {
    "TESTING": "1",
    "JWT_SECRET_KEY": "ci-test-secret-key",
    "DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test",
    "TEST_DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test",
}

# 顺序即执行顺序；check:full = 全部按此顺序。
GATES = {
    "ruff": (
        f"{PY} -m ruff check backend/ tools/ scripts/",
        ROOT,
        None,
    ),
    "eslint": (
        "npm run lint -- --max-warnings 0",
        FRONTEND,
        None,
    ),
    "tsc": (
        "npm run type-check",
        FRONTEND,
        None,
    ),
    "knip": (
        "npm run knip",
        FRONTEND,
        None,
    ),
    "compileall": (
        f"{PY} -m compileall -q backend/ tools/ scripts/",
        ROOT,
        None,
    ),
    "pollution": (
        "find backend tools scripts frontend/src -type f "
        "\\( -name '*.py' -o -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx' \\) "
        "-not -path '*/resources/*' -not -path '*/__pycache__/*' -not -path '*/node_modules/*' "
        f"| xargs {PY} tools/dev/collapse-blank-pollution.py --check -q",
        ROOT,
        None,
    ),
    "immutability": (
        f"{PY} tools/dev/check-script-version-immutability.py --base {BASE_REF}",
        ROOT,
        None,
    ),
    "agent-tests": (
        f"{PY} -m pytest backend/agent/tests/ -q",
        ROOT,
        AGENT_TEST_ENV,
    ),
    # ── 以下仅 check:full ──
    "backend-tests": (
        f"{PY} -m pytest backend/tests/ -v",
        ROOT,
        None,
    ),
    "integration": (
        f"{PY} -m pytest "
        "backend/tests/integration/test_main_chain_happy_path.py "
        "backend/tests/integration/test_pending_timeout_socketio.py "
        "backend/tests/integration/test_plan_chain_e2e.py "
        "backend/tests/test_seed_and_smoke.py -v",
        ROOT,
        None,
    ),
    "repo-tests": (
        f"{PY} -m pytest tests/ -v",
        ROOT,
        None,
    ),
    "vitest": (
        "npx vitest run",
        FRONTEND,
        None,
    ),
    "frontend-build": (
        "npm run build",
        FRONTEND,
        None,
    ),
    "docker-build": (
        "docker build -f Dockerfile.backend -t stability-backend . && "
        "docker build -f Dockerfile.frontend -t stability-frontend .",
        ROOT,
        None,
    ),
}

PROFILES = {
    "check:quick": ["ruff", "eslint", "tsc", "knip", "compileall"],
    "check:pr": [
        "ruff", "eslint", "tsc", "knip", "compileall",
        "pollution", "immutability", "agent-tests",
    ],
    "check:full": None,  # = 全部，按 GATES 顺序
}


def run_gate(name: str, cmd: str, cwd: str, env: dict | None) -> bool:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    print(f"\n== {name} ==", flush=True)
    proc = subprocess.run(cmd, shell=True, cwd=cwd, env=full_env)
    return proc.returncode == 0


def main() -> int:
    if "--list" in sys.argv:
        for profile, gate_names in PROFILES.items():
            print(f"{profile} -> {gate_names or 'all gates'}")
        return 0
    profile = next(
        (arg for arg in sys.argv[1:] if arg.startswith("check:")),
        "check:pr",
    )
    if profile not in PROFILES:
        print(f"unknown profile: {profile}", file=sys.stderr)
        return 2
    gate_names = PROFILES[profile] or list(GATES)
    for name in gate_names:
        cmd, cwd, env = GATES[name]
        if not run_gate(name, cmd, cwd, env):
            print(f"\n[FAIL] {name} ({profile})", file=sys.stderr)
            return 1
    print(f"\n[OK] {profile} ({len(gate_names)} gates)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
