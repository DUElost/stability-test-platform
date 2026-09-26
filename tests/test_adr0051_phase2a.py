"""ADR-0051 Phase 3：族树 → 确定性包 → tool_manifest 登记的仓库侧契约（`check_script_packages.py`）。

- 打包器：显式成员列表 / `python=None` 登记 / 权限位归一化；
- manifest 门禁：`python: null` 绿、`""` 红；
- checker：真实族树与真实 manifest 最新条目等价；改树未发版本红；残留 v 目录红；退役回落；
  无树条目豁免（Phase 4a 树集）；外部族豁免；publish 只落最新版；
- build_bundle 随身携带 `tool_manifest.json`。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
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


def _tree(root: Path, name: str, body: str = "print('x')\n") -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "_adb.py").write_text("ADB = 1\n", encoding="utf-8")
    (d / f"{name}.py").write_text(body, encoding="utf-8")
    (d / "capabilities.json").write_text("[]\n", encoding="utf-8")
    return d


def _registered(root: Path, families: dict[str, str]) -> tuple[dict, dict]:
    rebuilt = checker.rebuild_all(root, packer)
    doc = {"schema_version": 1, "tools": {}}
    for fam, ver in families.items():
        doc, _ = checker.register(doc, fam, ver, rebuilt[fam], packer)
    return doc, rebuilt


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
        assert added and doc["tools"]["fam"]["kind"] == "script"  # 默认 kind=script（--kind tool 登记外部族）
        assert doc["tools"]["fam"]["versions"][0]["python"] is None
        assert not gate.lint_manifest(doc)


class TestManifestGatePythonNull:
    def test_null_green_empty_red(self):
        entry = {"version": "1.0.0", "package_sha256": "a" * 64, "artifact": "packages/f/1.0.0.tar.gz",
                 "python": None, "script": "f.py", "retired": False}
        doc = {"schema_version": 1, "tools": {"f": {"kind": "script", "versions": [entry]}}}
        assert gate.lint_manifest(doc) == []  # kind=script + python:null = 平台脚本族标准形
        doc["tools"]["f"]["versions"][0]["python"] = ""
        assert any("python" in e for e in gate.lint_manifest(doc))


class TestRealTreeRegistered:
    """真实族树与真实 manifest 最新条目必须等价——这就是 tool-manifest 门禁在 CI 里跑的判定。"""

    def test_every_family_tree_matches_latest_entry(self):
        doc = json.loads((ROOT / "tool_manifest.json").read_text(encoding="utf-8"))
        root = ROOT / "backend" / "agent" / "scripts"
        rebuilt = checker.rebuild_all(root, packer)
        assert rebuilt, "树上应有族树"
        assert checker.check(doc, rebuilt, root) == []
        assert checker.stray_version_dirs(root) == []
        assert all(e["python"] is None for fam in rebuilt for e in doc["tools"][fam]["versions"])

    def test_external_tool_entry_untouched(self):
        doc = json.loads((ROOT / "tool_manifest.json").read_text(encoding="utf-8"))
        sls = doc["tools"]["Start-Log-Scan"]["versions"][0]
        assert sls["python"] == "venv/bin/python" and sls["version"] == "2026.09.22"


class TestCheckerSemantics:
    def test_register_then_check_green_and_idempotent(self, tmp_path):
        root = tmp_path / "scripts"
        _tree(root, "fam")
        doc, rebuilt = _registered(root, {"fam": "1.0.0"})
        assert checker.check(doc, rebuilt, root) == []
        assert checker.register(doc, "fam", "1.0.0", rebuilt["fam"], packer)[1] is False
        assert gate.lint_manifest(doc) == []

    def test_tree_change_without_new_version_is_red_then_register_fixes(self, tmp_path):
        root = tmp_path / "scripts"
        d = _tree(root, "fam")
        doc, _ = _registered(root, {"fam": "1.0.0"})
        (d / "_adb.py").write_text("ADB = 2\n", encoding="utf-8")  # 伴随文件也在整包 sha 内
        rebuilt = checker.rebuild_all(root, packer)
        errs = checker.check(doc, rebuilt, root)
        assert errs and "改了树没发版本" in errs[0]
        with pytest.raises(SystemExit):
            checker.register(doc, "fam", "1.0.0", rebuilt["fam"], packer)  # 版本号不可复用
        doc, added = checker.register(doc, "fam", "1.0.1", rebuilt["fam"], packer)
        assert added and checker.check(doc, rebuilt, root) == []

    def test_latest_is_natural_order_and_retire_falls_back(self, tmp_path):
        root = tmp_path / "scripts"
        d = _tree(root, "fam", "v9\n")
        doc, rebuilt9 = _registered(root, {"fam": "1.0.9"})
        (d / "fam.py").write_text("v10\n", encoding="utf-8")
        rebuilt10 = checker.rebuild_all(root, packer)
        doc, _ = checker.register(doc, "fam", "1.0.10", rebuilt10["fam"], packer)
        assert checker.latest_entry(doc["tools"]["fam"]["versions"])["version"] == "1.0.10"
        assert checker.check(doc, rebuilt10, root) == []
        doc["tools"]["fam"]["versions"][-1]["retired"] = True
        assert any("改了树没发版本" in e for e in checker.check(doc, rebuilt10, root))

    def test_stray_version_dir_and_ghost_family(self, tmp_path):
        root = tmp_path / "scripts"
        d = _tree(root, "fam")
        doc, rebuilt = _registered(root, {"fam": "1.0.0"})
        (d / "v1.0.0").mkdir()
        (d / "v1.0.0" / "leftover.py").write_text("x\n", encoding="utf-8")
        assert any("不得再有版本目录" in e for e in checker.check(doc, rebuilt, root))
        (d / "v1.0.0" / "leftover.py").unlink()
        (d / "v1.0.0" / "__pycache__").mkdir()
        (d / "v1.0.0" / "__pycache__" / "x.pyc").write_bytes(b"x")
        assert checker.check(doc, rebuilt, root) == []  # 纯 ignored 残壳容忍（老 checkout 升级形态）
        shutil.rmtree(d / "v1.0.0")
        doc["tools"]["ghost"] = {"versions": [{"version": "1.0.0", "package_sha256": "a" * 64,
                                              "artifact": "packages/ghost/1.0.0.tar.gz", "python": None,
                                              "script": "ghost.py", "retired": False}]}
        # Phase 4a：归类以树集为判据——无树条目豁免（外部族 python=null 二义），不报「族树不存在」
        assert checker.check(doc, rebuilt, root) == []

    def test_publish_writes_only_latest_and_manifest_copy(self, tmp_path):
        root = tmp_path / "scripts"
        _tree(root, "fam")
        doc, _ = _registered(root, {"fam": "1.0.0"})
        out = tmp_path / "packages"
        build = tmp_path / "build"
        build.mkdir()
        rebuilt = checker.rebuild_all(root, packer, build_dir=build)
        done, errs = checker.publish_latest(rebuilt, doc, out)
        assert errs == [] and done["published"] == ["fam@1.0.0"]
        assert (out / "fam" / "1.0.0.tar.gz").is_file()
        packer.write_site_manifest_copy(doc, out)
        assert json.loads((out / "manifest.json").read_text(encoding="utf-8")) == doc

    def test_publish_is_append_only_and_never_rewrites(self, tmp_path):
        """2026-09-26：--publish 曾把包边压缩边写进站点路径（非原子、先于等价判定、全量重写）。"""
        root = tmp_path / "scripts"
        _tree(root, "fam")
        doc, _ = _registered(root, {"fam": "1.0.0"})
        out, build = tmp_path / "packages", tmp_path / "build"
        build.mkdir()
        rebuilt = checker.rebuild_all(root, packer, build_dir=build)
        checker.publish_latest(rebuilt, doc, out)
        dest = out / "fam" / "1.0.0.tar.gz"
        before = dest.stat().st_mtime_ns
        done, errs = checker.publish_latest(rebuilt, doc, out)          # 同字节：跳过，不重写
        assert errs == [] and done == {"published": [], "identical": ["fam@1.0.0"]}
        assert dest.stat().st_mtime_ns == before
        dest.write_bytes(b"other bytes")                                 # 站点被改成别的字节：拒绝覆写
        _done, errs = checker.publish_latest(rebuilt, doc, out)
        assert any("拒绝覆写" in e for e in errs) and dest.read_bytes() == b"other bytes"
        assert not [p for p in out.rglob(".*")], "原子落位不应残留临时文件"

    def test_publish_refuses_when_tree_differs_from_registration(self, tmp_path):
        """改树未发版时构建 sha ≠ 登记：不得以已登记版本号的文件名落站点。"""
        root = tmp_path / "scripts"
        _tree(root, "fam")
        doc, _ = _registered(root, {"fam": "1.0.0"})
        (root / "fam" / "fam.py").write_text("print('changed')\n", encoding="utf-8")
        build = tmp_path / "build"
        build.mkdir()
        rebuilt = checker.rebuild_all(root, packer, build_dir=build)
        _done, errs = checker.publish_latest(rebuilt, doc, tmp_path / "packages")
        assert any("构建 sha" in e for e in errs)
        assert not (tmp_path / "packages" / "fam" / "1.0.0.tar.gz").exists()


class TestBundleCarriesManifest:
    def test_build_bundle_copies_tool_manifest(self):
        from tools.dev.source_anchor import SourceGuard

        SourceGuard.of_repo_path("tools/release/build_bundle.py").anchored(
            'for extra in ("ruff.toml", "tool_manifest.json"):'
        ).assert_present(
            'shutil.copy2(repo_root / extra, out / extra)',
            why="tool_manifest.json 必须随 bundle 复制到部署树根，否则 bundle 形态下 scan 无注册输入",
        )


class TestModeNormalization:
    """sha 不得随 umask 变化，只随 Git 可执行位变化。"""

    def test_umask_variants_same_sha_exec_bit_differs(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        f = src / "a.py"
        f.write_text("A\n", encoding="utf-8")
        shas = {}
        for mode in (0o644, 0o664, 0o600, 0o755, 0o775):
            f.chmod(mode)
            shas[mode] = packer.build_deterministic_tar_gz(src, tmp_path / f"{mode}.tar.gz")["package_sha256"]
        assert shas[0o644] == shas[0o664] == shas[0o600]
        assert shas[0o755] == shas[0o775] != shas[0o644]

    def test_normalized_mode_pure_function(self):
        import tarfile

        info = tarfile.TarInfo("x")
        info.type = tarfile.REGTYPE
        info.mode = 0o664
        assert packer._normalized_mode(info) == 0o644
        info.mode = 0o770
        assert packer._normalized_mode(info) == 0o755
        info.type = tarfile.DIRTYPE
        assert packer._normalized_mode(info) == 0o755
        info.type = tarfile.SYMTYPE
        assert packer._normalized_mode(info) == 0o777


@pytest.mark.parametrize("v, expected", [("1.3.17", (0, 1, 0, 3, 0, 17)), ("2026.09.22", (0, 2026, 0, 9, 0, 22))])
def test_version_key_numeric(v, expected):
    assert tuple(x for pair in checker.version_key(v) for x in pair) == expected
    assert checker.version_key("1.3.9") < checker.version_key("1.3.17")


class TestStrayTolerance:
    """老 checkout 的纯 pycache 版本目录残壳不判红；含真文件仍红（fix #3075 后续）。"""

    def test_pycache_only_shell_not_stray_but_real_file_is(self, tmp_path):
        root = tmp_path / "scripts"
        fam = root / "fam"
        shell = fam / "v1.0.0" / "__pycache__"
        shell.mkdir(parents=True)
        (shell / "x.cpython-313.pyc").write_bytes(b"x")
        (fam / "fam.py").write_text("x\n", encoding="utf-8")
        assert checker.stray_version_dirs(root) == []
        (fam / "v1.0.0" / "leftover.py").write_text("real\n", encoding="utf-8")
        assert checker.stray_version_dirs(root) == ["fam/v1.0.0"]
