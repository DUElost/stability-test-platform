"""#145 — companion module manifest during script scan."""

from pathlib import Path

from sqlalchemy.orm import Session

from backend.models.script import Script
from backend.services.script_catalog import scan_script_root, sha256_file, support_files_manifest


def test_support_files_manifest_excludes_entry(tmp_path: Path):
    version_dir = tmp_path / "monkey_setup" / "v2.0.0"
    version_dir.mkdir(parents=True)
    entry = version_dir / "monkey_setup.py"
    entry.write_text("print('main')\n", encoding="utf-8")
    helper = version_dir / "_adb.py"
    helper.write_text("print('helper')\n", encoding="utf-8")

    manifest = support_files_manifest(version_dir, entry)
    assert manifest == {"_adb.py": sha256_file(helper)}


def test_scan_conflict_when_helper_changes(db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    version_dir = root / "demo_script" / "v1.0.0"
    version_dir.mkdir(parents=True)
    entry = version_dir / "demo_script.py"
    entry.write_text("print('main')\n", encoding="utf-8")
    helper = version_dir / "_adb.py"
    helper.write_text("v1\n", encoding="utf-8")
    original_helper_sha = sha256_file(helper)

    first = scan_script_root(db_session, root)
    assert first.created == 1

    helper.write_text("v2\n", encoding="utf-8")
    second = scan_script_root(db_session, root)
    assert second.conflicts == [{"name": "demo_script", "version": "1.0.0"}]

    row = db_session.query(Script).filter_by(name="demo_script", version="1.0.0").one()
    assert row.support_files_manifest == {"_adb.py": original_helper_sha}
