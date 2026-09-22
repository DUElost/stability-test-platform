"""ADR-0051 Phase 2a：脚本版本目录 → 确定性包 → tool_manifest 登记的仓库侧契约。

- 打包器：显式成员列表 / ``python=None`` 登记；
- manifest 门禁：``python: null`` 绿、``""`` 红；
- check_script_packages：真实树与真实 manifest 等价、幂等登记（与 ``script_catalog``
  的入口/枚举/legacy 对拍在 ``backend/tests/services/test_script_catalog_package_backfill.py``——
  仓库级测试目录不导入 backend）；
- build_bundle 随身携带 ``tool_manifest.json``。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


packer = _load("package_tool_asset_2a", "tools/dev/package_tool_asset.py")
gate = _load("check_tool_manifest_2a", "tools/dev/check_tool_manifest.py")
checker = _load("check_script_packages_2a", "tools/dev/check_script_packages.py")


def _version_dir(root: Path, name: str, version: str, body: str = "print('x')\n") -> Path:
    d = root / name / f"v{version}"
    d.mkdir(parents=True)
    (d / "_adb.py").write_text("ADB = 1\n", encoding="utf-8")
    (d / f"{name}.py").write_text(body, encoding="utf-8")
    (d / "capabilities.json").write_text("[]\n", encoding="utf-8")
    return d


class TestPackerExplicitFiles:
    def test_explicit_files_override_walk(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text("A\n", encoding="utf-8")
        (src / "stray.log.txt").write_text("noise\n", encoding="utf-8")
        full = packer.build_deterministic_tar_gz(src, tmp_path / "full.tar.gz")
        only_a = packer.build_deterministic_tar_gz(src, tmp_path / "a.tar.gz", files=[Path("a.py")])
        assert full["file_count"] == 2 and only_a["file_count"] == 1
        assert full["package_sha256"] != only_a["package_sha256"]

    def test_register_entry_accepts_none_python(self):
        doc, added = packer.register_entry({"schema_version": 1, "tools": {}}, "fam", "1.0.0", "a" * 64,
                                           packer.artifact_path_for("fam", "1.0.0"), None, "fam.py")
        assert added and doc["tools"]["fam"]["versions"][0]["python"] is None
        assert not gate.lint_manifest(doc)


class TestManifestGatePythonNull:
    def test_null_green_empty_red(self):
        entry = {"version": "1.0.0", "package_sha256": "a" * 64, "artifact": "packages/f/1.0.0.tar.gz",
                 "python": None, "script": "f.py", "retired": False}
        doc = {"schema_version": 1, "tools": {"f": {"versions": [entry]}}}
        assert gate.lint_manifest(doc) == []
        doc["tools"]["f"]["versions"][0]["python"] = ""
        assert any("python" in e for e in gate.lint_manifest(doc))


class TestRealTreeRegistered:
    """真实树与真实 manifest 必须等价——这就是 tool-manifest 门禁在 CI 里跑的判定。"""

    def test_every_version_dir_registered_and_equivalent(self):
        doc = json.loads((ROOT / "tool_manifest.json").read_text(encoding="utf-8"))
        expected = checker.expected_entries(ROOT / "backend" / "agent" / "scripts", packer)
        assert expected, "树上应有版本目录"
        assert checker.check(doc, expected, ROOT / "backend" / "agent" / "scripts") == []
        assert all(e["python"] is None for k in expected for e in doc["tools"][k[0]]["versions"])

    def test_external_tool_entry_untouched(self):
        doc = json.loads((ROOT / "tool_manifest.json").read_text(encoding="utf-8"))
        sls = doc["tools"]["Start-Log-Scan"]["versions"][0]
        assert sls["python"] == "venv/bin/python" and sls["version"] == "2026.09.22"


class TestCheckerSemantics:
    def test_register_then_check_green_and_idempotent(self, tmp_path):
        root = tmp_path / "scripts"
        _version_dir(root, "fam", "1.0.0")
        _version_dir(root, "fam", "1.0.10")
        _version_dir(root, "fam", "1.0.9")
        exp = checker.expected_entries(root, packer)
        doc, added = checker.register({"schema_version": 1, "tools": {}}, exp, packer)
        assert added == 3
        assert [e["version"] for e in doc["tools"]["fam"]["versions"]] == ["1.0.0", "1.0.9", "1.0.10"]
        assert checker.check(doc, exp, root) == []
        assert checker.register(doc, exp, packer)[1] == 0
        assert gate.lint_manifest(doc) == []

    def test_in_place_edit_is_red(self, tmp_path):
        root = tmp_path / "scripts"
        d = _version_dir(root, "fam", "1.0.0")
        doc, _ = checker.register({"schema_version": 1, "tools": {}}, checker.expected_entries(root, packer), packer)
        (d / "_adb.py").write_text("ADB = 2\n", encoding="utf-8")  # 伴随文件也在整包 sha 内（盲区消失）
        errs = checker.check(doc, checker.expected_entries(root, packer), root)
        assert errs and "重建 sha" in errs[0]

    def test_ghost_entry_red_unless_retired(self, tmp_path):
        root = tmp_path / "scripts"
        _version_dir(root, "fam", "1.0.0")
        exp = checker.expected_entries(root, packer)
        doc, _ = checker.register({"schema_version": 1, "tools": {}}, exp, packer)
        doc["tools"]["fam"]["versions"].append({
            "version": "2.0.0", "package_sha256": "a" * 64, "artifact": "packages/fam/2.0.0.tar.gz",
            "python": None, "script": "fam.py", "retired": False,
        })
        assert any("无对应版本目录" in e for e in checker.check(doc, exp, root))
        doc["tools"]["fam"]["versions"][-1]["retired"] = True
        assert checker.check(doc, exp, root) == []

    def test_publish_writes_layout_and_manifest_copy(self, tmp_path):
        root = tmp_path / "scripts"
        _version_dir(root, "fam", "1.0.0")
        out = tmp_path / "packages"
        exp = checker.expected_entries(root, packer, out_dir=out)
        assert (out / "fam" / "1.0.0.tar.gz").is_file()
        doc, _ = checker.register({"schema_version": 1, "tools": {}}, exp, packer)
        packer.write_site_manifest_copy(doc, out)
        assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == doc


class TestBundleCarriesManifest:
    def test_build_bundle_copies_tool_manifest(self):
        """bundle 根须携带 Git 唯一事实源（scan 从部署树根回填 package_sha256）。"""
        from tools.dev.source_anchor import SourceGuard

        SourceGuard.of_repo_path("tools/release/build_bundle.py").anchored(
            'for extra in ("ruff.toml", "tool_manifest.json"):'
        ).assert_present(
            'shutil.copy2(repo_root / extra, out / extra)',
            why="tool_manifest.json 必须随 bundle 复制到部署树根，否则 bundle 形态下 scan 回填静默跳过",
        )


@pytest.mark.parametrize("v, expected", [("1.3.17", (0, 1, 0, 3, 0, 17)), ("2026.09.22", (0, 2026, 0, 9, 0, 22))])
def test_version_key_numeric(v, expected):
    assert tuple(x for pair in checker.version_key(v) for x in pair) == expected
    assert checker.version_key("1.3.9") < checker.version_key("1.3.17")
