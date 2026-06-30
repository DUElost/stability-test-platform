"""Unit tests for event directory naming helpers."""

from __future__ import annotations

from pathlib import Path

from backend.agent.aee.event_dirs import (
    event_dir_basename_from_path,
    find_event_dir_under_root,
    is_event_dir_basename,
)


def test_is_event_dir_basename_iso_and_compact():
    assert is_event_dir_basename("2026-06-23_14-30-00_db.01")
    assert is_event_dir_basename("2026_0629_174940_206_db.74.ANR")
    assert not is_event_dir_basename("db.00.ANR.dbg.DEC")
    assert not is_event_dir_basename("some_random_dir")


def test_event_dir_basename_from_path():
    assert event_dir_basename_from_path(
        "/mnt/hdd/aee_events/folder/serial/2026_0629_174940_206_db.74.ANR/__exp_main.txt"
    ) == "2026_0629_174940_206_db.74.ANR"
    assert event_dir_basename_from_path(
        r"Y:\sonic_tinno\devices\55\2026-06-23_14-30-00_db.01\main.dbg"
    ) == "2026-06-23_14-30-00_db.01"
    assert event_dir_basename_from_path("/data/aee_exp/db.74.ANR") is None


def test_find_event_dir_under_root_nested(tmp_path):
    root = tmp_path / "hdd"
    event = root / "folder" / "serial" / "2026_0629_002306_121_db.71.JE"
    event.mkdir(parents=True)
    (event / "ZZ_INTERNAL").write_text("x", encoding="utf-8")

    found = find_event_dir_under_root(root, "2026_0629_002306_121_db.71.JE")
    assert found == event
