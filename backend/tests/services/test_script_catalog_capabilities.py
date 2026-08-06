"""#171 — script.capabilities 由版本目录 capabilities.json 扫描登记。"""

import json
from pathlib import Path

from sqlalchemy.orm import Session

from backend.models.script import Script
from backend.services.script_catalog import read_capabilities, scan_script_root, sha256_file
from backend.services.script_progress_capability import script_supports_progress


def _write_script_version(root: Path, name: str, version: str, capabilities=None) -> Path:
    version_dir = root / name / f"v{version}"
    version_dir.mkdir(parents=True)
    (version_dir / f"{name}.py").write_text("print('ok')\n", encoding="utf-8")
    if capabilities is not None:
        (version_dir / "capabilities.json").write_text(
            json.dumps({"capabilities": capabilities}), encoding="utf-8",
        )
    return version_dir


def test_read_capabilities_parses_metadata(tmp_path: Path):
    version_dir = _write_script_version(tmp_path, "demo", "1.0.0", ["progress_stamps"])
    assert read_capabilities(version_dir) == ["progress_stamps"]


def test_read_capabilities_empty_without_metadata(tmp_path: Path):
    version_dir = _write_script_version(tmp_path, "demo", "1.0.0")
    assert read_capabilities(version_dir) == []


def test_read_capabilities_malformed_metadata_is_empty(tmp_path: Path):
    version_dir = _write_script_version(tmp_path, "demo", "1.0.0", [])
    (version_dir / "capabilities.json").write_text("{not json", encoding="utf-8")
    assert read_capabilities(version_dir) == []


def test_scan_persists_capabilities(db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    _write_script_version(root, "demo", "1.0.0", ["progress_stamps"])

    result = scan_script_root(db_session, root)
    assert result.created == 1

    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.capabilities == ["progress_stamps"]


def test_scan_defaults_capabilities_to_empty(db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    _write_script_version(root, "demo", "1.0.0")

    scan_script_root(db_session, root)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.capabilities == []


def test_scan_conflicts_when_capabilities_metadata_changes(db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    version_dir = _write_script_version(
        root, "demo", "1.0.0", ["progress_stamps"],
    )
    first = scan_script_root(db_session, root)
    assert first.created == 1

    (version_dir / "capabilities.json").write_text(
        json.dumps({"capabilities": ["other_cap"]}), encoding="utf-8",
    )
    second = scan_script_root(db_session, root)
    assert second.conflicts == [{"name": "demo", "version": "1.0.0"}]

    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.capabilities == ["progress_stamps"]


def test_first_scan_backfills_capabilities_without_conflict(db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    version_dir = _write_script_version(
        root, "demo", "1.0.0", ["progress_stamps"],
    )
    db_session.add(Script(
        name="demo",
        script_type="python",
        version="1.0.0",
        nfs_path=str(version_dir / "demo.py"),
        content_sha256=sha256_file(version_dir / "demo.py"),
        capabilities=[],
        support_files_manifest={},
        is_active=True,
        default_params={},
        param_schema={},
    ))
    db_session.commit()

    result = scan_script_root(db_session, root)
    assert result.conflicts == []
    assert result.skipped == 1

    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.capabilities == ["progress_stamps"]


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


_PROGRESS_THRESHOLDS = {
    # monkey_setup v2.3.0 虽实现 PROGRESS，但 #138 push 回调缺陷到 v2.3.1
    # 才修复，因此从 v2.3.1 起强制声明；flash_firmware v1.1.0 起声明。
    "monkey_setup": (2, 3, 1),
    "flash_firmware": (1, 1, 0),
}


def test_repo_catalog_parity_after_scan(db_session: Session):
    """Repo 脚本目录扫描后，能力声明与门禁查询必须完全一致。

    对已知会打 PROGRESS 戳的脚本族，按版本阈值**枚举所有版本目录**：
    达到阈值的版本必须存在 capabilities.json 且声明 progress_stamps，
    阈值以下的版本不得声明。新增 v2.3.5 且漏放 capabilities.json 时，
    此测试立即红掉——这正是 v2.3.4 曾漏登记想防的回归。
    """
    repo_scripts = (
        Path(__file__).resolve().parents[3] / "backend" / "agent" / "scripts"
    )

    for name_dir in sorted(p for p in repo_scripts.iterdir() if p.is_dir()):
        name = name_dir.name
        threshold = _PROGRESS_THRESHOLDS.get(name)
        if threshold is None:
            continue
        for version_dir in sorted(p for p in name_dir.iterdir() if p.is_dir()):
            raw_version = version_dir.name
            if not raw_version.startswith("v") or len(raw_version) <= 1:
                continue
            version = raw_version[1:]
            caps = read_capabilities(version_dir)
            should_declare = _version_tuple(version) >= threshold
            declared = "progress_stamps" in caps
            assert declared == should_declare, (
                f"{name} {version}: progress_stamps declared={declared}, "
                f"expected={should_declare}"
            )

    scan_script_root(db_session, repo_scripts)

    for name, threshold in _PROGRESS_THRESHOLDS.items():
        name_dir = repo_scripts / name
        for version_dir in sorted(p for p in name_dir.iterdir() if p.is_dir()):
            raw_version = version_dir.name
            if not raw_version.startswith("v") or len(raw_version) <= 1:
                continue
            version = raw_version[1:]
            expected = _version_tuple(version) >= threshold
            assert (
                script_supports_progress(db_session, name, version) == expected
            ), f"{name} {version}: DB capability ≠ repo metadata"

    # 保留显式锚点，防止阈值规则本身被误改。
    assert script_supports_progress(db_session, "monkey_setup", "2.3.1")
    assert script_supports_progress(db_session, "monkey_setup", "2.3.4")
    assert script_supports_progress(db_session, "flash_firmware", "1.1.0")
    assert not script_supports_progress(db_session, "monkey_setup", "2.2.0")
    assert not script_supports_progress(db_session, "monkey_setup", "2.3.0")
    assert not script_supports_progress(db_session, "flash_firmware", "1.0.0")
