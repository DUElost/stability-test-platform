"""#1938：run_gates `schema-at-head` 门禁接线守卫。

背景：2026-09-14 生产控制面 pull 到含新迁移（#1907）的 main 后未跑迁移即
重启——所有 host 查询 500（累计 237,315 次）、agent 心跳停更、错误日志
~170KB/s 刷屏。`tools/dev/check_alembic_at_head.py`（#1882）已存在，但
`scripts/run_gates.py` 的任何 profile 都不调用它，本地 routine 门禁全绿放行。

本守卫把接线锚成结构断言：gate 存在且指向检查脚本、纳入 quick/pr、不被
check:full 排除、且在 profile 列表首位（fail-fast）。判据直接 import
run_gates 模块读 GATES/PROFILES/PROFILES 结构，不做文本正则。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_GATES = ROOT / "scripts" / "run_gates.py"
GATE = "schema-at-head"
CHECKER = "tools/dev/check_alembic_at_head.py"


def _load_run_gates():
    spec = importlib.util.spec_from_file_location("run_gates_under_test", RUN_GATES)
    assert spec and spec.loader, "无法加载 scripts/run_gates.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestSchemaAtHeadGateWiring:
    def test_gate_targets_alembic_checker_from_repo_root(self):
        """gate 必须调用 #1882 的检查脚本，且在仓库根执行（生产工作树语义）。"""
        mod = _load_run_gates()
        assert GATE in mod.GATES, f"{GATE} 未注册进 GATES"
        cmd, cwd, _env = mod.GATES[GATE]
        assert CHECKER in cmd, f"{GATE} 未指向 {CHECKER}：{cmd!r}"
        assert cwd == mod.ROOT, f"{GATE} 的 cwd 必须是仓库根：{cwd!r}"

    def test_gate_in_quick_and_pr_profiles(self):
        """routine（quick）与推前（pr）都必须跑到，否则半部署态仍可静默通过。"""
        mod = _load_run_gates()
        for profile in ("check:quick", "check:pr"):
            assert GATE in mod.PROFILES[profile], f"{GATE} 未纳入 {profile}"

    def test_gate_runs_first_for_fail_fast(self):
        """对齐探针在 quick/pr 列表首位：未配库时仅秒级跳过，配库时尽早拦。"""
        mod = _load_run_gates()
        for profile in ("check:quick", "check:pr"):
            assert mod.PROFILES[profile][0] == GATE, (
                f"{GATE} 应在 {profile} 首位（fail-fast），实际顺序："
                f"{mod.PROFILES[profile][:3]}"
            )

    def test_gate_included_in_full_profile(self):
        """check:full = GATES - FULL_EXCLUDE，不得被排除。"""
        mod = _load_run_gates()
        assert GATE not in mod.FULL_EXCLUDE
        assert GATE in [g for g in mod.GATES if g not in mod.FULL_EXCLUDE]
