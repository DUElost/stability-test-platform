"""ADR-0051 Phase 3：``sync_scripts_from_manifest`` —— 注册输入 = tool_manifest.json + 站点包源。

夹具自建确定性 tar.gz + ``packages/{name}/{version}.tar.gz`` 布局 + manifest 文档；
不再有任何「扫描检出目录」的路径（ADR-0046 病根随版本目录退役）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sqlalchemy.orm import Session

from backend.models.script import Script
from backend.tests.script_package_site import Site
from backend.services.script_catalog import (
    load_package_index,
    read_capabilities,
    support_files_manifest,
    sync_scripts_from_manifest,
)


def _sync(db, site: Site, **kw):
    return sync_scripts_from_manifest(db, site.manifest, site.packages_root, kw.pop("runtime_root", None), **kw)


def test_creates_rows_from_packages_with_entry_support_capabilities(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    sha = site.add("demo", "1.0.0", {
        "demo.py": "print('demo')\n", "_adb.py": "ADB = 1\n",
        "capabilities.json": json.dumps({"capabilities": ["progress_stamps"]}), "README.md": "x",
    })
    result = _sync(db_session, site, runtime_root="/opt/stability-test-agent/agent/scripts")
    assert result.created == 1 and result.conflicts == [] and result.package_missing == []
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.is_active is True and row.script_type == "python" and row.category == "device"
    assert row.content_sha256 == hashlib.sha256(b"print('demo')\n").hexdigest()
    assert row.support_files_manifest == {"_adb.py": hashlib.sha256(b"ADB = 1\n").hexdigest()}
    assert row.capabilities == ["progress_stamps"]
    assert row.package_sha256 == sha
    assert row.nfs_path == "/opt/stability-test-agent/agent/scripts/demo/v1.0.0/demo.py"


def test_second_sync_is_idempotent_and_shell_entry_supported(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    site.add("wifi", "1.0.0", {"wifi.sh": "#!/usr/bin/env bash\necho wifi\n"}, script="wifi.sh")
    first = _sync(db_session, site)
    again = _sync(db_session, site)
    assert (first.created, again.created, again.skipped) == (1, 0, 1)
    assert db_session.query(Script).filter_by(name="wifi").one().script_type == "shell"


def test_package_missing_is_reported_not_registered(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    site.add("demo", "1.0.0", {"demo.py": "x\n"}, publish=False)
    result = _sync(db_session, site)
    assert result.created == 0
    assert result.package_missing == [{
        "name": "demo", "version": "1.0.0", "reason": "package_missing",
        "artifact": str(site.packages_root / "demo" / "1.0.0.tar.gz"),
    }]
    assert db_session.query(Script).count() == 0


def test_tarball_sha_mismatch_is_package_conflict(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    site.add("demo", "1.0.0", {"demo.py": "x\n"}, sha_override="0" * 64)
    result = _sync(db_session, site)
    assert result.created == 0
    assert [(c["name"], c["reason"]) for c in result.package_conflicts] == [("demo", "package_sha_mismatch")]


def test_retired_entry_deactivates_explicitly_and_missing_never_deactivates(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    site.add("demo", "1.0.0", {"demo.py": "a\n"})
    site.add("demo", "1.1.0", {"demo.py": "b\n"})
    _sync(db_session, site)
    # 包从站点消失（未发布/挂载掉）→ 只报告，不反激活（ADR-0046 D2）
    (site.packages_root / "demo" / "1.0.0.tar.gz").unlink()
    result = _sync(db_session, site)
    assert result.deactivated == 0 and len(result.package_missing) == 1
    assert db_session.query(Script).filter_by(name="demo", version="1.0.0").one().is_active is True
    # manifest retired:true → 显式退役
    site.retire("demo", "1.0.0")
    result = _sync(db_session, site)
    assert result.deactivated == 1
    assert result.deactivated_versions[0]["name"] == "demo" and result.deactivated_versions[0]["version"] == "1.0.0"
    assert db_session.query(Script).filter_by(name="demo", version="1.0.0").one().is_active is False
    assert db_session.query(Script).filter_by(name="demo", version="1.1.0").one().is_active is True
    # 已退役行不复活（幂等）
    assert _sync(db_session, site).deactivated == 0


def test_unregistered_active_rows_are_reported_only(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    db_session.add(Script(name="seeded", script_type="python", version="9.9.9", nfs_path="/x/seeded.py",
                          content_sha256="a" * 64, is_active=True, default_params={}, param_schema={}))
    db_session.add(Script(name="scan_aee", script_type="python", version="1.0.0", nfs_path="/x/scan_aee.py",
                          content_sha256="b" * 64, is_active=True, default_params={}, param_schema={}))
    db_session.commit()
    result = _sync(db_session, site)
    assert result.unregistered_active == [{"name": "seeded", "version": "9.9.9"}]  # legacy 名不报
    assert db_session.query(Script).filter_by(name="seeded").one().is_active is True


def test_drifted_row_is_conflict_until_force_rebaseline(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    sha = site.add("demo", "1.0.0", {"demo.py": "x\n", "_lib.py": "L = 1\n"})
    _sync(db_session, site)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    row.content_sha256 = "f" * 64  # 库侧漂移（包不可变）
    row.is_active = False
    db_session.commit()
    result = _sync(db_session, site)
    assert result.conflicts == [{"name": "demo", "version": "1.0.0"}]
    db_session.refresh(row)
    assert row.content_sha256 == "f" * 64 and row.is_active is False
    result = _sync(db_session, site, force_rebaseline=True)
    db_session.refresh(row)
    assert len(result.rebaselined) == 1 and row.content_sha256 == hashlib.sha256(b"x\n").hexdigest()
    assert row.package_sha256 == sha and row.is_active is True  # 显式运维出口：重锚并复活


def test_backfills_package_sha_and_reports_db_mismatch(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    sha = site.add("demo", "1.0.0", {"demo.py": "x\n"})
    db_session.add(Script(name="demo", script_type="python", version="1.0.0", nfs_path="/x/demo.py",
                          content_sha256=hashlib.sha256(b"x\n").hexdigest(), is_active=True,
                          default_params={}, param_schema={}, support_files_manifest={}, capabilities=[]))
    db_session.commit()
    result = _sync(db_session, site)
    row = db_session.query(Script).filter_by(name="demo").one()
    assert result.package_backfilled == 1 and row.package_sha256 == sha
    row.package_sha256 = "9" * 64
    db_session.commit()
    result = _sync(db_session, site)
    assert [(c["name"], c["reason"]) for c in result.package_conflicts] == [("demo", "db_package_sha_mismatch")]
    db_session.refresh(row)
    assert row.package_sha256 == "9" * 64  # 不覆盖，交人判


def test_runtime_root_reanchors_nfs_path_and_windows_root(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    site.add("demo", "1.0.0", {"demo.py": "x\n"})
    _sync(db_session, site)
    row = db_session.query(Script).filter_by(name="demo").one()
    assert row.nfs_path == str(site.packages_root / "demo" / "v1.0.0" / "demo.py")
    _sync(db_session, site, runtime_root="/opt/stability-test-agent/agent/scripts")
    db_session.refresh(row)
    assert row.nfs_path == "/opt/stability-test-agent/agent/scripts/demo/v1.0.0/demo.py"
    _sync(db_session, site, runtime_root=r"C:\stp\agent\scripts")
    db_session.refresh(row)
    assert row.nfs_path == r"C:\stp\agent\scripts\demo\v1.0.0\demo.py"


def test_external_tool_entries_and_bad_manifest_are_ignored(db_session: Session, tmp_path: Path):
    site = Site(tmp_path)
    site.doc["tools"]["Start-Log-Scan"] = {"kind": "tool", "versions": [{
        "version": "2026.09.22", "package_sha256": "b" * 64, "artifact": "packages/Start-Log-Scan/2026.09.22.tar.gz",
        "python": "venv/bin/python", "script": "start_log_scan.py", "retired": False}]}
    site.write()
    assert _sync(db_session, site).created == 0
    assert load_package_index(site.manifest) == {}
    site.manifest.write_text("{not json", encoding="utf-8")
    assert _sync(db_session, site).created == 0


def test_tree_helpers_still_serve_family_trees(tmp_path: Path):
    """族树上的入口/伴随/能力判据（check_script_packages 与 seed 守卫复用）。"""
    tree = tmp_path / "fam"
    tree.mkdir()
    (tree / "fam.py").write_text("x\n", encoding="utf-8")
    (tree / "_helper.py").write_text("h\n", encoding="utf-8")
    (tree / "capabilities.json").write_text(json.dumps(["a", 1, ""]), encoding="utf-8")
    assert support_files_manifest(tree, tree / "fam.py") == {"_helper.py": hashlib.sha256(b"h\n").hexdigest()}
    assert read_capabilities(tree) == ["a"]
    (tree / "capabilities.json").write_text("{bad", encoding="utf-8")
    assert read_capabilities(tree) == []


# ── #3196：包身份列（Phase 2b 运行时校验唯一判据）不得被静默清空 ──────────────


def test_unreadable_manifest_does_not_clear_package_sha(db_session: Session, tmp_path: Path):
    """manifest 坏 / 不在场 ⇒ 已回填的 ``package_sha256`` 必须原地保留。

    「读不到登记」在旧形态里被执行成了「把身份写没」。清空方向是 fail-open：列一旦为
    空，Phase 2b 的按包身份校验就没有可比对象。这里钉的是**不写**，并顺带钉住
    「全表只上报不动手」（``unregistered_active``）——反激活只能由 retired 显式驱动。
    """
    site = Site(tmp_path)
    sha = site.add("demo", "1.0.0", {"demo.py": "x\n"})
    _sync(db_session, site)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.package_sha256 == sha

    site.manifest.write_text("{not json", encoding="utf-8")
    result = _sync(db_session, site)
    db_session.refresh(row)
    assert row.package_sha256 == sha and result.rebaselined == []
    assert [r["name"] for r in result.unregistered_active] == ["demo"]

    site.manifest.unlink()
    _sync(db_session, site)
    db_session.refresh(row)
    assert row.package_sha256 == sha and row.is_active is True


def test_entry_without_package_sha_is_named_not_overwritten(db_session: Session, tmp_path: Path):
    """条目登记值为空 ⇒ 点名 ``manifest_package_sha_missing``，且**不**覆写任何身份列。

    取 ``force_rebaseline=True`` 走重锚分支（#3196 报的正是这条分支无条件覆写
    ``package_sha256``）。原实现只靠「空登记值不可能等于任何实算 tarball sha」这个
    **副作用**挡住，本用例把它变成显式判据：宁可标注不一致，不可静默清身份；
    同时钉住重锚被拒时**内容身份也不被顺手改写**。
    """
    site = Site(tmp_path)
    sha = site.add("demo", "1.0.0", {"demo.py": "x\n"})
    _sync(db_session, site)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    row.content_sha256 = "f" * 64  # 库侧漂移：逼重锚分支执行
    db_session.commit()

    site.doc["tools"]["demo"]["versions"][0]["package_sha256"] = ""
    site.write()
    result = _sync(db_session, site, force_rebaseline=True)
    db_session.refresh(row)

    assert [(c["name"], c["reason"]) for c in result.package_conflicts] == [
        ("demo", "manifest_package_sha_missing")
    ]
    assert result.rebaselined == []
    assert row.package_sha256 == sha and row.content_sha256 == "f" * 64
