"""Tests for run_merge_sync argv selection and post-merge validation."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.core import metrics
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


def test_build_merge_argv_unsupported_tool_raises(tmp_path):
    """#291: 探测失败 = 配置错误，直接抛错；不再回落 -merge_files。"""
    org = [str(tmp_path / "a_org.xls")]
    tool = {"python": "python", "script": str(tmp_path / "start_log_scan.py")}
    (tmp_path / "start_log_scan.py").write_text("# stub", encoding="utf-8")

    with patch.object(ds, "scan_tool_supports_merge_files_list", return_value=False):
        with pytest.raises(RuntimeError, match="does not support -merge_files_list"):
            ds.build_merge_argv(tool, org, ["-side", "shanghai"])


def test_resolve_scan_tool_prefers_backend_keys(monkeypatch):
    """#295: 控制面专用键优先；旧无前缀键同时存在也不使用。"""
    monkeypatch.setenv("STP_BACKEND_DEDUP_SCAN_PYTHON", "/backend/python")
    monkeypatch.setenv("STP_BACKEND_DEDUP_SCAN_SCRIPT", "/backend/scan.py")
    monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/legacy/python")
    monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/legacy/scan.py")

    assert ds.resolve_scan_tool() == {
        "python": "/backend/python",
        "script": "/backend/scan.py",
    }


def test_resolve_scan_tool_none_when_unset(monkeypatch):
    """#518: 未配置 STP_BACKEND_DEDUP_SCAN_* → None（config-gated 503 语义不变）。"""
    monkeypatch.delenv("STP_BACKEND_DEDUP_SCAN_PYTHON", raising=False)
    monkeypatch.delenv("STP_BACKEND_DEDUP_SCAN_SCRIPT", raising=False)
    monkeypatch.delenv("STP_DEDUP_SCAN_PYTHON", raising=False)
    monkeypatch.delenv("STP_DEDUP_SCAN_SCRIPT", raising=False)

    assert ds.resolve_scan_tool() is None


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


def test_run_merge_sync_skips_failed_plan_run(db_session, sample_plan_run, monkeypatch):
    """ADR-0028 D2: FAILED PlanRun must not merge even with scan artifacts present."""
    from backend.models.enums import PlanRunStatus

    failed_counter = MagicMock()
    monkeypatch.setattr(metrics, "merge_skip_failed_plan_run_total", failed_counter)

    sample_plan_run.status = PlanRunStatus.FAILED.value
    db_session.commit()

    def _must_not_resolve_tool():
        raise AssertionError("merge tool must not be resolved for a FAILED PlanRun")

    with patch.object(ds, "resolve_scan_tool", side_effect=_must_not_resolve_tool):
        assert ds.run_merge_sync(sample_plan_run.id) == ""
    # P1：跳过路径计数（failed_plan_run 是预期门禁，计数不告警）
    failed_counter.inc.assert_called_once_with()


def test_run_merge_sync_skips_when_tool_not_configured(
    db_session, sample_plan_run, monkeypatch,
):
    """#518: STP_BACKEND_DEDUP_SCAN_* 缺失 → 静默跳过；P1 非零即告警计数。"""
    tool_counter = MagicMock()
    monkeypatch.setattr(metrics, "merge_skip_tool_not_configured_total", tool_counter)

    with patch.object(ds, "resolve_scan_tool", return_value=None):
        assert ds.run_merge_sync(sample_plan_run.id) == ""

    tool_counter.inc.assert_called_once_with()


def test_run_merge_sync_skips_no_org_files(db_session, sample_plan_run, monkeypatch):
    """无本轮 org 文件 → 静默跳过（空产物自然跳过，计数不告警）。"""
    no_files_counter = MagicMock()
    monkeypatch.setattr(metrics, "merge_skip_no_org_files_total", no_files_counter)

    tool = {"python": "python", "script": "/tmp/start_log_scan.py"}
    with patch.object(ds, "resolve_scan_tool", return_value=tool):
        assert ds.run_merge_sync(sample_plan_run.id) == ""

    no_files_counter.inc.assert_called_once_with()


def test_count_hosts_with_scan_artifacts_scopes_to_since_watermark(
    db_session, sample_plan_run,
):
    """Earlier-round artifacts for the same host must not satisfy this round.

    Incremental scans reuse the same plan_run_id; without the ``since`` filter,
    host-a's stale row makes the first poll read 1/1 and break before host-a's
    new upload lands.
    """
    from datetime import datetime, timedelta, timezone

    from backend.models.plan_run_artifact import PlanRunArtifact

    run_id = sample_plan_run.id
    stale_at = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)
    fresh_at = datetime(2026, 8, 8, 7, 0, tzinfo=timezone.utc)
    watermark = datetime(2026, 8, 8, 6, 30, tzinfo=timezone.utc)

    db_session.add(
        PlanRunArtifact(
            plan_run_id=run_id,
            host_id="host-a",
            storage_uri="/tmp/stale_org.xls",
            artifact_type=ds.ARTIFACT_TYPE_SCAN,
            size_bytes=100,
            created_at=stale_at,
        )
    )
    db_session.add(
        PlanRunArtifact(
            plan_run_id=run_id,
            host_id="host-a",
            storage_uri="/tmp/fresh_org.xls",
            artifact_type=ds.ARTIFACT_TYPE_SCAN,
            size_bytes=100,
            created_at=fresh_at,
        )
    )
    db_session.commit()

    # Stale row alone does not count once the watermark is past it.
    assert ds.count_hosts_with_scan_artifacts(run_id, ["host-a"], since=watermark) == 1
    assert ds.count_hosts_with_scan_artifacts(run_id, ["host-a"], since=fresh_at) == 1
    assert ds.count_hosts_with_scan_artifacts(
        run_id, ["host-a"], since=fresh_at + timedelta(seconds=1)
    ) == 0

    # Only the stale row exists before watermark — this is the re-trigger case.
    db_session.query(PlanRunArtifact).filter(
        PlanRunArtifact.storage_uri == "/tmp/fresh_org.xls"
    ).delete()
    db_session.commit()
    assert ds.count_hosts_with_scan_artifacts(run_id, ["host-a"], since=watermark) == 0


# ── merge 产物中心化（2026-08-31）────────────────────────────────────

class TestPublishMergeToCenter:
    def test_publishes_to_center_dedup_merge(self, tmp_path, monkeypatch):
        merge_dir = tmp_path / "merge_result" / "2026_08_30_22_18_02"
        merge_dir.mkdir(parents=True)
        (merge_dir / "Result_MergeFiles.xls").write_text("x", encoding="utf-8")
        (merge_dir / "statistics.txt").write_text("s", encoding="utf-8")
        center = tmp_path / "center"
        center.mkdir()
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(center))

        dest = ds._publish_merge_to_center(270, merge_dir)
        assert dest is not None
        assert dest == center / "dedup" / "270" / "merge"
        assert (dest / "Result_MergeFiles.xls").is_file()
        assert (dest / "statistics.txt").is_file()

    def test_returns_none_when_center_unconfigured(self, tmp_path, monkeypatch):
        merge_dir = tmp_path / "merge_result" / "d1"
        merge_dir.mkdir(parents=True)
        (merge_dir / "Result_MergeFiles.xls").write_text("x", encoding="utf-8")
        monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
        assert ds._publish_merge_to_center(270, merge_dir) is None

    def test_publish_is_idempotent_overwrite(self, tmp_path, monkeypatch):
        merge_dir = tmp_path / "merge_result" / "d2"
        merge_dir.mkdir(parents=True)
        (merge_dir / "Result_MergeFiles.xls").write_text("new", encoding="utf-8")
        center = tmp_path / "center"
        center.mkdir()
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(center))
        ds._publish_merge_to_center(270, merge_dir)
        dest = ds._publish_merge_to_center(270, merge_dir)  # 重跑覆盖
        assert (dest / "Result_MergeFiles.xls").read_text(encoding="utf-8") == "new"


# ── merge 报告 Path 对外重写（2026-08-31）────────────────────────────

class TestMapAgentPathToCenter:
    def test_maps_stp_scan_path_to_center(self):
        src = ("/mnt/hdd/aee_events/.stp-scan/pr280-mc7gh7g0/"
               "MLD-LX2_16_260804V71_0831_MonkeyAEEinfo/AYCGNX6728006411/aee_exp/"
               "2026_0831_020000_789_db.fatal.04.KE/db.fatal.00.KEx/"
               "db.fatal.00.KE.dbg.DEC/__exp_main.txt")
        got = ds._map_agent_path_to_center(src, 280, "/mnt/stp-aee")
        assert got == "/mnt/stp-aee/devices/280/2026_0831_020000_789_db.fatal.04.KE/"

    def test_maps_vendor_aee_exp(self):
        src = ("/mnt/hdd/aee_events/.stp-scan/pr280-x/"
               "FOLDER/SERIAL/vendor_aee_exp/2026_0807_231846_000_db.00.NE/main.dbg")
        got = ds._map_agent_path_to_center(src, 280, "/mnt/stp-aee")
        assert got == "/mnt/stp-aee/devices/280/2026_0807_231846_000_db.00.NE/"

    def test_non_event_path_unchanged(self):
        assert ds._map_agent_path_to_center(
            "/mnt/hdd/aee_events/Result_x.xls", 280, "/mnt/stp-aee"
        ) == "/mnt/hdd/aee_events/Result_x.xls"


class TestRewriteMergeReportPaths:
    def test_rewrites_path_column_in_xls(self, tmp_path):
        import xlwt

        # 隔离 center：建 devices/280/04（本 run 候选存在——不触发兜底）
        center = tmp_path / "center"
        ev = center / "devices" / "280" / "2026_0831_020000_789_db.fatal.04.KE"
        ev.mkdir(parents=True)

        xls = tmp_path / "Result_MergeFiles.xls"
        wb = xlwt.Workbook()
        ws = wb.add_sheet("Sheet1")
        ws.write(0, 0, "Id"); ws.write(0, 1, "Path"); ws.write(0, 2, "ExpClass")
        ws.write(1, 0, "0.0")
        ws.write(1, 1, ("/mnt/hdd/aee_events/.stp-scan/pr280-x/F/S/aee_exp/"
                        "2026_0831_020000_789_db.fatal.04.KE/__exp_main.txt"))
        ws.write(1, 2, "Kernel (KE)")
        wb.save(str(xls))

        n = ds._rewrite_merge_report_paths_to_center(tmp_path, 280, str(center))
        assert n == 1
        import xlrd
        book = xlrd.open_workbook(str(xls))
        sheet = book.sheet_by_index(0)
        assert sheet.cell_value(1, 1) == (
            f"{center}/devices/280/2026_0831_020000_789_db.fatal.04.KE/")
        assert sheet.cell_value(1, 2) == "Kernel (KE)"  # 其余列原样

    def test_publish_rewrites_center_copy(self, tmp_path, monkeypatch):
        import xlwt

        merge_dir = tmp_path / "merge_result" / "d1"
        merge_dir.mkdir(parents=True)
        xls = merge_dir / "Result_MergeFiles.xls"
        wb = xlwt.Workbook()
        ws = wb.add_sheet("S")
        ws.write(0, 0, "Path")
        ws.write(1, 0, ("/mnt/hdd/aee_events/.stp-scan/pr280-x/F/S/aee_exp/"
                        "2026_0807_231846_000_db.00.NE/main.dbg"))
        wb.save(str(xls))
        center = tmp_path / "center"
        center.mkdir()
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(center))

        dest = ds._publish_merge_to_center(280, merge_dir)
        assert dest is not None
        import xlrd
        book = xlrd.open_workbook(str(dest / "Result_MergeFiles.xls"))
        assert book.sheet_by_index(0).cell_value(1, 0) == (
            f"{center}/devices/280/2026_0807_231846_000_db.00.NE/")


class TestResolveCenterEventPath:
    def test_prefers_current_run(self, tmp_path):
        center = tmp_path / "center"
        ev = center / "devices" / "287" / "2026_0807_231846_000_db.00.NE"
        ev.mkdir(parents=True)
        assert ds._resolve_center_event_path(
            str(center), 287, "2026_0807_231846_000_db.00.NE"
        ) == f"{center}/devices/287/2026_0807_231846_000_db.00.NE/"

    def test_falls_back_to_history_run(self, tmp_path):
        center = tmp_path / "center"
        old = center / "devices" / "270" / "2026_0830_223000_456_db.fatal.02.KE"
        old.mkdir(parents=True)
        got = ds._resolve_center_event_path(
            str(center), 287, "2026_0830_223000_456_db.fatal.02.KE")
        assert got == f"{center}/devices/270/2026_0830_223000_456_db.fatal.02.KE/"

    def test_no_history_keeps_candidate(self, tmp_path):
        center = tmp_path / "center"
        (center / "devices").mkdir(parents=True)
        got = ds._resolve_center_event_path(
            str(center), 287, "2026_0830_223000_456_db.fatal.02.KE")
        assert got == f"{center}/devices/287/2026_0830_223000_456_db.fatal.02.KE/"

    def test_rewrite_falls_back_integration(self, tmp_path):
        import xlwt

        # 历史 run 270 有 02 事件副本；报表引用 02（本 run 287 未上送）
        center = tmp_path / "center"
        old = center / "devices" / "270" / "2026_0830_223000_456_db.fatal.02.KE"
        old.mkdir(parents=True)
        (old / "__exp_main.txt").write_text("x", encoding="utf-8")

        xls = tmp_path / "Result_MergeFiles.xls"
        wb = xlwt.Workbook()
        ws = wb.add_sheet("S")
        ws.write(0, 0, "Path")
        ws.write(1, 0, ("/mnt/hdd/aee_events/.stp-scan/pr287-x/F/S/aee_exp/"
                        "2026_0830_223000_456_db.fatal.02.KE/__exp_main.txt"))
        wb.save(str(xls))

        ds._rewrite_merge_report_paths_to_center(tmp_path, 287, str(center))
        import xlrd
        book = xlrd.open_workbook(str(xls))
        assert book.sheet_by_index(0).cell_value(1, 0) == (
            f"{center}/devices/270/2026_0830_223000_456_db.fatal.02.KE/")
