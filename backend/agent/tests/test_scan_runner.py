"""ScanRunner 单测（ADR-0025 Sprint 4 Task 1）。

覆盖面：
  1. run_local_scan 以 -dedup_org 调用 start_log_scan.py
  2. 未 configure 时返回 None
  3. subprocess 返回非零时返回 None
  4. org.xls 未找到时返回 None
  5. subprocess 超时时返回 None
  6. configure 环境变量降级
  7. _build_argv 含/不含 -end
  8. 重复 configure 被忽略
"""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.scan_runner import ScanRunner


@pytest.fixture(autouse=True)
def _reset_scan_runner():
    from backend.agent.unisoc_scan_runner import UnisocScanRunner

    ScanRunner._reset_for_tests()
    UnisocScanRunner._reset_for_tests()
    yield
    ScanRunner._reset_for_tests()
    UnisocScanRunner._reset_for_tests()


def _make_runner() -> ScanRunner:
    r = ScanRunner.instance()
    r.configure(
        scan_tool_python="/usr/bin/python3",
        scan_tool_script="/opt/scan/start_log_scan.py",
        hdd_root="/mnt/hdd/aee_events",
        side="shanghai",
    )
    assert r.is_configured()
    return r


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def test_run_local_scan_calls_start_log_scan_with_aee_tne_mode(tmp_path):
    r = _make_runner()
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    org_xls = hdd / "Result_shanghai_org.xls"
    org_xls.write_text("fake")
    r._hdd_root = str(hdd)

    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(stdout="done")
        result = r.run_local_scan(42, "host-1")

    assert result is not None
    assert "Result_shanghai_org.xls" in result
    called_argv = mock_run.call_args[0][0]
    assert "-m" in called_argv
    assert "0" in called_argv
    assert "-d" in called_argv
    assert str(hdd) in called_argv
    assert "-side" in called_argv


def test_run_local_scan_not_configured():
    r = ScanRunner.instance()
    assert not r.is_configured()
    result = r.run_local_scan(1, "host-1")
    assert result is None


def test_run_local_scan_tool_failure():
    r = _make_runner()
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=1, stderr="error")
        result = r.run_local_scan(1, "host-1", is_final=True)
    assert result is None


def test_run_local_scan_no_org_xls_found(tmp_path):
    r = _make_runner()
    r._hdd_root = str(tmp_path)
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=0, stdout="done")
        result = r.run_local_scan(1, "host-1")
    assert result is None


def test_run_local_scan_timeout():
    r = _make_runner()
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=[], timeout=600)
        result = r.run_local_scan(1, "host-1")
    assert result is None


def test_configure_env_fallback(monkeypatch):
    monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/env/python3")
    monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/env/scan.py")
    r = ScanRunner.instance()
    with patch("backend.agent.scan_runner.get_aee_local_root", return_value=Path("/env/hdd")):
        r.configure()
    assert r.is_configured()
    assert r._scan_tool_python == "/env/python3"
    assert r._scan_tool_script == "/env/scan.py"


def test_configure_side_reads_env_tag(monkeypatch):
    r = ScanRunner.instance()

    monkeypatch.setenv("STP_DEDUP_SCAN_TAG", "factory")
    r.configure(scan_tool_python="/usr/bin/python3", scan_tool_script="/opt/scan/start_log_scan.py")
    assert r._side == "factory"

    ScanRunner._reset_for_tests()
    r = ScanRunner.instance()
    monkeypatch.setenv("STP_DEDUP_SCAN_TAG", "FACTORY")
    r.configure(scan_tool_python="/usr/bin/python3", scan_tool_script="/opt/scan/start_log_scan.py")
    assert r._side == "factory"

    ScanRunner._reset_for_tests()
    r = ScanRunner.instance()
    monkeypatch.delenv("STP_DEDUP_SCAN_TAG", raising=False)
    r.configure(scan_tool_python="/usr/bin/python3", scan_tool_script="/opt/scan/start_log_scan.py")
    assert r._side == "shanghai"


def test_configure_explicit_side_wins_over_env(monkeypatch):
    monkeypatch.setenv("STP_DEDUP_SCAN_TAG", "factory")
    r = ScanRunner.instance()
    r.configure(
        scan_tool_python="/usr/bin/python3",
        scan_tool_script="/opt/scan/start_log_scan.py",
        side="shanghai",
    )
    assert r._side == "shanghai"


def test_queue_defers_until_configured(monkeypatch):
    monkeypatch.setenv("STP_DEDUP_SCAN_PYTHON", "/usr/bin/python3")
    monkeypatch.setenv("STP_DEDUP_SCAN_SCRIPT", "/opt/scan/start_log_scan.py")
    executed: list[int] = []
    monkeypatch.setattr(
        ScanRunner,
        "_execute_job",
        classmethod(lambda cls, job: executed.append(job.plan_run_id)),
    )

    # 未 configure 时入队：worker 应 requeue 并等待，不执行也不丢弃
    ScanRunner.enqueue_scan_now(71, "host-1", is_final=False)
    time.sleep(2.5)  # worker defer 间隔 2s
    assert ScanRunner.pending_count() == 1
    assert executed == []

    # configure 后 worker 继续消费
    ScanRunner.instance().configure()
    deadline = time.time() + 5
    while not executed and time.time() < deadline:
        time.sleep(0.1)
    assert executed == [71]
    assert ScanRunner.pending_count() == 0


def _configure_unisoc_for_tests() -> None:
    from backend.agent.unisoc_scan_runner import UnisocScanRunner

    UnisocScanRunner.instance().configure(
        scan_tool_python="/usr/bin/python3",
        scan_tool_script="/opt/unisoc/scan_log_gt.py",
        result_python="/usr/bin/python3",
        result_script="/opt/unisoc/scan_result.py",
    )


def test_queue_runs_when_only_unisoc_configured(monkeypatch):
    """#1071: MTK ScanRunner 未配置时不得饿死已配置的 UNISOC 扫描。"""
    from backend.agent.unisoc_scan_runner import UnisocScanRunner

    UnisocScanRunner._reset_for_tests()
    executed: list[str] = []

    def fake_execute(cls, job):
        executed.append("run")

    monkeypatch.setattr(
        ScanRunner, "_execute_job", classmethod(fake_execute),
    )
    _configure_unisoc_for_tests()
    assert not ScanRunner.instance().is_configured()
    assert UnisocScanRunner.instance().is_configured()

    ScanRunner.enqueue_scan_now(88, "host-u", is_final=False)
    deadline = time.time() + 5
    while not executed and time.time() < deadline:
        time.sleep(0.1)
    assert executed == ["run"]
    UnisocScanRunner._reset_for_tests()


def test_execute_job_skips_mtk_runs_unisoc_when_mtk_missing(monkeypatch):
    """#1071: _execute_job 按各自 is_configured 分支，不硬依赖 MTK。"""
    from backend.agent.unisoc_scan_runner import UnisocScanRunner

    UnisocScanRunner._reset_for_tests()
    calls: list[str] = []

    class _Job:
        plan_run_id = 9
        host_id = "h"
        is_final = False
        device_serials = ()
        run_date_stamps = ()

    monkeypatch.setattr(
        ScanRunner.instance(),
        "run_scan_and_upload",
        lambda *a, **k: calls.append("mtk"),
    )
    _configure_unisoc_for_tests()
    monkeypatch.setattr(
        UnisocScanRunner.instance(),
        "run_scan_and_upload",
        lambda *a, **k: calls.append("unisoc"),
    )
    assert not ScanRunner.instance().is_configured()
    ScanRunner._execute_job(_Job())
    assert calls == ["unisoc"]
    UnisocScanRunner._reset_for_tests()


def test_configure_rejected_if_already_configured():
    r = _make_runner()
    first_python = r._scan_tool_python
    r.configure(scan_tool_python="/different/python")
    assert r._scan_tool_python == first_python


def test_build_argv_includes_end_flag():
    r = _make_runner()
    argv = r._build_argv(is_final=True)
    assert argv[-1] == "-end"
    assert "-m" in argv
    assert "-d" in argv
    assert "-side" in argv


def test_build_argv_without_end_flag():
    r = _make_runner()
    argv = r._build_argv(is_final=False)
    assert "-end" not in argv
    assert "-m" in argv


def test_host_scan_semaphore_limits_concurrency():
    _make_runner()
    assert ScanRunner.try_begin_host_scan() is True
    assert ScanRunner.try_begin_host_scan() is False
    ScanRunner.end_host_scan()
    assert ScanRunner.try_begin_host_scan() is True
    ScanRunner.end_host_scan()


def test_enqueue_coalesces_same_plan_run():
    _make_runner()
    with patch.object(ScanRunner, "_ensure_worker"):
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=False)
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=False)
        ScanRunner.enqueue_scan_now(
            55, "host-1", is_final=True,
            device_serials=["0000NX2622000670"],
            run_date_stamps=["0808"],
        )
        assert ScanRunner.pending_count() == 1
        job = ScanRunner._dequeue_next()
        assert job is not None
        assert job.plan_run_id == 55
        assert job.is_final is True
        assert job.device_serials == ("0000NX2622000670",)
        assert job.run_date_stamps == ("0808",)


def test_enqueue_fifo_preserves_distinct_plan_runs():
    _make_runner()
    with patch.object(ScanRunner, "_ensure_worker"):
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=False)
        ScanRunner.enqueue_scan_now(56, "host-1", is_final=True)
        assert ScanRunner.pending_count() == 2
        first = ScanRunner._dequeue_next()
        second = ScanRunner._dequeue_next()
        assert first is not None and first.plan_run_id == 55
        assert second is not None and second.plan_run_id == 56


def test_worker_runs_queued_job_after_active_scan():
    _make_runner()
    executed: list[tuple[int, bool]] = []
    gate = threading.Event()

    def slow_scan(
        self, plan_run_id: int, host_id: str, *, is_final: bool = False, **_kwargs,
    ):
        executed.append((plan_run_id, is_final))
        if len(executed) == 1:
            gate.wait(timeout=2)

    with patch.object(ScanRunner, "run_scan_and_upload", slow_scan):
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=False)
        deadline = time.time() + 1
        while time.time() < deadline and not executed:
            time.sleep(0.02)
        ScanRunner.enqueue_scan_now(55, "host-1", is_final=True)
        assert ScanRunner.pending_count() == 1
        gate.set()
        deadline = time.time() + 3
        while time.time() < deadline and len(executed) < 2:
            time.sleep(0.05)
    assert executed == [(55, False), (55, True)]


def test_run_local_scan_returns_none_when_no_fresh_xls(tmp_path):
    r = _make_runner()
    hdd = tmp_path / "hdd"
    hdd.mkdir()
    old_xls = hdd / "Result_shanghai_org.xls"
    old_xls.write_text("old")
    import os
    old_time = os.stat(old_xls).st_mtime - 100
    os.utime(str(old_xls), (old_time, old_time))
    r._hdd_root = str(hdd)

    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=0, stdout="done")
        result = r.run_local_scan(1, "host-1")

    assert result is None


def test_configure_force_overrides_existing():
    r = _make_runner()
    assert r._scan_tool_python == "/usr/bin/python3"
    r.configure(scan_tool_python="/new/python", scan_tool_script="/new/scan.py", force=True)
    assert r._scan_tool_python == "/new/python"
    assert r._scan_tool_script == "/new/scan.py"


def test_run_dedup_org_calls_dedup_org(tmp_path):
    r = _make_runner()
    org_xls = tmp_path / "Result_test_org.xls"
    org_xls.write_text("fake")
    dedup_xls = tmp_path / "Result_test_org_dedup_org_20260624_000000.xls"
    dedup_xls.write_text("deduped")

    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=0, stdout=str(dedup_xls))
        result = r.run_dedup_org(str(org_xls), 42, "host-1")

    assert result is not None
    assert "dedup_org" in result
    called_argv = mock_run.call_args[0][0]
    assert "-dedup_org" in called_argv
    assert str(org_xls) in called_argv
    assert "-side" in called_argv


def test_run_local_scan_scopes_to_plan_run_serials_and_stamp(tmp_path):
    r = _make_runner()
    hdd = tmp_path / "hdd"
    keep = (
        hdd / "V551A_0808_MonkeyAEEinfo" / "0000NX2622000670"
        / "2026_0808_010203_001_db.00.ANR"
    )
    other_dev = (
        hdd / "V551A_0808_MonkeyAEEinfo" / "0000NX2622000662"
        / "2026_0808_010203_002_db.00.ANR"
    )
    old_day = (
        hdd / "V551A_0731_MonkeyAEEinfo" / "0000NX2622000670"
        / "2026_0731_010203_001_db.00.ANR"
    )
    keep.mkdir(parents=True)
    other_dev.mkdir(parents=True)
    old_day.mkdir(parents=True)
    (keep / "ZZ_INTERNAL").write_text("keep")
    (other_dev / "ZZ_INTERNAL").write_text("other")
    (old_day / "ZZ_INTERNAL").write_text("old")
    r._hdd_root = str(hdd)

    def fake_run(argv, **_kwargs):
        scan_d = Path(argv[argv.index("-d") + 1])
        (scan_d / "Result_shanghai_org.xls").write_text("fake")
        return _completed(stdout="done")

    with patch("backend.agent.scan_runner.subprocess.run", side_effect=fake_run) as mock_run:
        result = r.run_local_scan(
            42,
            "host-1",
            device_serials=["0000NX2622000670"],
            run_date_stamps=["0808"],
        )

    assert result is not None
    called_argv = mock_run.call_args[0][0]
    staging = Path(called_argv[called_argv.index("-d") + 1])
    assert staging.parent == hdd / ".stp-scan"
    assert staging.name.startswith("pr42-")
    assert (staging / keep.relative_to(hdd) / "ZZ_INTERNAL").is_file()
    assert not (staging / "V551A_0808_MonkeyAEEinfo" / "0000NX2622000662").exists()
    assert not (staging / "V551A_0731_MonkeyAEEinfo").exists()


def test_run_local_scan_fails_closed_on_unsafe_serials(tmp_path):
    r = _make_runner()
    r._hdd_root = str(tmp_path / "hdd")
    Path(r._hdd_root).mkdir()
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        result = r.run_local_scan(
            42, "host-1", device_serials=["../etc", "/abs"],
        )
    assert result is None
    mock_run.assert_not_called()


def test_run_dedup_org_tool_failure():
    r = _make_runner()
    with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
        mock_run.return_value = _completed(returncode=1, stderr="error")
        result = r.run_dedup_org("/fake/path.xls", 1, "host-1")
    assert result is None


# ── #1078：staging 回收（硬链接终态后不清理，削弱 HDD spill）────────────


def _seed_event_file(hdd: Path, serial: str = "SER-A") -> Path:
    """在 HDD 上造一个事件文件，返回原文件路径（供 nlink 断言）。"""
    event = hdd / "V551A_0808_MonkeyAEEinfo" / serial / "2026_0808_010203_001_db.00.ANR"
    event.parent.mkdir(parents=True)
    event.write_text("event")
    return event


def _scan_with_fake_tool(r: ScanRunner, plan_run_id: int = 42, **kwargs):
    """跑一次带假 scan 工具的扫描，返回 (result, staging)。"""

    def fake_run(argv, **_kwargs):
        scan_d = Path(argv[argv.index("-d") + 1])
        (scan_d / "Result_shanghai_org.xls").write_text("fake")
        return _completed(stdout="done")

    with patch(
        "backend.agent.scan_runner.subprocess.run", side_effect=fake_run,
    ) as mock_run:
        result = r.run_local_scan(plan_run_id, "host-1", **kwargs)
    called_argv = mock_run.call_args[0][0]
    staging = Path(called_argv[called_argv.index("-d") + 1])
    return result, staging


class TestReclaimScanStaging:
    def test_refuses_hdd_root(self, tmp_path):
        """serials 为空时 scan_root=HDD 根 —— 回收必须拒绝动手（防误删）。"""
        from backend.agent.scan_runner import reclaim_scan_staging

        reclaim_scan_staging(str(tmp_path))
        assert tmp_path.exists()

    def test_refuses_non_staging_parent(self, tmp_path):
        from backend.agent.scan_runner import reclaim_scan_staging

        decoy = tmp_path / "other" / "pr42-abc"
        decoy.mkdir(parents=True)
        reclaim_scan_staging(str(decoy))
        assert decoy.exists()

    def test_removes_staging_and_releases_hardlink(self, tmp_path):
        """删 staging 后原事件目录的硬链接引用确实释放（nlink 回落）。"""
        import os

        from backend.agent.scan_runner import reclaim_scan_staging

        hdd = tmp_path / "hdd"
        hdd.mkdir()
        event = _seed_event_file(hdd)
        staging = hdd / ".stp-scan" / "pr42-abc"
        staging.mkdir(parents=True)
        linked = staging / "linked.ANR"
        os.link(event, linked)
        assert os.stat(event).st_nlink == 2

        reclaim_scan_staging(str(staging))

        assert not staging.exists()
        assert event.exists()
        assert os.stat(event).st_nlink == 1

    def test_failure_path_reclaims_staging(self, tmp_path):
        """scan 工具失败 → staging 立即回收，不等到下一轮 prepare。"""
        r = _make_runner()
        hdd = tmp_path / "hdd"
        hdd.mkdir()
        _seed_event_file(hdd)
        r._hdd_root = str(hdd)

        with patch("backend.agent.scan_runner.subprocess.run") as mock_run:
            mock_run.return_value = _completed(returncode=1, stderr="error")
            result = r.run_local_scan(
                42, "host-1",
                device_serials=["SER-A"], run_date_stamps=["0808"],
            )

        assert result is None
        assert list((hdd / ".stp-scan").iterdir()) == []

    def test_success_path_keeps_staging_for_upload(self, tmp_path):
        """成功路径产物在 staging 里 —— 上传前不能删。"""
        r = _make_runner()
        hdd = tmp_path / "hdd"
        hdd.mkdir()
        _seed_event_file(hdd)
        r._hdd_root = str(hdd)

        result, staging = _scan_with_fake_tool(
            r, device_serials=["SER-A"], run_date_stamps=["0808"],
        )

        assert result is not None
        assert staging.exists()
        assert r._last_scan_root == str(staging)

    def test_run_scan_and_upload_reclaims_after_upload(self, tmp_path):
        """端到端：上传完成后 staging 被回收，prune 原事件目录可释放空间。"""
        r = _make_runner()
        hdd = tmp_path / "hdd"
        hdd.mkdir()
        event = _seed_event_file(hdd)
        r._hdd_root = str(hdd)

        uploader = MagicMock()
        uploader.is_configured.return_value = True
        uploader.upload_scan_report.return_value = "/nfs/dst.xls"

        with patch(
            "backend.agent.scan_runner.subprocess.run",
            side_effect=lambda argv, **_k: (
                (_ := Path(argv[argv.index("-d") + 1]) / "Result_shanghai_org.xls")
                .write_text("fake"),
                _completed(stdout="done"),
            )[1],
        ):
            with patch(
                "backend.agent.upload_manager.UploadManager.instance",
                return_value=uploader,
            ):
                r.run_scan_and_upload(
                    42, "host-1", is_final=False,
                    device_serials=["SER-A"], run_date_stamps=["0808"],
                )

        assert uploader.upload_scan_report.called
        assert not (hdd / ".stp-scan" / "pr42-" ).exists() or all(
            not p.name.startswith("pr42-")
            for p in (hdd / ".stp-scan").iterdir()
        )
        import os
        assert os.stat(event).st_nlink == 1

    def test_run_scan_and_upload_reclaims_when_upload_raises(self, tmp_path):
        """#1277: upload 抛异常也必须回收 staging——否则占盘到该 plan_run 下一轮
        scan 的 prepare 才释放（长时不重跑即持续占盘）。"""
        r = _make_runner()
        hdd = tmp_path / "hdd"
        hdd.mkdir()
        event = _seed_event_file(hdd)
        r._hdd_root = str(hdd)

        uploader = MagicMock()
        uploader.is_configured.return_value = True
        uploader.upload_scan_report.side_effect = RuntimeError("nfs down")

        with patch(
            "backend.agent.scan_runner.subprocess.run",
            side_effect=lambda argv, **_k: (
                (_ := Path(argv[argv.index("-d") + 1]) / "Result_shanghai_org.xls")
                .write_text("fake"),
                _completed(stdout="done"),
            )[1],
        ):
            with patch(
                "backend.agent.upload_manager.UploadManager.instance",
                return_value=uploader,
            ):
                with pytest.raises(RuntimeError, match="nfs down"):
                    r.run_scan_and_upload(
                        42, "host-1", is_final=False,
                        device_serials=["SER-A"], run_date_stamps=["0808"],
                    )

        assert uploader.upload_scan_report.called
        assert all(
            not p.name.startswith("pr42-")
            for p in (hdd / ".stp-scan").iterdir()
        )
        import os
        assert os.stat(event).st_nlink == 1
