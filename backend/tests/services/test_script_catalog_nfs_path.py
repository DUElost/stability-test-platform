"""nfs_path 是 Agent 侧路径：runtime_root 变化时必须跟着重新锚定。

238 现场：先装控制面（无 Agent → 无 STP_SCRIPT_RUNTIME_ROOT）时首次 scan 把
nfs_path 写成**控制面**路径；首台 Agent 接入补齐该键后，scan 因内容未变而跳过，
nfs_path 停在控制面路径 → 每次派发 `cannot map nfs_path`（受控链永远跑不起来）。
"""

from pathlib import Path

from sqlalchemy.orm import Session

from backend.models.script import Script
from backend.services.script_catalog import scan_script_root

RUNTIME_ROOT = "/opt/stability-test-agent/agent/scripts"


def _write_script_version(root: Path, name: str, version: str) -> Path:
    version_dir = root / name / f"v{version}"
    version_dir.mkdir(parents=True)
    (version_dir / f"{name}.py").write_text("print('ok')\n", encoding="utf-8")
    return version_dir


def test_scan_reanchors_nfs_path_when_runtime_root_appears(
        db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    _write_script_version(root, "demo", "1.0.0")

    scan_script_root(db_session, root)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    control_plane_path = str(root / "demo" / "v1.0.0" / "demo.py")
    assert row.nfs_path == control_plane_path
    sha_before = row.content_sha256

    result = scan_script_root(db_session, root, runtime_root=RUNTIME_ROOT)
    db_session.refresh(row)
    assert row.nfs_path == f"{RUNTIME_ROOT}/demo/v1.0.0/demo.py"
    # 内容身份不变、不新增：只是路径锚点跟随站点配置
    assert row.content_sha256 == sha_before
    assert result.created == 0
    assert result.skipped == 1


def test_scan_keeps_nfs_path_when_runtime_root_is_unchanged(
        db_session: Session, tmp_path: Path):
    root = tmp_path / "scripts"
    _write_script_version(root, "demo", "1.0.0")

    scan_script_root(db_session, root, runtime_root=RUNTIME_ROOT)
    row = db_session.query(Script).filter_by(name="demo", version="1.0.0").one()
    first = row.nfs_path

    scan_script_root(db_session, root, runtime_root=RUNTIME_ROOT)
    db_session.refresh(row)
    assert row.nfs_path == first
