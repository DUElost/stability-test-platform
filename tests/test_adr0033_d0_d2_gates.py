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


def _load_script_packages():
    spec = importlib.util.spec_from_file_location(
        "check_script_packages_under_test", ROOT / "tools" / "dev" / "check_script_packages.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAdr0033GateWiring:
    def test_d0_classification_moved_to_kind_registration(self, tmp_path):
        """ADR-0051 D8：D0 归类门禁（check_new_script_family，提交说明里的归类声明）退役——归类由
        tool_manifest.json 族级 kind 承担，check_script_packages 判三态。退役前提（替代门禁已生效）
        在这里以行为钉住，而不是只断言「旧门禁不在了」。"""
        gates = _load_run_gates()
        assert "new-script-family" not in gates.GATES
        for profile in ("check:quick", "check:pr"):
            assert "new-script-family" not in gates.PROFILES[profile]
        assert not (ROOT / "tools" / "dev" / "check_new_script_family.py").exists()

        csp = _load_script_packages()
        packer = csp._load_packer()
        root = tmp_path / "scripts"
        (root / "newfam").mkdir(parents=True)
        (root / "newfam" / "newfam.py").write_text("print(1)\n", encoding="utf-8")
        rebuilt = csp.rebuild_all(root, packer)
        empty = {"schema_version": 1, "tools": {}}
        # ① 新族树未登记 = 未声明归类 → 红
        assert any("登记即归类声明" in e for e in csp.check(empty, rebuilt, root))
        # ② 登记为 kind=tool 却挂树 = 外部工具源码入仓 → 红
        as_tool, _ = packer.register_entry(dict(empty, tools={}), "newfam", "1.0.0",
                                           rebuilt["newfam"]["package_sha256"],
                                           packer.artifact_path_for("newfam", "1.0.0"), None,
                                           rebuilt["newfam"]["script"], kind="tool")
        assert any("外部工具源码不得入仓" in e for e in csp.check(as_tool, rebuilt, root))
        # ③ 登记为 kind=script = 平台自研声明 → 绿
        as_script, _ = packer.register_entry(dict(empty, tools={}), "newfam", "1.0.0",
                                             rebuilt["newfam"]["package_sha256"],
                                             packer.artifact_path_for("newfam", "1.0.0"), None,
                                             rebuilt["newfam"]["script"], kind="script")
        assert csp.check(as_script, rebuilt, root) == []

    def test_tool_contract_gate(self):
        mod = _load_run_gates()
        assert "tool-contract" in mod.GATES
        cmd, cwd, _ = mod.GATES["tool-contract"]
        assert "verify_tool_contract.py" in cmd
        assert cwd == mod.ROOT
        for profile in ("check:quick", "check:pr"):
            assert "tool-contract" in mod.PROFILES[profile]
