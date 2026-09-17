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


def test_guard_blocks_deactivation_when_tree_is_not_deploy_target(
        db_session: Session, tmp_path: Path, monkeypatch):
    """#2386 验收 3：被扫子树 ≠ 部署目标（origin/main）→ **不反激活**。

    现场：生产 ``STP_SCRIPT_ROOT`` 就是共享主工作树，别的会话把检出切到某分支后，
    窗口内跑一次 scan 就会把主线活跃版本静默退役（且目录回来再扫也不复活）。
    守卫把闸门加在「读了非权威树」这个真因上：仍新增/刷新/报冲突，只拒绝反激活。
    """
    import shutil

    root = tmp_path / "scripts"
    version_dir = _write_script_version(root, "demo", "1.0.0")
    scan_script_root(db_session, root)
    shutil.rmtree(version_dir)

    monkeypatch.setattr(
        "backend.services.script_catalog.script_tree_matches_deploy_target",
        lambda *a, **k: False,
    )
    result = scan_script_root(db_session, root)

    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.is_active is True, "非部署目标树上不得反激活（反激活是单向的）"
    assert result.deactivated == 0
    assert result.deactivated_versions == []
    # 「本会反激活」的清单要能让操作员看出被拦的是谁
    assert [(d["name"], d["version"]) for d in result.deactivation_skipped_versions] == [
        ("demo", "1.0.0"),
    ]


def test_guard_allows_explicit_deactivation_on_non_deploy_tree(
        db_session: Session, tmp_path: Path, monkeypatch):
    """确要在非主线树上退役时，``allow_deactivate=True`` 是那条显式、可审计的出口。"""
    import shutil

    root = tmp_path / "scripts"
    version_dir = _write_script_version(root, "demo", "1.0.0")
    scan_script_root(db_session, root)
    shutil.rmtree(version_dir)

    calls = {"n": 0}

    def _should_not_be_asked(*_a, **_k):
        calls["n"] += 1
        return False

    monkeypatch.setattr(
        "backend.services.script_catalog.script_tree_matches_deploy_target",
        _should_not_be_asked,
    )
    result = scan_script_root(db_session, root, allow_deactivate=True)

    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    assert row.is_active is False
    assert result.deactivated == 1
    assert calls["n"] == 0, "显式开关下不必再花一次 git 调用"


def test_guard_fails_open_when_target_is_undeterminable(
        db_session: Session, tmp_path: Path):
    """判据不可得（非 git 树，如发布包）→ **fail-open**，不挡合法部署。

    发布包是不可变发布物，不存在「切分支」场景；把「判据不可用」当成「不一致」会让
    一次正常部署被拒——两个方向的代价不对称，故取 fail-open。
    """
    import shutil

    from backend.services.script_catalog import script_tree_matches_deploy_target

    root = tmp_path / "scripts"
    version_dir = _write_script_version(root, "demo", "1.0.0")
    scan_script_root(db_session, root)
    shutil.rmtree(version_dir)

    assert script_tree_matches_deploy_target(root) is None  # tmp_path 不在 git 仓库内
    result = scan_script_root(db_session, root)
    assert result.deactivated == 1, "判据不可得时应照常反激活（fail-open）"


def test_script_tree_matches_deploy_target_real_git(tmp_path: Path):
    """判据本体（真 git）：子树与 base 一致 → True；改一个字节 → False。"""
    import subprocess

    from backend.services.script_catalog import script_tree_matches_deploy_target

    repo = tmp_path / "repo"
    version_dir = repo / "backend/agent/scripts/demo/v1.0.0"
    version_dir.mkdir(parents=True)
    (version_dir / "demo.py").write_text("print('ok')\n", encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-qm", "init")
    git("update-ref", "refs/remotes/origin/main", "HEAD")

    root = repo / "backend" / "agent" / "scripts"
    assert script_tree_matches_deploy_target(root) is True
    (version_dir / "demo.py").write_text("print('changed')\n", encoding="utf-8")
    assert script_tree_matches_deploy_target(root) is False
