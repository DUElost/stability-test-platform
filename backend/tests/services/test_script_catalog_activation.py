"""scan 的 is_active 单向语义：盘上缺失 → 停用；人工停用 → 不复活。

Honor 刷机自动化（方向 A）调研发现的回归：b7c8d9e0f1a2 曾 deactivate
flash_firmware v1.0.0，但 scan 会把「目录还在盘上且 is_active=false」的行
无条件翻回 true，推翻 admin deactivate（DELETE /scripts/{id}，带审计与
plan 引用门禁）和 seed 迁移的决策。修复后 is_active 由 scan 单向管理。
"""

from pathlib import Path

from sqlalchemy.orm import Session

from backend.models.script import Script
from backend.services.script_catalog import scan_script_root


def _write_script_version(root: Path, name: str, version: str) -> Path:
    version_dir = root / name / f"v{version}"
    version_dir.mkdir(parents=True)
    (version_dir / f"{name}.py").write_text("print('ok')\n", encoding="utf-8")
    return version_dir


def test_scan_does_not_resurrect_manual_deactivation(
        db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    _write_script_version(root, "demo", "1.0.0")

    scan_script_root(db_session, root)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    row.is_active = False
    db_session.commit()

    result = scan_script_root(db_session, root)
    db_session.refresh(row)
    assert row.is_active is False
    assert result.deactivated == 0
    assert result.created == 0


def test_scan_deactivates_version_missing_from_disk(
        db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    version_dir = _write_script_version(root, "demo", "1.0.0")

    scan_script_root(db_session, root)
    import shutil
    shutil.rmtree(version_dir)

    result = scan_script_root(db_session, root)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.is_active is False
    assert result.deactivated == 1
    # #2386：单向反激活必须可归责——明细（含被判定缺失的 nfs_path）进结果与审计，
    # 而不是留一个事后无从查起的计数。
    assert [(d["name"], d["version"]) for d in result.deactivated_versions] == [
        ("demo", "1.0.0"),
    ]
    assert result.deactivated_versions[0]["nfs_path"].endswith("demo/v1.0.0/demo.py")


def test_deactivation_detail_is_empty_when_nothing_missing(
        db_session: Session, tmp_path: Path):
    """反向边界：没有反激活时明细必须是空表，而不是「计数 0 + 键缺失」。"""
    root = tmp_path / "scripts"
    _write_script_version(root, "demo", "1.0.0")
    scan_script_root(db_session, root)

    again = scan_script_root(db_session, root)
    assert again.deactivated == 0
    assert again.deactivated_versions == []
    assert again.to_dict()["deactivated_versions"] == []


def test_scan_registers_new_version_active(
        db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    _write_script_version(root, "demo", "1.0.0")
    scan_script_root(db_session, root)

    _write_script_version(root, "demo", "1.1.0")
    scan_script_root(db_session, root)
    row = db_session.query(Script).filter_by(name="demo", version="1.1.0").one()
    assert row.is_active is True


def test_force_rebaseline_reactivates_as_explicit_operator_hatch(
        db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    version_dir = _write_script_version(root, "demo", "1.0.0")
    scan_script_root(db_session, root)

    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    row.is_active = False
    db_session.commit()

    # 内容变了 + force_rebaseline：显式运维动作，允许复活并重锚 sha
    (version_dir / "demo.py").write_text("print('changed')\n", encoding="utf-8")
    scan_script_root(db_session, root, force_rebaseline=True)
    db_session.refresh(row)
    assert row.is_active is True
