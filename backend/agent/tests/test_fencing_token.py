"""ADR-0019 Phase 2b Agent 级 fencing_token 贯穿测试。

5 个测试，覆盖 token 流转、LeaseRenewer skip、pipeline lock verify、
complete_run 透传、run_task_wrapper 入口校验。
"""

import sys
import threading
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from backend.agent.api_client import complete_run, _build_complete_payload
from backend.agent.job_runner import JobRunnerState, run_task_wrapper
from backend.agent.lease_renewer import LeaseRenewer
from backend.agent.pipeline_engine import PipelineEngine
from backend.agent.registry.local_db import LocalDB
from backend.agent.step_trace_uploader import _to_payload


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def lease_renewer():
    lock = threading.Lock()
    ids = {1, 2}
    stop = threading.Event()
    mgr = LeaseRenewer(
        api_url="http://127.0.0.1:8000",
        active_jobs_lock=lock,
        active_job_ids=ids,
        lock_renewal_stop_event=stop,
    )
    return mgr


@pytest.fixture
def job_runner_state():
    lock = threading.Lock()
    ids = set()
    device_ids = set()

    def register(jid: int, token: str = "", device_id: Optional[int] = None):
        with lock:
            ids.add(jid)

    def deregister(jid: int):
        with lock:
            ids.discard(jid)

    def device_register(did: int):
        with lock:
            device_ids.add(did)

    def device_deregister(did: int):
        with lock:
            device_ids.discard(did)

    return JobRunnerState(
        active_jobs_lock=lock,
        active_job_ids=ids,
        active_device_ids=device_ids,
        watcher_enabled=False,
        lock_register=register,
        lock_deregister=deregister,
        device_id_register=device_register,
        device_id_deregister=device_deregister,
    )


# ── Test 16: claimed job dict fencing_token 强协议 ────────────────────────────

def test_run_task_wrapper_missing_fencing_token_raises_key_error(job_runner_state):
    """缺 fencing_token 时 run_task_wrapper 直接 KeyError，不静默兜底。"""
    run = {
        "id": 123,
        "device_id": 1,
        "device_serial": "S1",
        "pipeline_def": {
            "lifecycle": {
                "init": [{"step_id": "x", "action": "script:noop", "version": "1.0.0", "timeout_seconds": 1}],
                "teardown": [],
            }
        },
        # 故意不写 fencing_token
    }
    mock_adb = MagicMock()
    with pytest.raises(KeyError, match="fencing_token"):
        run_task_wrapper(
            run, mock_adb, "http://x", "h1",
            job_runner_state, None, None, None,
        )


# ── Test 17: run_task_wrapper heartbeat + complete 带 token ──────────────────

def test_run_task_wrapper_heartbeat_and_complete_include_token(job_runner_state):
    """心跳 payload 和 complete_job 调用均显式传递 fencing_token。"""
    run = {
        "id": 99,
        "device_id": 2,
        "device_serial": "SN-99",
        "fencing_token": "2:3",
        "pipeline_def": {
            "lifecycle": {
                "init": [{"step_id": "x", "action": "script:noop", "version": "1.0.0", "timeout_seconds": 1}],
                "teardown": [],
            }
        },
    }
    mock_adb = MagicMock()

    with patch("backend.agent.job_runner.update_job") as mock_update, \
         patch("backend.agent.job_runner.complete_job") as mock_complete, \
         patch("backend.agent.job_runner.execute_pipeline_run") as mock_exec:
        mock_exec.return_value = {"status": "FINISHED", "exit_code": 0}

        run_task_wrapper(
            run, mock_adb, "http://x", "h1",
            job_runner_state, None, None, None,
        )

    # 心跳 payload 含 fencing_token
    mock_update.assert_called_once()
    assert mock_update.call_args[0][2]["fencing_token"] == "2:3", (
        "Heartbeat payload must include fencing_token"
    )

    # complete_job 传 fencing_token
    mock_complete.assert_called_once()
    assert mock_complete.call_args.kwargs["fencing_token"] == "2:3", (
        "complete_job must receive fencing_token"
    )


# ── Test 18: LeaseRenewer skip when no token ───────────────────────────

def test_lease_renewer_skips_extend_when_no_token(lease_renewer):
    """无 fencing_token 的 job → extend_lock 直接 skip，不发 HTTP。"""
    with patch("requests.post") as mock_post:
        lease_renewer._extend_lock(999)  # 999 has no token registered

    mock_post.assert_not_called()


def test_lease_renewer_sends_token_when_present(lease_renewer):
    """有 fencing_token → extend_lock POST body 包含 {"fencing_token": token}。"""
    lease_renewer.set_fencing_token(1, "test:token:1")

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        lease_renewer._extend_lock(1)

    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["json"] == {"fencing_token": "test:token:1"}


# ── Test 19: PipelineEngine._verify_device_lease sends fencing_token ──────────

def test_pipeline_engine_verify_device_lease_sends_fencing_token():
    """lease verify 阶段 POST extend_lock 携带 json={"fencing_token": token}。"""
    mock_adb = MagicMock()
    engine = PipelineEngine(
        adb=mock_adb,
        serial="S1",
        run_id=42,
        api_url="http://127.0.0.1:8000",
        fencing_token="42:7",
    )

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        result = engine._verify_device_lease()

    assert result is None, "Valid lease should return None (verified)"
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["json"] == {"fencing_token": "42:7"}, (
        "_verify_device_lease must POST fencing_token in body"
    )


# ── Test 20: complete_run / _build_complete_payload 透传 fencing_token ───────

def test_build_complete_payload_includes_fencing_token():
    """_build_complete_payload 将 fencing_token 写入 /complete 请求体。"""
    payload = _build_complete_payload(
        {"status": "FINISHED", "exit_code": 0},
        fencing_token="8:1",
    )
    assert payload["fencing_token"] == "8:1"
    assert payload["update"]["status"] == "FINISHED"


def test_complete_run_passes_token_into_complete_payload():
    """complete_run 别名将 fencing_token 完整传递到 POST body。"""
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        complete_run(
            "http://127.0.0.1:8000",
            88,
            {
                "status": "FINISHED",
                "exit_code": 0,
                "artifact": {"storage_uri": "file:///tmp/x.tar.gz"},
            },
            fencing_token="88:2",
        )

    called_payload = mock_post.call_args.kwargs["json"]
    assert called_payload["fencing_token"] == "88:2"
    assert called_payload["artifact"] == {"storage_uri": "file:///tmp/x.tar.gz"}


# ── Test 21: StepTrace 本地缓存与上传携带 fencing_token ─────────────────────

def test_local_step_trace_cache_persists_fencing_token(tmp_path):
    """StepTrace 写入本地 SQLite 时必须持久化 fencing_token。"""
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    try:
        db.save_step_trace(
            job_id=90,
            step_id="init",
            stage="init",
            event_type="COMPLETED",
            status="COMPLETED",
            fencing_token="90:1",
        )

        traces = db.get_unacked_traces()
    finally:
        db.close()

    assert traces[0]["fencing_token"] == "90:1"


def test_local_step_trace_cache_backfills_missing_token_from_active_job(tmp_path):
    """升级前的未 ack StepTrace 缺 token 时，从 active_job_registry 回填。"""
    db_path = tmp_path / "agent.db"

    db = LocalDB()
    db.initialize(str(db_path))
    try:
        db.save_active_job(job_id=90, device_id=1, fencing_token="90:1")
        db.save_step_trace(
            job_id=90,
            step_id="legacy",
            stage="init",
            event_type="COMPLETED",
            status="COMPLETED",
        )
    finally:
        db.close()

    reopened = LocalDB()
    reopened.initialize(str(db_path))
    try:
        traces = reopened.get_unacked_traces()
    finally:
        reopened.close()

    assert traces[0]["fencing_token"] == "90:1"


def test_step_trace_uploader_payload_includes_fencing_token():
    """StepTraceUploader 发往 /steps 的 payload 必须包含 fencing_token。"""
    payload = _to_payload({
        "job_id": 91,
        "step_id": "execute",
        "stage": "execute",
        "event_type": "FAILED",
        "status": "FAILED",
        "output": None,
        "error_message": "boom",
        "original_ts": "2026-05-06T00:00:00+00:00",
        "fencing_token": "91:2",
    })

    assert payload["fencing_token"] == "91:2"


def test_pipeline_engine_step_trace_mq_includes_fencing_token():
    """PipelineEngine 上报 StepTrace 时必须把自身 fencing_token 传给 MQProducer。"""
    mq = MagicMock()
    mq.connected = True
    engine = PipelineEngine(
        adb=MagicMock(),
        serial="S1",
        run_id=92,
        mq_producer=mq,
        fencing_token="92:3",
    )

    engine._report_step_trace_mq(
        step_id="init",
        stage="init",
        event_type="STARTED",
        status="RUNNING",
    )

    assert mq.send_step_trace.call_args.kwargs["fencing_token"] == "92:3"
