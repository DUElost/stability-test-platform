"""证据区契约（B8 G6 / ADR-0062 D9 第 6 条）的路径与复制反例。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.agent.contracts.evidence_layout import (
    EVIDENCE_ROOT,
    NAMING_RULE_VERSION,
    EvidenceLayoutError,
    collection_folder_from_event_path,
    copytree_no_symlinks,
    dir_sha256_and_size,
    ensure_dir_under_root,
    evidence_rel_path,
    parse_evidence_rel_path,
    primary_folder_name,
)

FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
EVENT = "2026_1031_120000_123_db.74.ANR"


def _event_dir(root: Path, *, serial: str = "AZ6Q123456", folder: str = FOLDER) -> Path:
    event = root / folder / serial / "aee_exp" / EVENT
    event.mkdir(parents=True)
    (event / "main.dbg").write_bytes(b"dbg")
    (event / "mobilelog").mkdir()
    (event / "mobilelog" / "log.txt").write_bytes(b"log")
    return event


def test_evidence_path_roundtrip_and_run_prefix():
    rel = evidence_rel_path(
        root_plan_run_id=1245, collection_folder=FOLDER,
        serial="AZ6Q123456", event_dir=EVENT,
    )
    assert rel == f"{EVIDENCE_ROOT}/1245_{FOLDER}/AZ6Q123456/{EVENT}"
    assert parse_evidence_rel_path(
        rel, root_plan_run_id=1245, serial="AZ6Q123456", event_dir=EVENT,
    ) == (EVIDENCE_ROOT, f"1245_{FOLDER}", "AZ6Q123456", EVENT)
    assert NAMING_RULE_VERSION.startswith("aee-folder-name/")
    assert primary_folder_name(1245, FOLDER) == f"1245_{FOLDER}"


@pytest.mark.parametrize(
    "rel",
    [
        "evidence/../outside",
        "/evidence/1245_x_MonkeyAEEinfo/SN/2026_1031_120000_123_db.74.ANR",
        "evidence/1245_" + FOLDER + "/SN/2026_1031_120000_123_db.74.ANR/extra",
        "evidence/999_" + FOLDER + "/SN/2026_1031_120000_123_db.74.ANR",
        "evidence/1245_" + FOLDER + "/OTHER/2026_1031_120000_123_db.74.ANR",
        "evidence/1245_" + FOLDER + "/SN/not-an-event",
        "evidence\\1245_" + FOLDER + "\\SN\\2026_1031_120000_123_db.74.ANR",
        f"devices/1245_{FOLDER}/SN/{EVENT}",
    ],
)
def test_parse_rejects_escape_wrong_chain_serial_and_event(rel):
    with pytest.raises(EvidenceLayoutError):
        parse_evidence_rel_path(rel, root_plan_run_id=1245, serial="SN")


def test_collection_folder_comes_from_recorded_local_path(tmp_path):
    local = _event_dir(tmp_path / "aee")
    folder, event_dir = collection_folder_from_event_path(
        str(local), serial="AZ6Q123456",
    )
    assert folder == FOLDER
    assert event_dir == EVENT


def test_collection_folder_rejects_watcher_layout_and_serial_mismatch(tmp_path):
    watcher = tmp_path / "jobs" / "42" / "AEE" / EVENT
    watcher.mkdir(parents=True)
    with pytest.raises(EvidenceLayoutError):
        collection_folder_from_event_path(str(watcher), serial="AZ6Q123456")
    with pytest.raises(EvidenceLayoutError):
        collection_folder_from_event_path(
            str(_event_dir(tmp_path / "aee")), serial="OTHER",
        )


def test_copytree_no_symlinks_copies_tree_and_refuses_existing_dest(tmp_path):
    src = _event_dir(tmp_path / "aee")
    root = tmp_path / "log"
    root.mkdir()
    dest = root / "evidence" / f"1245_{FOLDER}" / "AZ6Q123456" / EVENT
    copytree_no_symlinks(src, dest, dest_root=root)
    assert (dest / "main.dbg").read_bytes() == b"dbg"
    assert dir_sha256_and_size(src) == dir_sha256_and_size(dest)
    with pytest.raises(FileExistsError):
        copytree_no_symlinks(src, dest, dest_root=root)


def test_copytree_no_symlinks_rejects_symlink_in_source(tmp_path):
    src = _event_dir(tmp_path / "aee")
    os.symlink("/etc/hostname", src / "escape")
    root = tmp_path / "log"
    root.mkdir()
    dest = root / "evidence" / "p" / "SN" / EVENT
    with pytest.raises(EvidenceLayoutError):
        copytree_no_symlinks(src, dest, dest_root=root)
    assert not dest.exists()


def test_ensure_dir_rejects_symlink_component(tmp_path):
    root = tmp_path / "log"
    (root / "evidence").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "evidence" / "primary").symlink_to(outside)
    with pytest.raises(EvidenceLayoutError):
        ensure_dir_under_root(root, ["evidence", "primary", "SN"])
    assert not (outside / "SN").exists()
