#!/usr/bin/env python3
"""STP 质量门禁单一入口：本地矩阵先行，CI 侧后续逐 job 接入对应 profile。

用法:
    python scripts/run_gates.py check:quick    # 最快一轮（纯静态，含 knip）
    python scripts/run_gates.py check:pr       # 推送前默认：与 PR CI 现有检查逐项重叠
    python scripts/run_gates.py check:gov      # 治理面专项（结构 + skill 用量探针）
    python scripts/run_gates.py check:full     # 夜间全量：main 全量 CI 的本地可跑部分
                                               # + 本机专属 gate（FULL_EXCLUDE 除外，#825）
    python scripts/run_gates.py --list

设计约束（与 ci.yml 现状一一对应，不改变任何门禁的语义）:
- 本地默认不跑 PG 套件 / vitest / build / docker —— 这些归 check:full，
  白天全量 CI 只在夜间出现（注意力优先）。pr-migrate 例外地进 check:pr：
  docker 可用则真跑、不可用显式 SKIP（#825——迁移回归是 PR 阶段唯一拦截点）。
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
    # 差异面不变量检查（#855 收口，覆盖图 §7.1 的差集收缩）：context-only
    # 不变量中可静态判定子集的「新增行」检查——S11 的产物侧对偶。
    # 2026-09-07 升格 BLOCK（违规 exit 1；--advisory 留痕放行）：精度以全库
    # 枚举静态验证（观察期对差异面 gate 结构性失效，见该脚本抬头），
    # CI 对应物=ci.yml lint job「差异面不变量检查」step。
    "invariant-diff": (
        f"{PY} tools/dev/check_invariant_diff.py --base {BASE_REF}",
        ROOT,
        None,
    ),
    # 治理面结构门禁（synthesis C-G1 L0）：@import 行内失效等事故的确定性拦截。
    # 纯文本检查、毫秒级；--self-test 正反样例自证见该脚本抬头。
    "gov-surface": (
        f"{PY} tools/dev/check_governance_surface.py --check",
        ROOT,
        None,
    ),
    # Execution Registry 自测（ADR-0034 P1，execution-contract.md §2-§5 纯函数）：
    # 离线红绿双向（scope/overlap/真值表/codec/原子写）；不触网、不写真实 registry。
    "ai-work": (
        f"{PY} tools/dev/ai_work.py --self-test",
        ROOT,
        None,
    ),
    # Harness 摄取矩阵探针（ADR-0034 P2 验收/#855-b 落地）：黑盒双题探针 +
    # EXPECTED 偏离检测（行为漂移监测，含 #857 上游修复对照行）。真实 LLM
    # 会话分钟级 × 外部依赖——仅 check:gov 手跑，不进 quick/pr/full
    # （FULL_EXCLUDE 同步排除，#1046——此前 check:full 实际会跑到本 gate）。
    "harness-ingest": (
        f"{PY} tools/dev/harness_probe.py",
        ROOT,
        None,
    ),
    # P3 drift gate（ADR-0034 §2.7 P3）：freshness/declaration-drift/coverage-mismatch/
    # overlap 顶层 hint，**advisory 不阻塞**（exit 0）——只在 check:full（夜间全量）
    # 留痕输出；不进 quick/pr（守合入路径 ~2min 注意力预算）。转 required 须独立裁决。
    "ai-drift": (
        f"{PY} tools/dev/ai_work.py drift",
        ROOT,
        None,
    ),
    # pr-migrate-empty-db 本地等价（#825/#644）：docker 可用则真跑（空库 alembic
    # upgrade head + ORM schema 比对，postgres:16 一次性容器）；docker 不可用则
    # 显式 SKIP（exit 0 并注明）——推送前能拦迁移回归的环境拦，拦不了的不假绿。
    # 正反样例自证：--self-test
    "pr-migrate": (
        f"{PY} tools/dev/check_pr_migrate.py",
        ROOT,
        None,
    ),
    # public 仓库内网主机地址扫描（#538/#550/#557 收尾）：纯文本正则、秒级。
    # 只拦四段齐全的具体主机地址，CIDR 网段常量与标准地址放行；
    # ADR-0020 脚本目录 / 已锁定迁移 / 测试夹具走白名单。
    # 正反样例自证：--self-test
    "ip-leak": (
        f"{PY} tools/dev/check-internal-ip-leak.py --check -q",
        ROOT,
        None,
    ),
    # Prometheus 告警规则契约（#1257/R14-F11）：规则选择器与 backend 指标
    # 注册表逐条比对——未知指标/标签、直方图裸用基础名即红；promtool 可用时
    # 追加场景触发测试（无 promtool 的机器该子项 skip，结构层恒跑）。
    "prom-alerts": (
        f"{PY} -m pytest tests/test_prometheus_alerts_contract.py -q",
        ROOT,
        None,
    ),
    # skill 用量探针（防建而不用）：--strict 下 ≥14 天零调用 = 门禁红。
    # 空洞处置二选一：删 skill 或改写触发词使其真实可命中。
    # （gov-evals 行为 eval 已于 2026-09-06 移除——S11 锚点承接不变量保全，
    #   残余缺口见 #855 与 docs/notes/simplification/2026-09-06-gov-eval-l1-removal.md）
    "gov-skills": (
        f"{PY} tools/dev/skill_usage_report.py --strict",
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
    "check:quick": ["ruff", "eslint", "tsc", "knip", "compileall", "gov-surface", "ai-work"],
    "check:pr": [
        "ruff", "eslint", "tsc", "knip", "compileall",
        "pollution", "immutability", "invariant-diff",
        "gov-surface", "ip-leak", "prom-alerts", "agent-tests",
        "pr-migrate",
    ],
    # 治理面专项：结构门禁 + skill 用量探针 + Harness 摄取矩阵（手跑，分钟级）
    "check:gov": ["gov-surface", "gov-skills", "harness-ingest"],
    # check:full = main 全量 CI 的本地可跑部分 + 本机专属 gate，但排除
    # 数据源物理仅在本机的 gate（#825：他机跑 check:full 不得确定性红灯）
    # 与须手跑外部依赖的 gate（#1046）。gov-skills 依赖 ~/.claude 会话转录；
    # harness-ingest 每形态一次真实非交互 LLM 会话（分钟级 × 外部依赖），
    # 仅 check:gov 手跑；ai-drift 在无 registry 数据的机器上 no-op 绿，故保留。
    "check:full": None,  # = 全部 GATES - FULL_EXCLUDE，按 GATES 顺序
}

# 显式排除表（#825/#1046）：仅本机数据源/他机必红/须手跑外部依赖的 gate
FULL_EXCLUDE = {"gov-skills", "harness-ingest"}


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
    gate_names = PROFILES[profile] or [g for g in GATES if g not in FULL_EXCLUDE]
    for name in gate_names:
        cmd, cwd, env = GATES[name]
        if not run_gate(name, cmd, cwd, env):
            print(f"\n[FAIL] {name} ({profile})", file=sys.stderr)
            return 1
    print(f"\n[OK] {profile} ({len(gate_names)} gates)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
