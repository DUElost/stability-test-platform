"""ADR-0033 D0 / D2 门禁接线守卫。"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from tools.dev.source_anchor import SourceGuard

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

    def test_tool_contract_fixture_gate(self):
        """#3094①：门禁如实命名 tool-contract-fixture——绿灯只证 fixture 自检，
        不再被读成「真实新族已过 Tool Contract 准入」。"""
        mod = _load_run_gates()
        assert "tool-contract-fixture" in mod.GATES
        assert "tool-contract" not in mod.GATES, "旧 gate 名未退役（#3094）"
        cmd, cwd, _ = mod.GATES["tool-contract-fixture"]
        assert "verify_tool_contract.py" in cmd
        assert cwd == mod.ROOT
        for profile in ("check:quick", "check:pr"):
            assert "tool-contract-fixture" in mod.PROFILES[profile]

    def test_tool_contract_step_named_honestly(self):
        """#3094①：CI step 名与 S5x 锚点同步为「fixture 自检」。

        否定断言走 `SourceGuard` 锚点助手（#2639 棘轮）：先证明被扫文件仍是真源
        （step 仍调用 `--self-test`），再断言旧名不出现——否则改名/搬走后断言会恒真。
        """
        ci = SourceGuard.of_repo_path(".github/workflows/ci.yml").anchored(
            "python tools/dev/verify_tool_contract.py --self-test"
        )
        ci.assert_present("ADR-0033 Tool Contract fixture 自检", why="#3094① 新 step 名")
        ci.assert_absent("ADR-0033 Tool Contract 检查", why="#3094① 旧 step 名不得回潮")
        surface = SourceGuard.of_repo_path(
            "tools/dev/check_governance_surface.py"
        ).anchored("tool-contract-fixture")
        surface.assert_present(
            '"tool-contract-fixture": ("ci.yml", "ADR-0033 Tool Contract fixture 自检")',
            why="#3094① S5x 锚点须随改名同步，否则 governance surface 会红",
        )

    def test_verify_tool_contract_has_no_skip_switch(self):
        """#3094②：STP_VERIFY_TOOL_CONTRACT 跳过开关已删——env 置 0 也必须真跑。

        否定断言同样走 `SourceGuard`：锚点 `def main()` 证明读的仍是验证器本体。
        """
        src = SourceGuard.of_repo_path("tools/dev/verify_tool_contract.py").anchored(
            "def main()"
        )
        src.assert_absent("STP_VERIFY_TOOL_CONTRACT", why="#3094② 跳过开关不得回潮")
        env = {**os.environ, "STP_VERIFY_TOOL_CONTRACT": "0"}
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "dev" / "verify_tool_contract.py")],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, f"env=0 时脚本未真跑：{proc.stderr}"
        assert "SKIP" not in (proc.stdout + proc.stderr), "仍在打印 SKIP（#3094②）"
