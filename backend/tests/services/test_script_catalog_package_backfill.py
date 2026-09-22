"""ADR-0051 Phase 2a：scan 从 tool_manifest.json 回填 ``script.package_sha256``。

回填只在 manifest 有 (name, version) 且未 retired 时发生；行上已有值且不等 →
记 ``package_conflicts``、不改写（manifest append-only 下不等只可能来自库侧）；
``force_rebaseline`` 重锚内容身份时同步重锚包身份。
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from backend.models.script import Script
from backend.services.script_catalog import load_package_index, scan_script_root


def _write_version(root: Path, name: str, version: str) -> Path:
    d = root / name / f"v{version}"
    d.mkdir(parents=True)
    (d / f"{name}.py").write_text(f"print('{version}')\n", encoding="utf-8")
    return d


def _manifest(tmp_path: Path, entries: list[tuple[str, str, str, bool]]) -> Path:
    tools: dict = {}
    for name, version, sha, retired in entries:
        tools.setdefault(name, {"versions": []})["versions"].append({
            "version": version, "package_sha256": sha, "artifact": f"packages/{name}/{version}.tar.gz",
            "python": None, "script": f"{name}.py", "retired": retired,
        })
    p = tmp_path / "tool_manifest.json"
    p.write_text(json.dumps({"schema_version": 1, "tools": tools}), encoding="utf-8")
    return p


def test_load_package_index_skips_retired_and_bad(tmp_path: Path):
    mf = _manifest(tmp_path, [("a", "1.0.0", "a" * 64, False), ("a", "1.0.1", "b" * 64, True), ("a", "1.0.2", "short", False)])
    assert load_package_index(mf) == {("a", "1.0.0"): "a" * 64}
    assert load_package_index(tmp_path / "missing.json") == {}
    assert load_package_index(None) == {}


def test_scan_backfills_new_and_existing_rows(db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    _write_version(root, "demo", "1.0.0")
    _write_version(root, "demo", "1.0.1")
    # 先无 manifest 扫一次：行存在、package_sha256 为空（Phase 2a 前形态）
    scan_script_root(db_session, root, manifest_path=None)
    rows = {r.version: r for r in db_session.query(Script).filter_by(name="demo")}
    assert rows["1.0.0"].package_sha256 is None

    mf = _manifest(tmp_path, [("demo", "1.0.0", "1" * 64, False), ("demo", "1.0.1", "2" * 64, True)])
    _write_version(root, "demo", "1.0.2")
    result = scan_script_root(db_session, root, manifest_path=mf)
    for r in rows.values():
        db_session.refresh(r)
    assert result.package_backfilled == 1 and result.package_conflicts == []
    assert rows["1.0.0"].package_sha256 == "1" * 64
    assert rows["1.0.1"].package_sha256 is None  # retired 条目不回填
    new = db_session.query(Script).filter_by(name="demo", version="1.0.2").one()
    assert new.package_sha256 is None  # manifest 无条目
    assert result.to_dict()["package_backfilled"] == 1


def test_scan_reports_conflict_without_overwrite(db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    _write_version(root, "demo", "1.0.0")
    mf = _manifest(tmp_path, [("demo", "1.0.0", "1" * 64, False)])
    scan_script_root(db_session, root, manifest_path=mf)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    row.package_sha256 = "9" * 64
    db_session.commit()

    result = scan_script_root(db_session, root, manifest_path=mf)
    db_session.refresh(row)
    assert row.package_sha256 == "9" * 64
    assert result.package_conflicts == [{
        "name": "demo", "version": "1.0.0", "db_sha256": "9" * 64, "manifest_sha256": "1" * 64,
    }]
    assert result.package_backfilled == 0


def test_force_rebaseline_reanchors_package_sha(db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    d = _write_version(root, "demo", "1.0.0")
    scan_script_root(db_session, root, manifest_path=None)
    (d / "demo.py").write_text("print('rewritten')\n", encoding="utf-8")
    mf = _manifest(tmp_path, [("demo", "1.0.0", "3" * 64, False)])
    conflict = scan_script_root(db_session, root, manifest_path=mf)
    assert conflict.conflicts == [{"name": "demo", "version": "1.0.0"}]
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.package_sha256 is None  # 内容冲突时不回填

    result = scan_script_root(db_session, root, manifest_path=mf, force_rebaseline=True)
    db_session.refresh(row)
    assert len(result.rebaselined) == 1 and row.package_sha256 == "3" * 64

# ---- check_script_packages ⇄ script_catalog 对拍（stdlib 工具不 import backend，靠这里钉住同判据） ----

import importlib.util

ROOT = Path(__file__).resolve().parents[3]


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_script_packages_parity", ROOT / "tools" / "dev" / "check_script_packages.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCheckerParityWithCatalog:
    checker = None

    @classmethod
    def setup_class(cls):
        cls.checker = _load_checker()

    def test_legacy_names_match_backend(self):
        from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
        assert self.checker.LEGACY_SCRIPT_NAMES == LEGACY_AEE_SCRIPT_NAMES

    def test_pick_entry_matches_script_catalog_on_real_tree(self):
        from backend.services import script_catalog as sc
        root = ROOT / "backend" / "agent" / "scripts"
        seen = 0
        for name, version, vdir in self.checker.iter_version_dirs(root):
            expect, _ = sc._pick_entry(vdir)
            got = self.checker.pick_entry(vdir)
            assert (got.name if got else None) == (expect.name if expect else None), (name, version)
            seen += 1
        assert seen > 0

    def test_iter_version_dirs_matches_catalog_iteration(self):
        from backend.services import script_catalog as sc
        root = ROOT / "backend" / "agent" / "scripts"
        ours = {(n, v) for n, v, _ in self.checker.iter_version_dirs(root)}
        theirs = {(n, v) for _, n, v, _, _ in sc._iter_script_entries(root)}
        assert ours == theirs
