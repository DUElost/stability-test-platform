"""Tests for ``backend.scripts.check_script_package_equivalence`` (ADR-0051 Phase 3)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from backend.scripts import check_script_package_equivalence as eq


def _build_fixture_repo(tmp_path: Path):
    """Create a minimal family tree, tool_manifest.json, and site packages directory."""
    scripts_root = tmp_path / "scripts"
    fam = scripts_root / "demo_script"
    fam.mkdir(parents=True)
    entry = fam / "demo_script.py"
    entry.write_text("print('ok')\n", encoding="utf-8")
    helper = fam / "_helper.py"
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    caps = fam / "capabilities.json"
    caps.write_text(json.dumps({"capabilities": ["progress_stamps"]}), encoding="utf-8")

    checker = eq._load_checker()
    packer = checker._load_packer()
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    rebuilt = checker.rebuild_all(scripts_root, packer, build_dir=build_dir)
    facts = rebuilt["demo_script"]

    manifest = {
        "schema_version": 2,
        "tools": {
            "demo_script": {
                "kind": "script",
                "versions": [
                    {
                        "version": "1.0.0",
                        "package_sha256": facts["package_sha256"],
                        "script": facts["script"],
                        "python": None,
                        "retired": False,
                    },
                ],
            },
        },
    }
    packages_root = tmp_path / "packages"
    done, errs = checker.publish_latest(rebuilt, manifest, packages_root)
    assert not errs, errs
    assert done["published"] == ["demo_script@1.0.0"]

    manifest_path = tmp_path / "tool_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    entry_sha = hashlib.sha256(entry.read_bytes()).hexdigest()
    helper_sha = hashlib.sha256(helper.read_bytes()).hexdigest()
    db_row = {
        "name": "demo_script",
        "version": "1.0.0",
        "nfs_path": "/opt/packages/demo_script/v1.0.0/demo_script.py",
        "content_sha256": entry_sha,
        "support_files_manifest": {"_helper.py": helper_sha},
        "capabilities": ["progress_stamps"],
        "is_active": True,
        "package_sha256": facts["package_sha256"],
    }
    return {
        "scripts_root": scripts_root,
        "packages_root": packages_root,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "db_row": db_row,
        "facts": facts,
        "checker": checker,
    }


def _create_sqlite_script_db(db_path: Path, rows: list[dict]) -> str:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE script (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                version TEXT NOT NULL,
                nfs_path TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                support_files_manifest TEXT NOT NULL,
                capabilities TEXT NOT NULL,
                is_active INTEGER NOT NULL,
                package_sha256 TEXT
            )
            """
        )
        for r in rows:
            conn.execute(
                """
                INSERT INTO script
                    (name, version, nfs_path, content_sha256, support_files_manifest, capabilities, is_active, package_sha256)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    r["name"],
                    r["version"],
                    r["nfs_path"],
                    r["content_sha256"],
                    json.dumps(r["support_files_manifest"]),
                    json.dumps(r["capabilities"]),
                    1 if r["is_active"] else 0,
                    r.get("package_sha256"),
                ),
            )
        conn.commit()
    return f"sqlite:///{db_path}"


def test_evaluate_valid_and_retired_exemptions(tmp_path: Path) -> None:
    fx = _build_fixture_repo(tmp_path)
    manifest = fx["manifest"]
    manifest["tools"]["demo_script"]["versions"].insert(
        0,
        {
            "version": "0.9.0",
            "package_sha256": "a" * 64,
            "script": "demo_script.py",
            "python": None,
            "retired": True,
        },
    )
    rows = [
        fx["db_row"],
        {
            **fx["db_row"],
            "version": "0.9.0",
            "is_active": False,
            "package_sha256": "a" * 64,
        },
        {
            "name": "legacy_untracked",
            "version": "0.1.0",
            "nfs_path": "/opt/legacy/legacy_untracked.py",
            "content_sha256": "b" * 64,
            "support_files_manifest": {},
            "capabilities": [],
            "is_active": False,
            "package_sha256": None,
        },
    ]

    results = eq.evaluate(rows, manifest, fx["packages_root"])
    statuses = {(r["name"], r["version"]): r["status"] for r in results}
    assert statuses == {
        ("demo_script", "1.0.0"): "ok",
        ("demo_script", "0.9.0"): "retired_without_package",
        ("legacy_untracked", "0.1.0"): "retired_untracked",
    }
    summary, failing_rows = eq._build_summary(results, [], [], True)
    assert summary["failing"] == 0
    assert failing_rows == []
    assert summary["retired_exempt"] == 2


@pytest.mark.parametrize(
    ("mutator", "expected_status"),
    [
        (lambda r, fx: r.update(name="unknown_active"), "unregistered_active"),
        (lambda r, fx: r.update(nfs_path="/opt/other/wrong.py"), "entry_name_mismatch"),
        (lambda r, fx: r.update(content_sha256="0" * 64), "entry_sha_mismatch"),
        (lambda r, fx: r.update(support_files_manifest={}), "support_mismatch"),
        (lambda r, fx: r.update(capabilities=[]), "capabilities_mismatch"),
        (lambda r, fx: r.update(package_sha256="f" * 64), "db_package_sha_mismatch"),
    ],
)
def test_evaluate_detects_row_mismatches(tmp_path: Path, mutator, expected_status: str) -> None:
    fx = _build_fixture_repo(tmp_path)
    bad_row = dict(fx["db_row"])
    mutator(bad_row, fx)

    results = eq.evaluate([bad_row], fx["manifest"], fx["packages_root"])
    assert results[0]["status"] == expected_status
    summary, _ = eq._build_summary(results, [], [], True)
    assert summary["failing"] == 1


def test_evaluate_detects_missing_or_corrupted_tarball(tmp_path: Path) -> None:
    fx = _build_fixture_repo(tmp_path)
    tarball = fx["packages_root"] / "demo_script" / "1.0.0.tar.gz"
    tarball.write_bytes(b"corrupted")

    results = eq.evaluate([fx["db_row"]], fx["manifest"], fx["packages_root"])
    assert results[0]["status"] == "package_sha_mismatch"

    tarball.unlink()
    results_missing = eq.evaluate([fx["db_row"]], fx["manifest"], fx["packages_root"])
    assert results_missing[0]["status"] == "package_missing"

    head_errs = eq.evaluate_manifest_heads(fx["manifest"], fx["packages_root"], fx["checker"])
    assert head_errs == [{"name": "demo_script", "version": "1.0.0", "reason": "head_package_missing"}]


def test_cli_main_end_to_end_and_tree_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    fx = _build_fixture_repo(tmp_path)
    db_url = _create_sqlite_script_db(tmp_path / "db.sqlite", [fx["db_row"]])
    monkeypatch.setenv("DATABASE_URL", db_url)

    rc = eq.main([
        "--scripts-root", str(fx["scripts_root"]),
        "--manifest", str(fx["manifest_path"]),
        "--packages-root", str(fx["packages_root"]),
        "--json",
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["failing"] == 0
    assert payload["summary"]["ok"] == 1
    assert payload["summary"]["backfilled"] == 1

    # Modify source tree without registering new version -> tree check catches drift; --skip-tree skips it.
    (fx["scripts_root"] / "demo_script" / "demo_script.py").write_text("print('drifted')\n", encoding="utf-8")
    rc_drift = eq.main([
        "--scripts-root", str(fx["scripts_root"]),
        "--manifest", str(fx["manifest_path"]),
        "--packages-root", str(fx["packages_root"]),
        "--json",
    ])
    assert rc_drift == 1
    drift_payload = json.loads(capsys.readouterr().out)
    assert drift_payload["summary"]["tree_errors"] >= 1

    rc_skip = eq.main([
        "--manifest", str(fx["manifest_path"]),
        "--packages-root", str(fx["packages_root"]),
        "--skip-tree",
    ])
    assert rc_skip == 0
    assert "EQUIVALENCE OK" in capsys.readouterr().out


def test_cli_returns_exit_2_when_inputs_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    rc = eq.main([
        "--manifest", str(tmp_path / "missing.json"),
        "--packages-root", str(tmp_path),
        "--skip-tree",
    ])
    assert rc == 2

    manifest_path = tmp_path / "tool_manifest.json"
    manifest_path.write_text('{"schema_version": 2, "tools": {}}', encoding="utf-8")
    rc_pk = eq.main([
        "--manifest", str(manifest_path),
        "--packages-root", str(tmp_path / "missing_packages"),
        "--skip-tree",
    ])
    assert rc_pk == 2
