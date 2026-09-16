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


def test_multi_instance_merge_warning_reflects_adapter_state(monkeypatch):
    """#2189 / ADR-0027 v1.8 清单第 7 条：多实例形态下 merge 实例绑定必须可见。

    单实例（adapter 关）→ 无告警，保证默认形态零噪音；多实例 → 告警含 ``merge_instance_bound``
    与 ``ref=#2189``，使部署方能从启动日志就知道 merge 没有跨实例互斥。
    """
    monkeypatch.setattr(
        "backend.realtime.socketio_redis.socketio_redis_adapter_enabled", lambda: False
    )
    assert ds.multi_instance_merge_warning() is None

    monkeypatch.setattr(
        "backend.realtime.socketio_redis.socketio_redis_adapter_enabled", lambda: True
    )
    warning = ds.multi_instance_merge_warning()
    assert warning is not None
    assert "merge_instance_bound=true" in warning
    assert "ref=#2189" in warning


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


def test_find_fresh_picks_newest_when_two_new_dirs(tmp_path):
    """#1072 根因：共享 merge_result 下两轮新目录并存时 max(mtime) 会错绑。"""
    import time

    merge_root = tmp_path / "merge_result"
    merge_root.mkdir()
    before: set[str] = set()
    baseline = 0.0
    a = merge_root / "out_a"
    a.mkdir()
    (a / "Result_MergeFiles.xls").write_bytes(b"A")
    time.sleep(0.02)
    b = merge_root / "out_b"
    b.mkdir()
    (b / "Result_MergeFiles.xls").write_bytes(b"B")
    found = ds.find_fresh_merge_output_dir(merge_root, baseline, before_names=before)
    assert found == b


def test_exclusive_merge_lock_serializes_harvest(tmp_path):
    """#1072：持锁 snapshot→落盘→收割时，两 PlanRun 各自拿到自己的产物。"""
    import threading
    import time

    merge_root = tmp_path / "merge_result"
    merge_root.mkdir()
    results: list[tuple[str, bytes]] = []
    errors: list[BaseException] = []

    def worker(tag: bytes) -> None:
        try:
            with ds._exclusive_merge_tool_lock(tmp_path):
                before = ds._merge_output_dir_names(merge_root)
                baseline = ds.latest_merge_output_mtime(merge_root)
                time.sleep(0.05)  # 放大交错窗口；无锁时必交叉
                sub = merge_root / f"out_{tag.decode()}"
                sub.mkdir()
                (sub / "Result_MergeFiles.xls").write_bytes(tag)
                found = ds.find_fresh_merge_output_dir(merge_root, baseline, before)
                results.append((found.name, found.joinpath("Result_MergeFiles.xls").read_bytes()))
        except BaseException as exc:  # noqa: BLE001 — 收集线程错误
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(b"runA",)),
        threading.Thread(target=worker, args=(b"runB",)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors
    assert sorted(c for _, c in results) == [b"runA", b"runB"]
    assert {name for name, _ in results} == {"out_runA", "out_runB"}


def test_run_merge_sync_holds_merge_lock_during_harvest(tmp_path, monkeypatch):
    """run_merge_sync 在收割窗口内持有 .stp_merge.lock。"""
    import fcntl

    (tmp_path / "start_log_scan.py").write_text("# stub", encoding="utf-8")
    tool = {"python": "python", "script": str(tmp_path / "start_log_scan.py")}
    merge_root = tmp_path / "merge_result"
    lock_held = {"ok": False}

    def fake_run(*_a, **_k):
        lock_path = merge_root / ".stp_merge.lock"
        assert lock_path.is_file()
        with open(lock_path, "a+", encoding="utf-8") as fh:
            # 非阻塞抢锁应失败 → 证明 run_merge_sync 仍持 LOCK_EX
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock_held["ok"] = True
            else:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                lock_held["ok"] = False
        out = merge_root / "2026_09_08_18_00_00"
        out.mkdir(parents=True, exist_ok=True)
        (out / "Result_MergeFiles.xls").write_bytes(b"x")
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
    with patch.object(ds, "resolve_scan_tool", return_value=tool), \
         patch.object(ds, "_load_org_files_for_merge", return_value=["/fake/a_org.xls"]), \
         patch.object(ds, "build_merge_argv", return_value=(["python", "scan.py", "-merge_files_list", "x"], None)), \
         patch.object(ds, "scan_tool_supports_merge_files_list", return_value=True), \
         patch("backend.services.dedup_scan.subprocess.run", side_effect=fake_run), \
         patch.object(ds, "_register_merge_artifacts", return_value=1):
        assert ds.run_merge_sync(42) == "ok"
    assert lock_held["ok"] is True


def test_run_merge_sync_raises_on_center_publish_oserror(tmp_path, monkeypatch):
    """#1074: 中心发布 OSError 时不登记本机产物、不返回 ok。"""
    (tmp_path / "start_log_scan.py").write_text("# stub", encoding="utf-8")
    tool = {"python": "python", "script": str(tmp_path / "start_log_scan.py")}
    merge_root = tmp_path / "merge_result"
    center = tmp_path / "center"
    center.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(center))

    def fake_run(*_a, **_k):
        out = merge_root / "2026_09_08_20_00_00"
        out.mkdir(parents=True, exist_ok=True)
        (out / "Result_MergeFiles.xls").write_bytes(b"x")
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    register = MagicMock(return_value=1)
    with patch.object(ds, "resolve_scan_tool", return_value=tool), \
         patch.object(ds, "_load_org_files_for_merge", return_value=["/fake/a_org.xls"]), \
         patch.object(ds, "build_merge_argv", return_value=(["python", "scan.py", "-merge_files_list", "x"], None)), \
         patch.object(ds, "scan_tool_supports_merge_files_list", return_value=True), \
         patch("backend.services.dedup_scan.subprocess.run", side_effect=fake_run), \
         patch("backend.services.dedup_scan.shutil.copytree", side_effect=OSError("ENOSPC")), \
         patch.object(ds, "_register_merge_artifacts", register):
        with pytest.raises(RuntimeError, match="merge center publish failed"):
            ds.run_merge_sync(42)
    register.assert_not_called()
    # I-13 方案 A：发布失败**不得**删本机中转——它既是重试依据也是现场诊断对象。
    assert (merge_root / "2026_09_08_20_00_00" / "Result_MergeFiles.xls").is_file()


def test_run_merge_sync_removes_local_intermediate_after_publish(tmp_path, monkeypatch):
    """I-13 方案 A：发布 + 登记完成后，本机 `merge_result/{ts}/` 不再保留。

    判据 E-3：控制面本地 `merge_result/` 稳态为 0——登记指向的必须是**中心**路径，
    而本机中转副本（工具唯一能写的位置）事后删除。
    """
    from pathlib import Path

    (tmp_path / "start_log_scan.py").write_text("# stub", encoding="utf-8")
    tool = {"python": "python", "script": str(tmp_path / "start_log_scan.py")}
    merge_root = tmp_path / "merge_result"
    center = tmp_path / "center"
    center.mkdir()
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(center))
    out = merge_root / "2026_09_15_10_00_00"

    def fake_run(*_a, **_k):
        out.mkdir(parents=True, exist_ok=True)
        (out / "Result_MergeFiles.xls").write_bytes(b"x")
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    registered: list[Path] = []

    def _register(_db, _run_id, merge_dir):
        registered.append(Path(merge_dir))
        return 1

    with patch.object(ds, "resolve_scan_tool", return_value=tool), \
         patch.object(ds, "_load_org_files_for_merge", return_value=["/fake/a_org.xls"]), \
         patch.object(ds, "build_merge_argv", return_value=(["python", "scan.py", "-merge_files_list", "x"], None)), \
         patch.object(ds, "scan_tool_supports_merge_files_list", return_value=True), \
         patch("backend.services.dedup_scan.subprocess.run", side_effect=fake_run), \
         patch.object(ds, "_register_merge_artifacts", side_effect=_register):
        assert ds.run_merge_sync(42) == "ok"

    assert registered and registered[0] == center / "dedup" / "42" / "merge"
    assert (center / "dedup" / "42" / "merge" / "Result_MergeFiles.xls").is_file()
    assert not out.exists()
    assert ds._merge_output_dir_names(merge_root) == set()


def test_run_merge_sync_keeps_local_when_center_unconfigured(tmp_path, monkeypatch):
    """中心未配置时本机目录**就是交付物**（artifact 指向它）——不得删。"""
    from pathlib import Path

    (tmp_path / "start_log_scan.py").write_text("# stub", encoding="utf-8")
    tool = {"python": "python", "script": str(tmp_path / "start_log_scan.py")}
    merge_root = tmp_path / "merge_result"
    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)
    out = merge_root / "2026_09_15_10_00_01"

    def fake_run(*_a, **_k):
        out.mkdir(parents=True, exist_ok=True)
        (out / "Result_MergeFiles.xls").write_bytes(b"x")
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        proc.stdout = ""
        return proc

    registered: list[Path] = []

    with patch.object(ds, "resolve_scan_tool", return_value=tool), \
         patch.object(ds, "_load_org_files_for_merge", return_value=["/fake/a_org.xls"]), \
         patch.object(ds, "build_merge_argv", return_value=(["python", "scan.py", "-merge_files_list", "x"], None)), \
         patch.object(ds, "scan_tool_supports_merge_files_list", return_value=True), \
         patch("backend.services.dedup_scan.subprocess.run", side_effect=fake_run), \
         patch.object(ds, "_register_merge_artifacts",
                      side_effect=lambda _db, _run_id, merge_dir: registered.append(Path(merge_dir)) or 1):
        assert ds.run_merge_sync(42) == "ok"

    assert registered and registered[0] == out
    assert (out / "Result_MergeFiles.xls").is_file()


def test_sweep_stale_local_merge_outputs_bounded(tmp_path):
    """超期残留清理只动"像产物目录"且超期的那些：锁文件、新鲜目录、非产物目录都不碰。"""
    import os
    import time

    merge_root = tmp_path / "merge_result"
    merge_root.mkdir()
    stale = merge_root / "old"
    stale.mkdir()
    (stale / "Result_MergeFiles.xls").write_bytes(b"x")
    fresh = merge_root / "new"
    fresh.mkdir()
    (fresh / "Result_MergeFiles.xls").write_bytes(b"y")
    not_a_product = merge_root / "notes"
    not_a_product.mkdir()
    (not_a_product / "readme.txt").write_text("keep me", encoding="utf-8")
    (merge_root / ".stp_merge.lock").write_text("", encoding="utf-8")

    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))

    assert ds.sweep_stale_local_merge_outputs(merge_root) == 1
    assert not stale.exists()
    assert (fresh / "Result_MergeFiles.xls").is_file()
    assert not_a_product.is_dir()
    assert (merge_root / ".stp_merge.lock").is_file()


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
        assert ds.run_merge_sync(sample_plan_run.id) == "skipped_failed"
    # P1：跳过路径计数（failed_plan_run 是预期门禁，计数不告警）
    failed_counter.inc.assert_called_once_with()


def test_run_merge_sync_allow_failed_does_not_skip(db_session, sample_plan_run, monkeypatch):
    """#697: 手动 allow_failed=True 时 FAILED 不再短路。"""
    from backend.models.enums import PlanRunStatus

    sample_plan_run.status = PlanRunStatus.FAILED.value
    db_session.commit()

    with patch.object(ds, "resolve_scan_tool", return_value=None):
        # 越过 FAILED 门禁后，缺工具仍返回空串（非 skipped_failed）
        assert ds.run_merge_sync(sample_plan_run.id, allow_failed=True) == ""


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


def test_run_merge_all_platforms_records_per_platform_outcomes(
    db_session, sample_plan_run, monkeypatch,
):
    """多平台路由可观测：逐平台结果落 ``run_context.merge_platforms``。

    ADR-0032 B1 的「分区各自产出」此前只能靠查中心目录反推。工具/校验/发布**真失败**
    走 raise 而不是空串（见 ``test_run_merge_sync_raises_*``），本函数只记录正常返回的
    三种状态。
    """
    from backend.models.plan_run import PlanRun

    by_platform = {"mtk": "ok", "unisoc": ""}
    monkeypatch.setattr(
        ds, "run_merge_sync",
        lambda _run_id, *, platform=None, **_kw: by_platform[str(platform)],
    )

    assert ds.run_merge_all_platforms_sync(sample_plan_run.id) == "ok"

    db_session.expire_all()
    run = db_session.get(PlanRun, sample_plan_run.id)
    assert run.run_context["merge_platforms"]["platforms"] == {
        "mtk": "ok",
        "unisoc": "no_input",
    }


def test_run_merge_all_platforms_records_skipped_failed(
    db_session, sample_plan_run, monkeypatch,
):
    """全平台 skipped_failed → 聚合 skipped_failed，逐平台状态原样保留。"""
    from backend.models.plan_run import PlanRun

    monkeypatch.setattr(
        ds, "run_merge_sync",
        lambda _run_id, *, platform=None, **_kw: "skipped_failed",
    )

    assert ds.run_merge_all_platforms_sync(sample_plan_run.id) == "skipped_failed"

    db_session.expire_all()
    run = db_session.get(PlanRun, sample_plan_run.id)
    assert run.run_context["merge_platforms"]["platforms"] == {
        "mtk": "skipped_failed",
        "unisoc": "skipped_failed",
    }


def test_scan_completeness_scopes_to_since_watermark(
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
    expected = {"host-a": {"mtk"}}
    stale_at = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc)
    fresh_at = datetime(2026, 8, 8, 7, 0, tzinfo=timezone.utc)
    watermark = datetime(2026, 8, 8, 6, 30, tzinfo=timezone.utc)

    db_session.add_all([
        PlanRunArtifact(
            plan_run_id=run_id,
            host_id="host-a",
            storage_uri="/tmp/stale_org.xls",
            artifact_type=ds.ARTIFACT_TYPE_SCAN,
            size_bytes=100,
            created_at=stale_at,
        ),
        PlanRunArtifact(
            plan_run_id=run_id,
            host_id="host-a",
            storage_uri="/tmp/fresh_org.xls",
            artifact_type=ds.ARTIFACT_TYPE_SCAN,
            size_bytes=100,
            created_at=fresh_at,
        ),
    ])
    db_session.commit()

    # Stale row alone does not count once the watermark is past it.
    got = ds.scan_completeness(run_id, expected, since=watermark)
    assert (got.units_satisfied, got.units_expected) == (1, 1)
    assert got.hosts_with_artifacts == 1
    assert ds.scan_completeness(run_id, expected, since=fresh_at).complete
    assert not ds.scan_completeness(
        run_id, expected, since=fresh_at + timedelta(seconds=1)
    ).complete

    # Only the stale row exists before watermark — this is the re-trigger case.
    db_session.query(PlanRunArtifact).filter(
        PlanRunArtifact.storage_uri == "/tmp/fresh_org.xls"
    ).delete()
    db_session.commit()
    assert not ds.scan_completeness(run_id, expected, since=watermark).complete


def _scan_artifact(run_id, host_id, uri, *, at):
    from backend.models.plan_run_artifact import PlanRunArtifact

    return PlanRunArtifact(
        plan_run_id=run_id,
        host_id=host_id,
        storage_uri=uri,
        artifact_type=ds.ARTIFACT_TYPE_SCAN,
        size_bytes=100,
        created_at=at,
    )


def test_scan_completeness_pure_mtk_host_needs_only_mtk(db_session, sample_plan_run):
    """ADR-0032 B1「分区各自完备性判定」：纯 MTK host 不得被要求产出 UNISOC 报表。

    回归护栏：``require_platforms=DEDUP_PLATFORMS`` 期间要求**每个 host** 双平台齐，
    纯 MTK host 因此永远判不齐、每轮烧满轮询预算并误报 partial。
    """
    from datetime import datetime, timezone

    run_id = sample_plan_run.id
    expected = {"host-a": {"mtk"}}
    since = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)

    assert not ds.scan_completeness(run_id, expected, since=since).complete

    db_session.add(_scan_artifact(
        run_id, "host-a", "/nfs/dedup/1/mtk/host-a_Result_org.xls", at=since,
    ))
    db_session.commit()
    got = ds.scan_completeness(run_id, expected, since=since)
    assert got.complete
    assert (got.units_satisfied, got.units_expected) == (1, 1)


def test_scan_completeness_pure_unisoc_host_needs_only_unisoc(
    db_session, sample_plan_run,
):
    """对称面：纯 UNISOC host 不得被要求产出 MTK 报表。"""
    from datetime import datetime, timezone

    run_id = sample_plan_run.id
    since = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    db_session.add(_scan_artifact(
        run_id, "host-u", "/nfs/dedup/1/unisoc/host-u_Result_org.xls", at=since,
    ))
    db_session.commit()
    assert ds.scan_completeness(run_id, {"host-u": {"unisoc"}}, since=since).complete


def test_scan_completeness_mixed_host_waits_for_both_platforms(
    db_session, sample_plan_run,
):
    """混平台 host 仍必须两平台都到齐——MTK 先到不得提前满足屏障（#1071 原意）。"""
    from datetime import datetime, timezone

    run_id = sample_plan_run.id
    expected = {"host-mixed": {"mtk", "unisoc"}}
    since = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)

    db_session.add(_scan_artifact(
        run_id, "host-mixed", "/nfs/dedup/1/mtk/host-mixed_Result_org.xls", at=since,
    ))
    db_session.commit()
    got = ds.scan_completeness(run_id, expected, since=since)
    assert (got.units_satisfied, got.units_expected) == (1, 2)
    assert not got.complete

    db_session.add(_scan_artifact(
        run_id, "host-mixed", "/nfs/dedup/1/unisoc/host-mixed_Result_org.xls", at=since,
    ))
    db_session.commit()
    got = ds.scan_completeness(run_id, expected, since=since)
    assert got.complete
    assert got.units_satisfied == 2


def test_scan_completeness_mixed_fleet_counts_units_per_host_platform(
    db_session, sample_plan_run,
):
    """三型 host 混跑（纯 MTK / 纯 UNISOC / 混平台）→ 4 个完备性单位。"""
    from datetime import datetime, timezone

    run_id = sample_plan_run.id
    expected = {
        "host-mtk": {"mtk"},
        "host-unisoc": {"unisoc"},
        "host-mixed": {"mtk", "unisoc"},
    }
    since = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)
    db_session.add_all([
        _scan_artifact(run_id, "host-mtk", "/nfs/dedup/1/mtk/a.xls", at=since),
        _scan_artifact(run_id, "host-unisoc", "/nfs/dedup/1/unisoc/b.xls", at=since),
        _scan_artifact(run_id, "host-mixed", "/nfs/dedup/1/mtk/c.xls", at=since),
    ])
    db_session.commit()

    got = ds.scan_completeness(run_id, expected, since=since)
    assert (got.units_satisfied, got.units_expected) == (3, 4)
    assert got.hosts_with_artifacts == 3
    assert not got.complete


def test_scan_completeness_empty_expectation_is_trivially_complete(
    db_session, sample_plan_run,
):
    """无采集实现的平台（如 QCOM）不产生期望 → 不为它等待、也不误报缺产物。"""
    from datetime import datetime, timezone

    got = ds.scan_completeness(
        sample_plan_run.id, {}, since=datetime(2026, 9, 15, tzinfo=timezone.utc),
    )
    assert got.complete
    assert (got.units_satisfied, got.units_expected, got.hosts_with_artifacts) == (0, 0, 0)


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

    def test_raises_when_center_copy_oserror(self, tmp_path, monkeypatch):
        """#1074: 中心已配置但 copy 失败不得静默 None。"""
        merge_dir = tmp_path / "merge_result" / "d_fail"
        merge_dir.mkdir(parents=True)
        (merge_dir / "Result_MergeFiles.xls").write_text("x", encoding="utf-8")
        center = tmp_path / "center"
        center.mkdir()
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(center))

        with patch("backend.services.dedup_scan.shutil.copytree", side_effect=OSError("disk full")):
            with pytest.raises(RuntimeError, match="merge center publish failed"):
                ds._publish_merge_to_center(270, merge_dir)

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


def test_resolve_manual_merge_round_prefers_latest_stamped(db_session, sample_plan_run):
    from datetime import datetime, timezone

    from backend.models.plan_run_artifact import PlanRunArtifact

    older = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    db_session.add_all([
        PlanRunArtifact(
            plan_run_id=sample_plan_run.id,
            host_id="a",
            storage_uri="/tmp/a_org.xls",
            artifact_type=ds.ARTIFACT_TYPE_SCAN,
            size_bytes=1,
            scan_round_id="2026-09-01T10:00:00+00:00",
            created_at=older,
        ),
        PlanRunArtifact(
            plan_run_id=sample_plan_run.id,
            host_id="b",
            storage_uri="/tmp/b_org.xls",
            artifact_type=ds.ARTIFACT_TYPE_SCAN,
            size_bytes=1,
            scan_round_id="2026-09-08T12:00:00+00:00",
            created_at=newer,
        ),
    ])
    db_session.commit()

    rid, floor = ds.resolve_manual_merge_round(sample_plan_run.id)
    assert rid == "2026-09-08T12:00:00+00:00"
    assert floor == datetime.fromisoformat(rid)


def test_resolve_manual_merge_round_legacy_min_created_at(db_session, sample_plan_run):
    from datetime import datetime, timezone

    from backend.models.plan_run_artifact import PlanRunArtifact

    t0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    db_session.add_all([
        PlanRunArtifact(
            plan_run_id=sample_plan_run.id,
            host_id="a",
            storage_uri="/tmp/a_org.xls",
            artifact_type=ds.ARTIFACT_TYPE_SCAN,
            size_bytes=1,
            created_at=t1,
        ),
        PlanRunArtifact(
            plan_run_id=sample_plan_run.id,
            host_id="b",
            storage_uri="/tmp/b_org.xls",
            artifact_type=ds.ARTIFACT_TYPE_SCAN,
            size_bytes=1,
            created_at=t0,
        ),
    ])
    db_session.commit()

    rid, floor = ds.resolve_manual_merge_round(sample_plan_run.id)
    assert rid is None
    assert floor == t0


def test_merge_stderr_detects_error_prefix_and_traceback():
    """#798：行首 ``ERROR:`` 与 Traceback 形态同样表达失败（旧匹配仅
    ": error:"/"error: argument" 会漏判，exit 0 的残缺报表被当成功）。"""
    assert ds.merge_stderr_indicates_failure("ERROR: cannot open result file")
    assert ds.merge_stderr_indicates_failure(
        "Traceback (most recent call last):\n  File \"x\", line 1"
    )
    assert ds.merge_stderr_indicates_failure("Error: missing input")
    # 非失败形态不误报
    assert not ds.merge_stderr_indicates_failure("[INFO] merge done")
    assert not ds.merge_stderr_indicates_failure("wrote 3 rows")
