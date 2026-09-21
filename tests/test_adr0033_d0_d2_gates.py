"""ADR-0033 D0 / D2 门禁接线守卫。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_GATES = ROOT / "scripts" / "run_gates.py"


def _load_run_gates():
    spec = importlib.util.spec_from_file_location("run_gates_under_test", RUN_GATES)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAdr0033GateWiring:
    def test_new_script_family_gate(self):
        mod = _load_run_gates()
        assert "new-script-family" in mod.GATES
        cmd, cwd, _ = mod.GATES["new-script-family"]
        assert "check_new_script_family.py" in cmd
        assert cwd == mod.ROOT
        for profile in ("check:quick", "check:pr"):
            assert "new-script-family" in mod.PROFILES[profile]

    def test_tool_contract_gate(self):
        mod = _load_run_gates()
        assert "tool-contract" in mod.GATES
        cmd, cwd, _ = mod.GATES["tool-contract"]
        assert "verify_tool_contract.py" in cmd
        assert cwd == mod.ROOT
        for profile in ("check:quick", "check:pr"):
            assert "tool-contract" in mod.PROFILES[profile]
