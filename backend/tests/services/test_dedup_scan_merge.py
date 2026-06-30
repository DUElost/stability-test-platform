"""Tests for run_merge_sync argv selection and post-merge validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.services import dedup_scan as ds


@pytest.fixture(autouse=True)
def _reset_merge_probe_cache():
    ds.reset_merge_capability_cache_for_tests()
    yield
    ds.reset_merge_capability_cache_for_tests()


def test_build_merge_argv_prefers_merge_files_list_when_supported(tmp_path):
    org = [str(tmp_path / "a_org.xls"), str(tmp_path / "b_org.xls")]
    tool = {"python": "python", "script": str(tmp_path / "start_log_scan.py")}
    (tmp_path / "start_log_scan.py").write_text("# stub", encoding="utf-8")

    with patch.object(ds, "scan_tool_supports_merge_files_list", return_value=True):
        argv, listfile = ds.build_merge_argv(tool, org, ["-side", "shanghai"])

    assert "-merge_files_list" in argv
    assert listfile is not None
    assert listfile.read_text(encoding="utf-8").splitlines() == org
    listfile.unlink(missing_ok=True)


def test_build_merge_argv_falls_back_to_merge_files(tmp_path):
    org = [str(tmp_path / "a_org.xls")]
    tool = {"python": "python", "script": str(tmp_path / "start_log_scan.py")}
    (tmp_path / "start_log_scan.py").write_text("# stub", encoding="utf-8")

    with patch.object(ds, "scan_tool_supports_merge_files_list", return_value=False):
        argv, listfile = ds.build_merge_argv(tool, org, ["-side", "shanghai"])

    assert argv[:3] == ["python", tool["script"], "-merge_files"]
    assert org[0] in argv
    assert "-side" in argv
    assert listfile is None


def test_find_fresh_merge_output_dir_requires_new_subdir(tmp_path):
    merge_root = tmp_path / "merge_result"
    old = merge_root / "2026_06_25_21_25_13"
    old.mkdir(parents=True)
    (old / "Result_MergeFiles.xls").write_bytes(b"x")
    baseline = ds.latest_merge_output_mtime(merge_root)

    with pytest.raises(RuntimeError, match="no fresh merge output"):
        ds.find_fresh_merge_output_dir(merge_root, baseline, before_names={"2026_06_25_21_25_13"})

    new = merge_root / "2026_06_30_11_02_25"
    new.mkdir()
    (new / "Result_MergeFiles.xls").write_bytes(b"y")
    found = ds.find_fresh_merge_output_dir(merge_root, baseline, before_names={"2026_06_25_21_25_13"})
    assert found == new


def test_merge_stderr_indicates_failure():
    assert ds.merge_stderr_indicates_failure("start_log_scan.py: error: argument -m/--mode")
    assert not ds.merge_stderr_indicates_failure("[INFO] merge done")


def test_run_merge_sync_raises_when_subprocess_stderr_has_error(tmp_path):
    merge_root = tmp_path / "merge_result"
    merge_root.mkdir()
    tool = {"python": "python", "script": str(tmp_path / "start_log_scan.py")}
    (tmp_path / "start_log_scan.py").write_text("# stub", encoding="utf-8")

    proc = MagicMock()
    proc.returncode = 0
    proc.stderr = "start_log_scan.py: error: invalid int value: 'erge_files_list'"
    proc.stdout = ""

    with patch.object(ds, "resolve_scan_tool", return_value=tool), \
         patch.object(ds, "_load_org_files_for_merge", return_value=["/fake/a_org.xls"]), \
         patch.object(ds, "build_merge_argv", return_value=(["python", "scan.py", "-merge_files_list", "x"], None)), \
         patch.object(ds, "latest_merge_output_mtime", return_value=0.0), \
         patch.object(ds, "_merge_output_dir_names", return_value=set()), \
         patch("backend.services.dedup_scan.subprocess.run", return_value=proc):
        with pytest.raises(RuntimeError, match="merge subprocess reported errors"):
            ds.run_merge_sync(99)
