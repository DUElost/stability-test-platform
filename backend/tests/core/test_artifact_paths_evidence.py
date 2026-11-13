"""证据区路径解析与接收端校验（B8 G6 / D9 第 6 条）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core import artifact_paths as ap
from backend.agent.contracts.evidence_layout import EVIDENCE_ROOT

FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
EVENT = "2026_1031_120000_123_db.74.ANR"
REL = f"{EVIDENCE_ROOT}/1245_{FOLDER}/AZ6Q123456/{EVENT}"


@pytest.fixture
def roots(tmp_path, monkeypatch):
    log = tmp_path / "log"
    cp = tmp_path / "cp"
    log.mkdir()
    cp.mkdir()
    monkeypatch.setattr(ap, "resolve_shared_storage_root", lambda: str(log))
    monkeypatch.setattr(ap, "resolve_control_plane_storage_root", lambda: str(cp))
    return log, cp


def _dest(log: Path) -> Path:
    dest = log / REL
    dest.mkdir(parents=True)
    (dest / "main.dbg").write_bytes(b"x")
    return dest


def test_evidence_area_uses_log_root(roots):
    log, _ = roots
    assert ap.evidence_area_root() == (log / EVIDENCE_ROOT).resolve(strict=False)


def test_resolve_accepts_full_relative_identity(roots):
    log, _ = roots
    dest = _dest(log)
    assert ap.resolve_evidence_event_path(
        REL, root_plan_run_id=1245, serial="AZ6Q123456", event_dir=EVENT, must_exist=True,
    ) == dest


def test_resolve_rejects_wrong_chain_serial_event(roots):
    log, _ = roots
    _dest(log)
    for kwargs in (
        {"root_plan_run_id": 999},
        {"serial": "OTHER"},
        {"event_dir": "2026_1031_120000_999_db.01.JE"},
    ):
        with pytest.raises(ap.ArtifactPathOutsideRootError):
            ap.resolve_evidence_event_path(REL, must_exist=True, **kwargs)


def test_resolve_rejects_symlink_component(roots, tmp_path):
    log, _ = roots
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "main.dbg").write_bytes(b"x")
    (log / EVIDENCE_ROOT).mkdir()
    (log / EVIDENCE_ROOT / f"1245_{FOLDER}").symlink_to(outside)
    with pytest.raises(ap.ArtifactPathOutsideRootError):
        ap.resolve_evidence_event_path(REL, must_exist=True)


def test_resolve_rejects_symlink_inside_event_dir(roots):
    log, _ = roots
    dest = _dest(log)
    (dest / "escape").symlink_to("/etc/hostname")
    with pytest.raises(ap.ArtifactPathOutsideRootError):
        ap.resolve_evidence_event_path(REL, must_exist=True)


def test_resolve_missing_requires_must_exist(roots):
    log, _ = roots
    with pytest.raises(ap.ArtifactPathNotFoundError):
        ap.resolve_evidence_event_path(REL, must_exist=True)
    assert not ap.resolve_evidence_event_path(REL).exists()
