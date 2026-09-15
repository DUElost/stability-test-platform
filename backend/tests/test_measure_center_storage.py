"""Tests for backend/scripts/measure_center_storage.py（I-12/I-13 只读基线采集）。"""

from __future__ import annotations

import json
import os

import pytest

from backend.scripts import measure_center_storage as mcs


def _write(path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_walk_usage_counts_bytes_files_and_dirs(tmp_path):
    _write(tmp_path / "a.bin", 10)
    _write(tmp_path / "sub" / "b.bin", 5)

    usage = mcs.walk_usage(tmp_path)

    assert usage.bytes == 15
    assert usage.files == 2
    # root 自身 + sub
    assert usage.dirs == 2


def test_walk_usage_does_not_follow_symlinked_dir(tmp_path):
    """只读测量不得跟随 symlink：否则同一份数据会被重复计入。"""
    _write(tmp_path / "real" / "payload.bin", 7)
    os.symlink(tmp_path / "real", tmp_path / "link")

    usage = mcs.walk_usage(tmp_path)

    assert usage.bytes == 7, "symlink 目标内容被重复统计"
    assert usage.files == 1


def test_walk_usage_returns_empty_for_missing_root(tmp_path):
    assert mcs.walk_usage(tmp_path / "nope") == mcs.Usage()


@pytest.mark.parametrize("raw", ["/", "/usr", ""])
def test_resolve_center_root_rejects_dangerous_or_empty(raw):
    with pytest.raises(mcs.UnsafeRootError):
        mcs.resolve_center_root(raw)


def test_resolve_center_root_rejects_non_directory(tmp_path):
    target = tmp_path / "file.txt"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(mcs.UnsafeRootError):
        mcs.resolve_center_root(str(target))


def _seed_center(root) -> None:
    """造一个最小中心存储树：run 7 在 devices/jira 各一份，run 8 只在 devices。"""
    _write(root / "devices" / "7" / "ev-a" / "f.bin", 100)
    _write(root / "devices" / "8" / "ev-b" / "f.bin", 50)
    _write(root / "devices" / "unassigned" / "ev-c" / "f.bin", 20)
    _write(root / "jira" / "7" / "ev-a" / "f.bin", 100)
    _write(root / "dedup" / "7" / "mtk" / "h_org.xls", 3)
    _write(root / "dedup" / "7" / "merge" / "mtk" / "Result_MergeFiles.xls", 40)
    _write(root / "jira" / "7" / "merge" / "mtk" / "Result_MergeFiles.xls", 40)
    _write(root / "jobs" / "99" / "artifact.txt", 11)


def test_collect_baseline_reports_event_dir_duplication(tmp_path):
    _seed_center(tmp_path)

    report = mcs.collect_baseline(tmp_path)

    dup = report["duplication"]["event_dirs"]
    # devices: run7=100 + run8=50（unassigned 不算 run）；jira: run7=100（含 merge xls 40）
    assert dup["devices_bytes"] == 150
    assert dup["jira_bytes"] == 140
    assert dup["overlap_runs"] == ["7"]
    assert dup["overlap_count"] == 1
    assert "unassigned" not in dup["runs_in_devices"]


def test_collect_baseline_reports_merge_subdir_usage(tmp_path):
    _seed_center(tmp_path)

    report = mcs.collect_baseline(tmp_path)

    assert report["merge_subdir_usage"]["dedup"] == {
        "merge_bytes": 40,
        "merge_files": 1,
        "runs_with_merge": 1,
    }
    assert report["merge_subdir_usage"]["jira"] == {
        "merge_bytes": 40,
        "merge_files": 1,
        "runs_with_merge": 1,
    }


def test_collect_baseline_reports_run_counts_and_top(tmp_path):
    _seed_center(tmp_path)

    report = mcs.collect_baseline(tmp_path, top=2)

    assert report["run_counts"]["devices"] == 3  # 7 / 8 / unassigned
    assert report["run_counts"]["jira"] == 1
    assert report["run_counts"]["jobs"] == 1
    # jira/7 = 事件 100 + merge xls 40 = 140；devices/7 = 100；devices/8 = 50
    assert [row["name"] for row in report["top_runs_by_bytes"]] == [
        "jira/7",
        "devices/7",
    ]
    assert report["top_runs_by_bytes"][0]["bytes"] == 140
    assert "E-2" in report["not_covered"][0]


def test_collect_handles_empty_center_root(tmp_path):
    report = mcs.collect_baseline(tmp_path)

    assert report["families"] == {}
    assert report["run_counts"] == {}
    assert report["duplication"]["event_dirs"]["overlap_count"] == 0
    assert report["top_runs_by_bytes"] == []


def test_local_merge_result_absent_and_present(tmp_path):
    assert mcs.collect_local_merge_result(None) is None

    missing = mcs.collect_local_merge_result(tmp_path / "nope")
    assert missing is not None and missing["exists"] == "no"

    merge_root = tmp_path / "merge_result"
    _write(merge_root / "2026_09_15_10_00_00" / "Result_MergeFiles.xls", 12)
    present = mcs.collect_local_merge_result(merge_root)
    assert present is not None
    assert present["exists"] == "yes"
    assert present["bytes"] == 12
    assert present["subdirs"] == 1
    assert present["newest_mtime"] > 0


def test_derive_merge_result_root_from_scan_script():
    assert mcs.derive_merge_result_root({}) is None
    assert mcs.derive_merge_result_root({"STP_BACKEND_DEDUP_SCAN_SCRIPT": ""}) is None
    derived = mcs.derive_merge_result_root(
        {"STP_BACKEND_DEDUP_SCAN_SCRIPT": "/mnt/tools/start_log_scan.py"}
    )
    assert derived is not None
    assert derived.as_posix().endswith("/mnt/tools/merge_result")


def test_main_refuses_dangerous_root_without_touching_it(capsys, monkeypatch):
    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)

    assert mcs.main(["--center-root", "/"]) == 2

    assert "refusing to run" in capsys.readouterr().err


def test_main_json_output(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
    _seed_center(tmp_path)

    assert mcs.main(["--center-root", str(tmp_path), "--json", "--top", "1"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["center_root"] == str(tmp_path)
    assert payload["duplication"]["event_dirs"]["overlap_count"] == 1
    assert len(payload["top_runs_by_bytes"]) == 1


def test_format_report_renders_all_sections(tmp_path):
    _seed_center(tmp_path)
    report = mcs.collect_baseline(
        tmp_path, merge_result_root=tmp_path / "merge_result", top=1,
    )

    text = mcs.format_report(report)

    assert "E-1 event dir duplication" in text
    assert "E-1b merge reports" in text
    assert "E-3 local merge_result" in text
    assert "(absent)" in text
    assert "not covered by this script" in text
