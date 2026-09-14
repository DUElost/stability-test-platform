"""Tests for tools/dev/env_inventory.py（#737 文档切片）。"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "dev" / "env_inventory.py"
DOC = ROOT / "docs" / "development" / "environment-variables.md"
PY = sys.executable


def _load_module():
    spec = importlib.util.spec_from_file_location("env_inventory", TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_normalize_default_literals_and_kwargs():
    mod = _load_module()
    assert mod._normalize_default('"300"') == "300"
    assert mod._normalize_default("'abc'") == "abc"
    assert mod._normalize_default("600") == "600"
    assert mod._normalize_default("production_default=180") == "180"
    assert mod._normalize_default("DEFAULT_CONST") == "-"
    assert mod._normalize_default(None) == "-"


def test_scan_reads_covers_all_forms_and_classifies_test_only(tmp_path):
    mod = _load_module()
    backend = tmp_path / "backend"
    (backend / "tests").mkdir(parents=True)
    (backend / "svc.py").write_text(
        "import os\n"
        'A = os.getenv("ZZ_SVC_VAR", "5")\n'
        'B = os.environ.get("ZZ_ENV_GET")\n'
        'C = os.environ["ZZ_BRACKET"]\n'
        'D = _int_env("ZZ_HELPER", production_default=300)\n',
        encoding="utf-8",
    )
    (backend / "tests" / "test_x.py").write_text(
        'import os\nT = os.getenv("ZZ_TEST_ONLY", "1")\n', encoding="utf-8",
    )
    mod.ROOT = tmp_path
    reads = mod.scan_reads(backend)

    assert reads["ZZ_SVC_VAR"]["default"] == "5"
    assert reads["ZZ_ENV_GET"]["default"] == "-"
    assert "ZZ_BRACKET" in reads
    assert reads["ZZ_HELPER"]["default"] == "300"
    assert reads["ZZ_TEST_ONLY"]["test_only"] is True
    assert reads["ZZ_SVC_VAR"]["test_only"] is False


def test_scan_reads_covers_aliased_and_bare_forms(tmp_path):
    """别名 `import os as X` / `from os import getenv` 也必须被清单捕获（防绕过）。"""
    mod = _load_module()
    backend = tmp_path / "backend"
    backend.mkdir(parents=True)
    (backend / "aliased.py").write_text(
        "import os as _probe\n"
        "from os import getenv as _g\n"
        'A = _probe.getenv("ZZ_ALIAS_VAR", "9")\n'
        'B = _g("ZZ_BARE_VAR")\n'
        'C = _probe.environ["ZZ_ALIAS_BRACKET"]\n',
        encoding="utf-8",
    )
    mod.ROOT = tmp_path
    reads = mod.scan_reads(backend)
    assert reads["ZZ_ALIAS_VAR"]["default"] == "9"
    assert "ZZ_BARE_VAR" in reads
    assert "ZZ_ALIAS_BRACKET" in reads


def test_scan_skips_agent_scripts_catalog(tmp_path):
    mod = _load_module()
    backend = tmp_path / "backend"
    (backend / "agent" / "scripts" / "s1" / "v1.0.0").mkdir(parents=True)
    (backend / "agent" / "scripts" / "s1" / "v1.0.0" / "main.py").write_text(
        'import os\nX = os.getenv("ZZ_SCRIPT_VAR")\n', encoding="utf-8",
    )
    mod.ROOT = tmp_path
    reads = mod.scan_reads(backend)
    assert "ZZ_SCRIPT_VAR" not in reads, "版本化脚本目录不应进入运行时清单"


def test_audit_requires_declaration_or_registration(monkeypatch):
    """#737 收口：读取名必须「登记进示例 ∪ 内部声明」二选一。"""
    mod = _load_module()
    monkeypatch.setattr(mod, "_INTERNAL_ONLY", {})
    reads = {"ZZ_FREE": {"default": "1", "locations": [("backend/a.py", 1)], "test_only": False}}

    # ① 未登记且未声明 → 违规
    issues = mod.audit(reads, set())
    assert any("ZZ_FREE" in i and "未登记" in i for i in issues), issues
    # ② 已登记且已声明 → 冲突
    mod._INTERNAL_ONLY["ZZ_FREE"] = "测试声明"
    issues = mod.audit(reads, {"ZZ_FREE"})
    assert any("ZZ_FREE" in i and "仍在内部声明" in i for i in issues), issues
    # ③ 声明但代码已无读取点 → 陈旧
    issues = mod.audit({}, {"ZZ_FREE"})
    assert any("ZZ_FREE" in i and "陈旧" in i for i in issues), issues
    # ④ 声明内部且未登记 → 合规（二选一成立）
    assert mod.audit(reads, set()) == [], mod.audit(reads, set())


def test_repo_doc_inventory_is_in_sync():
    """仓库文档生成块与代码读取名一致（同 run_gates 的 env-inventory 门禁）。"""
    result = subprocess.run(
        [PY, str(TOOL), "--check"], cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "env-inventory:begin" in DOC.read_text(encoding="utf-8")

def test_signature_ignores_line_numbers_and_detects_semantics():
    """#1952 教训：行号平移不算漂移；默认值/登记/类别变化才算。"""
    mod = _load_module()
    base = {"ZZ_X": {"default": "5", "locations": [("backend/a.py", 10)], "test_only": False}}
    shifted = {"ZZ_X": {"default": "5", "locations": [("backend/a.py", 99)], "test_only": False}}
    assert mod.semantic_signature(base, set()) == mod.semantic_signature(shifted, set())

    changed_default = {"ZZ_X": {"default": "6", "locations": [("backend/a.py", 10)], "test_only": False}}
    diff = mod.signature_diff(
        mod.semantic_signature(changed_default, set()),
        mod.semantic_signature(base, set()),
    )
    assert any("默认值变化" in item for item in diff), diff
