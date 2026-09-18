"""Tests for backend/scripts/measure_center_storage.py（I-12/I-13 只读基线采集）。"""

from __future__ import annotations

import json
import os

import pytest

from backend.storage_families import RUN_FAMILIES
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
    """造一个最小中心存储树：run 7 在 devices/jira 各一份，run 8 只在 devices。

    ``_meta/7/`` 与 ``jobs/99/`` 各自的用途：前者证明 run 主键族**全部**进测量族清单
    （#2188：``_meta`` 曾被 retention 清、却不在测量族里 → E-2 结构性看不见），
    后者证明 job 主键族不冒充 run（``jobs/{job_id}`` 不是 run）。"""
    _write(root / "devices" / "7" / "ev-a" / "f.bin", 100)
    _write(root / "devices" / "8" / "ev-b" / "f.bin", 50)
    _write(root / "devices" / "unassigned" / "ev-c" / "f.bin", 20)
    _write(root / "jira" / "7" / "ev-a" / "f.bin", 100)
    _write(root / "dedup" / "7" / "mtk" / "h_org.xls", 3)
    _write(root / "dedup" / "7" / "merge" / "mtk" / "Result_MergeFiles.xls", 40)
    _write(root / "jira" / "7" / "merge" / "mtk" / "Result_MergeFiles.xls", 40)
    _write(root / "jobs" / "99" / "artifact.txt", 11)
    _write(root / "_meta" / "7" / "172-21-1-1.json", 7)


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
    # jobs/{job_id} 按 job 主键分桶，不得混进 run_counts（#2188）
    assert "jobs" not in report["run_counts"]
    assert report["job_dir_count"] == 1
    assert report["job_dir_bytes"] == 11
    # jira/7 = 事件 100 + merge xls 40 = 140；devices/7 = 100；devices/8 = 50
    assert [row["name"] for row in report["top_runs_by_bytes"]] == [
        "jira/7",
        "devices/7",
    ]
    assert report["top_runs_by_bytes"][0]["bytes"] == 140
    assert "E-2" in report["not_covered"][0]


@pytest.mark.parametrize("family", sorted(RUN_FAMILIES))
def test_every_run_family_is_visible_in_the_report(tmp_path, family):
    """每一个 run 主键族都必须在报告里可见——**清单由单一来源参数化**。

    这条是 #2188 的直接教训：``_meta/{run_id}/`` 有 retention 清理、却没有测量族条目，
    于是该族的残留对 E-2 对账完全不可见，「漏桶=E-2 必挂」这句自述判据不成立。
    断言写成对 ``RUN_FAMILIES`` 参数化，新增一族即自动获得覆盖（不再靠有人记得补）。
    """
    _write(tmp_path / family / "7" / "blob.bin", 9)

    report = mcs.collect_baseline(tmp_path)

    assert report["families"][family]["bytes"] == 9
    assert report["run_counts"][family] == 1
    assert any(row["name"].startswith(f"{family}/") for row in report["top_runs_by_bytes"])


def test_jobs_family_measured_as_job_keyed_dirs(tmp_path):
    """``jobs`` 有总量（进 ``families``），但分解口径是 job 目录数而非 run 数。"""
    _write(tmp_path / "jobs" / "99" / "artifact.txt", 11)
    _write(tmp_path / "jobs" / "100" / "artifact.txt", 5)

    report = mcs.collect_baseline(tmp_path)

    assert report["families"]["jobs"]["bytes"] == 16
    assert report["job_dir_count"] == 2
    assert report["job_dir_bytes"] == 16
    assert not any(
        row["name"].startswith("jobs/") for row in report["top_runs_by_bytes"]
    )


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
