"""过渡登记簿的仓库侧接线契约（gate 本体判据由 --self-test 红绿双向自证）。

钉的是「别悄悄拆线」：gate 在 profiles、CI step 锚在（S5x 也管，但那里挂了才红，
这里在 PR 级就跑）、真实台账的 id 唯一与 exit 前缀分布。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPECS = [
    ROOT / "tools" / "dev" / "check_transitions.py",
    ROOT / "scripts" / "run_gates.py",
    ROOT / ".github" / "workflows" / "ci.yml",
]


@pytest.fixture(scope="module")
def checker():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_transitions_under_test", ROOT / "tools/dev/check_transitions.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_gate_wired_into_both_profiles_and_ci():
    gates = (ROOT / "scripts/run_gates.py").read_text(encoding="utf-8")
    assert '"transitions": (' in gates
    quick = gates[gates.index('"check:quick"'):gates.index('"check:full"')]
    assert '"transitions"' in quick
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "过渡登记簿检查(ADR-0051)" in ci


def test_real_ledger_shape():
    doc = json.loads((ROOT / "docs/governance/transitions.json").read_text(encoding="utf-8"))
    ids = [t["id"] for t in doc["transitions"]]
    assert len(ids) == len(set(ids))
    assert all(t["exit"].startswith(("adr:", "issue:#")) for t in doc["transitions"])


def test_checker_rejects_expired_active_on_real_ledger(checker, tmp_path):
    """到期执法有牙：把真实台账任一 active 条目 due 改到过去 → 红（不是格式摆设）。"""
    import datetime as dt
    doc = json.loads((ROOT / "docs/governance/transitions.json").read_text(encoding="utf-8"))
    assert doc["transitions"], "台账不得清空（清空即绕过）"
    # 取「任一 active」而非 [0]：条目结项转 done 后 [0] 可能不再 active（done 条目过期不判红），
    # 硬取下标会让本守卫随台账演进静默失效（2026-09-26 flash-tool-dir-env-injection 结项即此形态）
    active = next(t for t in doc["transitions"] if t["status"] == "active")
    active["due"] = "2020-01-01"
    errs = checker.check_ledger(doc, dt.date(2026, 9, 24), ROOT)
    assert any("已到期" in e for e in errs), errs
