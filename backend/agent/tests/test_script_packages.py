"""ADR-0051 Phase 2b：``script_packages`` —— DB 权威（package_sha256）→ 包身份 → tools_cache。

夹具自建确定性 tar.gz 与站点 ``packages/`` 布局（不依赖 tools/dev，Agent 套件自足）。
"""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent import script_packages as sp
from backend.agent.script_verifier import verify_scripts_payload


def _make_package(packages_root: Path, name: str, version: str, files: dict[str, str]) -> str:
    """写 ``packages/{name}/{version}.tar.gz`` + ``manifest.json``，返回整包 sha。"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel, body in sorted(files.items()):
            data = body.encode("utf-8")
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    blob = buf.getvalue()
    sha = hashlib.sha256(blob).hexdigest()
    (packages_root / name).mkdir(parents=True, exist_ok=True)
    (packages_root / name / f"{version}.tar.gz").write_bytes(blob)
    import json

    mf = packages_root / "manifest.json"
    doc = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {"schema_version": 1, "tools": {}}
    doc["tools"].setdefault(name, {"versions": []})["versions"].append({
        "version": version, "package_sha256": sha, "artifact": f"packages/{name}/{version}.tar.gz",
        "python": None, "script": f"{name}.py", "retired": False,
    })
    mf.write_text(json.dumps(doc), encoding="utf-8")
    return sha


@pytest.fixture
def site(tmp_path: Path) -> dict:
    packages_root = tmp_path / "packages"
    cache_root = tmp_path / "tools_cache"
    tree_dir = tmp_path / "agent" / "scripts" / "demo" / "v1.0.0"
    tree_dir.mkdir(parents=True)
    (tree_dir / "demo.py").write_text("print('tree')\n", encoding="utf-8")
    sha = _make_package(packages_root, "demo", "1.0.0", {"demo.py": "print('package')\n", "_adb.py": "ADB=1\n"})
    entry = SimpleNamespace(name="demo", version="1.0.0", nfs_path=str(tree_dir / "demo.py"), package_sha256=sha)
    env = {"STP_PACKAGES_ROOT": str(packages_root), "STP_TOOLS_CACHE_ROOT": str(cache_root)}
    return {"packages_root": packages_root, "cache_root": cache_root, "entry": entry, "env": env, "tree": tree_dir}


def test_mode_parsing():
    assert sp.package_mode({}) == "off"
    assert sp.package_mode({"STP_SCRIPT_PACKAGES": " ON "}) == "on"
    assert sp.package_mode({"STP_SCRIPT_PACKAGES": "strict"}) == "strict"
    assert sp.package_mode({"STP_SCRIPT_PACKAGES": "bogus"}) == "off"


def test_off_never_touches_packages(site):
    env = {**site["env"], "STP_SCRIPT_PACKAGES": "off"}
    r = sp.resolve_script_path(site["entry"], env)
    assert r.source == "tree" and r.path == site["entry"].nfs_path and r.cwd == str(site["tree"])
    assert not site["cache_root"].exists()


def test_on_resolves_into_tools_cache(site):
    env = {**site["env"], "STP_SCRIPT_PACKAGES": "on"}
    r = sp.resolve_script_path(site["entry"], env)
    assert r.source == "package"
    assert Path(r.path) == site["cache_root"] / "demo" / "1.0.0" / "demo.py"
    assert Path(r.cwd) == site["cache_root"] / "demo" / "1.0.0"
    assert Path(r.path).read_text(encoding="utf-8") == "print('package')\n"
    assert (site["cache_root"] / "demo" / "1.0.0" / "_adb.py").is_file()
    assert (site["cache_root"] / "demo" / "1.0.0" / ".stp-verified").read_text().strip() == site["entry"].package_sha256


def test_on_without_package_sha_falls_back(site):
    env = {**site["env"], "STP_SCRIPT_PACKAGES": "on"}
    entry = SimpleNamespace(**{**vars(site["entry"]), "package_sha256": None})
    r = sp.resolve_script_path(entry, env)
    assert r.source == "tree" and r.reason == "no_package_sha"


def test_on_with_bad_sha_falls_back_strict_raises(site):
    env = {**site["env"], "STP_SCRIPT_PACKAGES": "on"}
    entry = SimpleNamespace(**{**vars(site["entry"]), "package_sha256": "0" * 64})
    r = sp.resolve_script_path(entry, env)
    assert r.source == "tree" and r.reason == "package_unavailable"
    with pytest.raises(sp.PackageUnavailable):
        sp.resolve_script_path(entry, {**env, "STP_SCRIPT_PACKAGES": "strict"})


def test_on_roots_undefined_falls_back(site):
    r = sp.resolve_script_path(site["entry"], {"STP_SCRIPT_PACKAGES": "on"})
    assert r.source == "tree" and r.reason == "roots_undefined"


def test_entry_missing_in_package_falls_back(site):
    env = {**site["env"], "STP_SCRIPT_PACKAGES": "on"}
    entry = SimpleNamespace(**{**vars(site["entry"]), "nfs_path": str(site["tree"] / "other.py")})
    r = sp.resolve_script_path(entry, env)
    assert r.source == "tree" and r.reason == "entry_missing_in_package"


def test_windows_nfs_path_basename(site):
    env = {**site["env"], "STP_SCRIPT_PACKAGES": "on"}
    entry = SimpleNamespace(**{**vars(site["entry"]), "nfs_path": r"C:\stp\agent\scripts\demo\v1.0.0\demo.py"})
    r = sp.resolve_script_path(entry, env)
    assert r.source == "package" and Path(r.path).name == "demo.py"


class TestVerifyScriptsPackageMode:
    def _expected(self, site):
        return [{
            "name": "demo", "version": "1.0.0", "nfs_path": site["entry"].nfs_path,
            "sha256": hashlib.sha256(b"print('tree')\n").hexdigest(),
            "package_sha256": site["entry"].package_sha256,
        }]

    def test_off_keeps_file_semantics(self, site, monkeypatch):
        for k, v in site["env"].items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv("STP_SCRIPT_PACKAGES", raising=False)
        row = verify_scripts_payload(self._expected(site), host_id="h1")["results"][0]
        assert row["ok"] is True and row["package_active"] is False

    def test_on_verifies_package_even_if_tree_file_missing(self, site, monkeypatch):
        for k, v in site["env"].items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("STP_SCRIPT_PACKAGES", "on")
        (site["tree"] / "demo.py").unlink()  # Phase 3 后的形态：树上没文件
        row = verify_scripts_payload(self._expected(site), host_id="h1")["results"][0]
        assert row["ok"] is True and row["package_active"] is True and row["exists"] is False
        assert (site["cache_root"] / "demo" / "1.0.0" / ".stp-verified").is_file()  # 预热

    def test_on_package_unavailable_is_explicit_error(self, site, monkeypatch):
        for k, v in site["env"].items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("STP_SCRIPT_PACKAGES", "on")
        exp = self._expected(site)
        exp[0]["package_sha256"] = "0" * 64
        row = verify_scripts_payload(exp, host_id="h1")["results"][0]
        assert row["ok"] is False and row["error"] == "package_unavailable"

    def test_on_without_package_sha_uses_file(self, site, monkeypatch):
        for k, v in site["env"].items():
            monkeypatch.setenv(k, v)
        monkeypatch.setenv("STP_SCRIPT_PACKAGES", "on")
        exp = self._expected(site)
        del exp[0]["package_sha256"]
        row = verify_scripts_payload(exp, host_id="h1")["results"][0]
        assert row["ok"] is True and row["package_active"] is False
