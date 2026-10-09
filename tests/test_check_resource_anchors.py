# -*- coding: utf-8 -*-
"""B4/G2（#3321 / #3601 v1.1 §1.2 C1、§4.3）：资源 authority 离线守卫的 checker 与接线守卫。

背景：#3320/#3321 的 F1 形态（脚本发布位置被当成宿主资源 authority）已有四个 setup
真实消费者被 G1a（PR #3605）修复；本文件守住 **checker 本身与门禁接线** 有牙齿：

- 全量 census 在真实仓库为绿，且声明面覆盖 §1.4 的 26 项（10 项函数级 legacy）；
- 隔离副本里恢复一个 G1a 错根 / 拆工具绑定 / 删包内伴随文件 / 让 dead helper 可达 /
  抽掉声明条目 → 从 checker 入口必须变红，恢复后转绿；
- `--base` 不可解析必须报不可验证（退出 2），不是空 diff 成功；
- `scripts/run_gates.py` 既有 tool-manifest gate 与 `.github/workflows/ci.yml` 既有
  manifest step 使用同一入口/参数（不新建 profile / required job）。

所有变异只在 `tmp_path` 隔离副本上进行；不动真实实现。
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "dev" / "check_resource_anchors.py"
CONTRACT = ROOT / "tools" / "dev" / "resource_anchor_contract.json"

#: checker 的最小扫描面（隔离副本只需这些，不需要完整仓库）。
COPY_PATHS = (
    "backend/agent/config.py",
    "backend/agent/aimonkey_paths.py",
    "backend/agent/contracts/pipeline_validator.py",
    "backend/agent/tool_cache.py",
    "backend/agent/tool_requirements.py",
    "backend/agent/install_agent.sh",
    "backend/services/host_updater.py",
    "tool_manifest.json",
    "tools/dev/resource_anchor_contract.json",
)


def _load_checker():
    name = "check_resource_anchors_under_test"
    if name in sys.modules:  # dataclass 字符串注解需要模块注册进 sys.modules
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, CHECKER)
    assert spec and spec.loader, "无法加载 checker"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_run_gates():
    spec = importlib.util.spec_from_file_location("run_gates_under_test_3321", ROOT / "scripts" / "run_gates.py")
    assert spec and spec.loader, "无法加载 scripts/run_gates.py"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_checker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), *args], capture_output=True, text=True, cwd=ROOT, check=False
    )


def _base_ref() -> str:
    """可选基线：优先 origin/main；CI 的 pr-agent-tests job 是浅克隆（fetch-depth 默认 1）
    时回退 HEAD——gate/CI 侧在 lint job（fetch-depth: 0）取 PR base，本测试只要求
    「同一入口 + 同一参数形状」在两种 checkout 下都可执行。"""
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet", "origin/main"],
        capture_output=True,
        text=True,
        check=False,
    )
    return "origin/main" if proc.returncode == 0 else "HEAD"


def _copy_repo_subset(dest: Path) -> Path:
    shutil.copytree(
        ROOT / "backend" / "agent" / "scripts",
        dest / "backend" / "agent" / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for rel in COPY_PATHS:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    return dest


def _errors_of(copy: Path) -> list[str]:
    mod = _load_checker()
    return mod.analyze(copy, mod.load_contract(copy)).errors


class TestContractCensus:
    """声明面必须覆盖 §1.4 的 26 项逻辑定位点（10 项函数级 legacy，不得文件级豁免）。"""

    def test_ids_unique_and_cover_declared_census(self):
        doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
        ids = [a["id"] for a in doc["anchors"]]
        assert len(ids) == len(set(ids)), f"锚 id 重复：{ids}"
        assert set(ids) == {f"A{i:02d}" for i in range(1, 27)}, f"§1.4 26 项未全覆盖：{sorted(ids)}"

    def test_legacy_entries_are_function_level_with_reason_and_exit(self):
        doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
        legacy = [a for a in doc["anchors"] if a.get("legacy")]
        assert len(legacy) == 10, f"legacy 豁免应为 10 项（A02/03/05/06/08/09/11/12/17/18），实际 {len(legacy)}"
        for anchor in legacy:
            for locator in anchor["locators"]:
                assert locator["type"] == "function", f"{anchor['id']} 不是函数级豁免：{locator}"
            meta = anchor["legacy"]
            assert meta.get("issue") == "#3320", f"{anchor['id']} 未引用 #3320"
            assert meta.get("reason"), f"{anchor['id']} 缺理由"
            assert meta.get("removal_condition"), f"{anchor['id']} 缺删除/重新判定条件"

    def test_four_specialty_families_declared(self):
        doc = json.loads(CONTRACT.read_text(encoding="utf-8"))
        assert set(doc["families"]) == {"mtbf", "gpu", "power-cycle", "sleep"}
        for fam, spec in doc["families"].items():
            assert spec["param_key"] and spec["env_key"], f"{fam} 缺 param/env 键"
            assert spec["subdir"], f"{fam} 缺 resources 子目录"


class TestCheckerBehavior:
    def test_self_test_green(self):
        proc = _run_checker("--self-test")
        assert proc.returncode == 0, f"self-test 红：{proc.stderr}"
        assert "[OK]" in proc.stdout

    def test_full_census_green_with_counts(self):
        proc = _run_checker()
        assert proc.returncode == 0, f"真实仓库 census 红：{proc.stderr}"
        assert "26 锚" in proc.stdout, proc.stdout
        assert "legacy 豁免 10" in proc.stdout, proc.stdout
        assert "未解析 0" in proc.stdout, proc.stdout

    def test_base_existing_ref_green(self):
        proc = _run_checker("--base", _base_ref())
        assert proc.returncode == 0, f"--base 红：{proc.stderr}"

    def test_base_unresolvable_is_unverifiable_not_empty_diff(self):
        proc = _run_checker("--base", "refs/heads/no-such-ref-3321")
        assert proc.returncode == 2, f"base 不可解析应退出 2（不可验证），实际 {proc.returncode}"
        assert "基线不可验证" in (proc.stdout + proc.stderr)


class TestIsolationMutations:
    """隔离副本变异：checker 必须实际执行并捕获错根；恢复后转绿。"""

    def _copy(self, tmp_path: Path) -> Path:
        return _copy_repo_subset(tmp_path / "repo")

    def test_g1a_wrong_root_restored_is_red_then_green(self, tmp_path):
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            'return Path(AGENT_DIR) / "resources" / "mtbf"',
            'return Path(__file__).resolve().parents[3] / "resources" / "mtbf"',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        errors = _errors_of(copy)
        assert any("A01" in e and "深度" in e for e in errors), f"恢复旧错根应红，实际 {errors}"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_tool_binding_removed_is_red(self, tmp_path):
        copy = self._copy(tmp_path)
        caps = copy / "backend/agent/scripts/flash_firmware/capabilities.json"
        doc = json.loads(caps.read_text(encoding="utf-8"))
        doc.pop("requires_tools", None)
        caps.write_text(json.dumps(doc), encoding="utf-8")
        errors = _errors_of(copy)
        assert any("缺 requires_tools" in e for e in errors), f"撤工具绑定应红，实际 {errors}"

    def test_package_member_missing_is_red(self, tmp_path):
        copy = self._copy(tmp_path)
        (copy / "backend/agent/scripts/gpu_setup/_gpu_stress_loop.sh").unlink()
        errors = _errors_of(copy)
        assert any("包成员缺失" in e for e in errors), f"删包内伴随文件应红，实际 {errors}"

    def test_dead_helper_wired_is_red(self, tmp_path):
        copy = self._copy(tmp_path)
        entry = copy / "backend/agent/scripts/mtbf_check/mtbf_check.py"
        entry.write_text(
            "from _lib import resources_dir\n\n\ndef main() -> None:\n    resources_dir({})\n\n\n"
            'if __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        errors = _errors_of(copy)
        assert any("现可从族入口到达" in e for e in errors), f"dead helper 被接入应红，实际 {errors}"

    def test_alias_import_wiring_is_red_then_green(self, tmp_path):
        """#3615 复核 P2-1：`from _lib import resources_dir as rd` 不得绕过可达性。"""
        copy = self._copy(tmp_path)
        entry = copy / "backend/agent/scripts/mtbf_check/mtbf_check.py"
        original = entry.read_text(encoding="utf-8")
        entry.write_text(
            "from _lib import resources_dir as rd\n\n\ndef main() -> None:\n    rd({})\n\n\n"
            'if __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        errors = _errors_of(copy)
        assert any("现可从族入口到达" in e for e in errors), f"别名导入接入应红，实际 {errors}"
        assert any("以别名导入 legacy helper" in e for e in errors), f"别名导入路径应红，实际 {errors}"
        entry.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_wildcard_and_getattr_wiring_is_red(self, tmp_path):
        """通配导入 / getattr 动态引用不得让调用面静默消失。"""
        copy = self._copy(tmp_path)
        entry = copy / "backend/agent/scripts/mtbf_check/mtbf_check.py"
        original = entry.read_text(encoding="utf-8")
        entry.write_text(
            "from _lib import *\n\n\ndef main() -> None:\n    resources_dir({})\n\n\n"
            'if __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        assert any("import *" in e for e in _errors_of(copy)), "通配导入应红"
        entry.write_text(
            'import _lib\n\n\ndef main() -> None:\n    getattr(_lib, "resources_dir")({})\n\n\n'
            'if __name__ == "__main__":\n    main()\n',
            encoding="utf-8",
        )
        assert any("getattr 动态引用" in e for e in _errors_of(copy)), "getattr 引用应红"
        entry.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_wrong_return_root_is_red_then_green(self, tmp_path):
        """#3615 复核 P2-2：保留正确赋值但返回错误路径必须红。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            '    return Path(AGENT_DIR) / "resources" / "mtbf"',
            '    _good = Path(AGENT_DIR) / "resources" / "mtbf"\n    return Path("/tmp/incorrect")',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        errors = _errors_of(copy)
        assert any("未出现在返回位置" in e for e in errors), f"错误返回根应红，实际 {errors}"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_param_override_removed_is_red_then_green(self, tmp_path):
        """#3615 复核 P2-3：只留 env、删掉 param 通道必须红。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            '    base = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", "")',
            '    base = env("STP_MTBF_RESOURCES_DIR", "")',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        errors = _errors_of(copy)
        assert any("显式参数 override 通道被删除" in e for e in errors), f"删 param 应红，实际 {errors}"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_env_first_precedence_is_red(self, tmp_path):
        """param > env 语义被反转必须红。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            '    base = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", "")',
            '    base = env("STP_MTBF_RESOURCES_DIR", "") or cfg.get("mtbf_resources_dir")',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        assert any("env 先于 param" in e for e in _errors_of(copy)), "env 优先应红"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_extra_suffix_root_is_red_then_green(self, tmp_path):
        """复审 R1：资源根尾部多余片段（root 必须是 ('resources', subdir) 结尾）必须红。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            '    return Path(AGENT_DIR) / "resources" / "mtbf"',
            '    return Path(AGENT_DIR) / "resources" / "mtbf" / "unexpected"',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        assert any("片段必须恰为" in e for e in _errors_of(copy)), "多余后缀应红"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_function_level_alias_import_is_red_then_green(self, tmp_path):
        """复审 R2：函数体内 `from _lib import x as rd` 不得让调用从可达性图消失。"""
        copy = self._copy(tmp_path)
        entry = copy / "backend/agent/scripts/mtbf_check/mtbf_check.py"
        original = entry.read_text(encoding="utf-8")
        entry.write_text(
            "def main():\n"
            "    from _lib import resources_dir as rd\n"
            "    print(rd({}))\n\n"
            'if __name__ == "__main__":\n'
            "    main()\n",
            encoding="utf-8",
        )
        errors = _errors_of(copy)
        assert any("现可从族入口到达" in e for e in errors), f"函数内别名导入应红，实际 {errors}"
        entry.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_variable_env_first_is_red_and_param_first_green(self, tmp_path):
        """复审 R3：经局部变量反转优先级必须红；等价 param 先行写法必须保持绿。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        env_first = original.replace(
            '    base = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", "")',
            '    base = env("STP_MTBF_RESOURCES_DIR", "")\n'
            '    param_value = cfg.get("mtbf_resources_dir")\n'
            "    base = base or param_value",
        )
        assert env_first != original, "变异未生效（测试锚点漂移）"
        lib.write_text(env_first, encoding="utf-8")
        assert any("env 先于 param" in e for e in _errors_of(copy)), "变量 env 优先应红"
        param_first = original.replace(
            '    base = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", "")',
            '    param_value = cfg.get("mtbf_resources_dir")\n'
            '    base = param_value or env("STP_MTBF_RESOURCES_DIR", "")',
        )
        lib.write_text(param_first, encoding="utf-8")
        assert _errors_of(copy) == [], "变量 param 先行应保持绿"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_wrong_root_prefix_is_red_then_green(self, tmp_path):
        """复审 S1：根片段必须恰为 ('resources', subdir)——前缀多余片段同样红。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            '    return Path(AGENT_DIR) / "resources" / "mtbf"',
            '    return Path(AGENT_DIR) / "unexpected" / "resources" / "mtbf"',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        assert any("片段必须恰为" in e for e in _errors_of(copy)), "前缀多余片段应红"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_alias_reuse_across_functions_is_red_then_green(self, tmp_path):
        """复审 S2：两个函数复用同一别名不得 last-write-wins 掩盖真实调用。"""
        copy = self._copy(tmp_path)
        entry = copy / "backend/agent/scripts/mtbf_check/mtbf_check.py"
        original = entry.read_text(encoding="utf-8")
        entry.write_text(
            "def main():\n"
            "    from _lib import resources_dir as rd\n"
            "    print(rd({}))\n\n"
            "def unused():\n"
            "    from _lib import sha256_file as rd\n"
            '    return rd("unused")\n\n'
            'if __name__ == "__main__":\n'
            "    main()\n",
            encoding="utf-8",
        )
        errors = _errors_of(copy)
        assert any("现可从族入口到达" in e for e in errors), f"别名复用下的真实调用应红，实际 {errors}"
        assert any("同一别名多来源" in e for e in errors), f"多来源别名应要求人工分类，实际 {errors}"
        entry.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_unused_param_first_chain_cannot_mask_env_first(self, tmp_path):
        """复审 S3：未被消费的 param-first 链不得作为证明；实际 env-first 必须红。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            '    base = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", "")',
            '    unused = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", "")\n'
            '    base = env("STP_MTBF_RESOURCES_DIR", "") or cfg.get("mtbf_resources_dir")',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        assert any("env 先于 param" in e for e in _errors_of(copy)), "未使用链掩盖应红"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_overwritten_param_first_assignment_is_red_then_green(self, tmp_path):
        """复审 T1：正确 param-first 赋值被同名 env 覆盖后不得再作消费证明。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            '    base = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", "")',
            '    base = cfg.get("mtbf_resources_dir") or env("STP_MTBF_RESOURCES_DIR", "")\n'
            '    base = env("STP_MTBF_RESOURCES_DIR", "")',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        assert any("消费值只读取 env" in e for e in _errors_of(copy)), "同名覆盖应红"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_variable_propagated_literal_return_is_red_then_green(self, tmp_path):
        """复审 T2：错误 literal 根经局部变量返回与直接返回同判。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        mutated = original.replace(
            "    return Path(base) / project",
            '    wrong = Path("/tmp/incorrect")\n'
            "    return wrong / project",
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        assert any("经局部变量返回硬编码路径" in e for e in _errors_of(copy)), "变量传播字面量应红"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_unproven_conditional_not_greenlit_by_other_point(self, tmp_path):
        """复审 U1：守卫点正确 param-first 不得放行另一消费点的 env-first 条件表达式。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        guard = "    if not base:\n        base = _default_resources_root()"
        mutated = original.replace(
            guard,
            guard
            + "\n"
            + '    base = env("STP_MTBF_RESOURCES_DIR", "") if env("STP_MTBF_RESOURCES_DIR", "") '
            + 'else cfg.get("mtbf_resources_dir")',
        )
        assert mutated != original, "变异未生效（测试锚点漂移）"
        lib.write_text(mutated, encoding="utf-8")
        errors = _errors_of(copy)
        assert any("env 先于 param" in e for e in errors), f"条件表达式 env 优先应红，实际 {errors}"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_fallback_guard_direction_must_be_empty_override(self, tmp_path):
        """复审 U2：``if base: default()`` 不得当作惰性守卫；``if not base`` / else 空分支保持绿。"""
        copy = self._copy(tmp_path)
        lib = copy / "backend/agent/scripts/mtbf_setup/_lib.py"
        original = lib.read_text(encoding="utf-8")
        reversed_guard = original.replace(
            "    if not base:\n        base = _default_resources_root()",
            "    if base:\n        base = _default_resources_root()",
        )
        assert reversed_guard != original, "变异未生效（测试锚点漂移）"
        lib.write_text(reversed_guard, encoding="utf-8")
        assert any("急切求值" in e for e in _errors_of(copy)), "守卫方向反转应红"
        else_form = original.replace(
            "    if not base:\n        base = _default_resources_root()",
            "    if base:\n"
            "        pass\n"
            "    else:\n"
            "        base = _default_resources_root()",
        )
        lib.write_text(else_form, encoding="utf-8")
        assert _errors_of(copy) == [], "if/else 空分支等价写法应绿"
        lib.write_text(original, encoding="utf-8")
        assert _errors_of(copy) == [], "恢复后应绿"

    def test_declaration_hole_is_red(self, tmp_path):
        copy = self._copy(tmp_path)
        contract_path = copy / "tools/dev/resource_anchor_contract.json"
        doc = json.loads(contract_path.read_text(encoding="utf-8"))
        doc["anchors"] = [a for a in doc["anchors"] if a["id"] != "A02"]
        contract_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        errors = _errors_of(copy)
        assert any("未声明候选" in e for e in errors), f"声明漏枚举应红，实际 {errors}"

    def test_copy_without_git_base_is_unverifiable(self, tmp_path):
        copy = self._copy(tmp_path)
        proc = subprocess.run(
            [sys.executable, str(CHECKER), "--repo-root", str(copy), "--base", "origin/main"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 2, f"非 git 副本上 --base 应报不可验证，实际 {proc.returncode}"
        assert "基线不可验证" in (proc.stdout + proc.stderr)


class TestGateWiring:
    """接线有牙齿：既有 tool-manifest gate 与 CI manifest step 用同一入口与参数。"""

    def test_tool_manifest_gate_runs_checker_selftest_and_base(self):
        mod = _load_run_gates()
        cmd, cwd, _env = mod.GATES["tool-manifest"]
        assert "tools/dev/check_resource_anchors.py --self-test" in cmd, cmd
        assert "tools/dev/check_resource_anchors.py --base" in cmd, cmd
        assert cwd == mod.ROOT

    def test_gate_reaches_quick_and_pr_profiles(self):
        mod = _load_run_gates()
        for profile in ("check:quick", "check:pr"):
            assert "tool-manifest" in mod.PROFILES[profile], f"tool-manifest 未纳入 {profile}"

    def test_ci_manifest_step_has_same_invocations(self):
        text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        start = text.index("ADR-0033 tool_manifest 检查")
        end = text.index("- name:", start + 10)
        block = text[start:end]
        assert "check_resource_anchors.py --self-test" in block, block
        assert "check_resource_anchors.py \\" in block and "--base" in block, block
        assert "github.base_ref" in block, "CI 侧应与 tool_manifest 同模式取 PR base"

    def test_gate_entry_commands_pass_on_real_repo(self):
        """与 gate 同一命令形状（self-test + --base）在真实 checkout 可执行且为绿。"""
        for args in (("--self-test",), ("--base", _base_ref())):
            proc = _run_checker(*args)
            assert proc.returncode == 0, f"gate 同款命令 {args} 红：{proc.stderr}"
