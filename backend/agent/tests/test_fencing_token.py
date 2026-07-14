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
import requests

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from backend.agent.api_client import complete_run, _build_complete_payload
from backend.agent.job_runner import JobRunnerState, run_task_wrapper
from backend.agent.lease_renewer import LeaseRenewer
from backend.agent.pipeline_engine import PipelineEngine
from backend.agent.registry.local_db import LocalDB
from backend.agent.step_trace_uploader import StepTraceUploader, _to_payload


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
    active_tokens = {}

    def register(
        jid: int,
        token: str = "",
        device_id: Optional[int] = None,
        device_serial: str = "",
        local_worker_token: str = "",
    ):
        with lock:
            ids.add(jid)
            active_tokens[jid] = local_worker_token or token
            if device_id is not None:
                device_ids.add(device_id)

    def deregister(jid: int, token: str = "", local_worker_token: str = ""):
        with lock:
            current = active_tokens.get(jid)
            expected_token = local_worker_token or token
            if current and expected_token and current != expected_token:
                return
            ids.discard(jid)
            active_tokens.pop(jid, None)

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
        active_job_tokens=active_tokens,
        running_worker_tokens={},
        watcher_globally_enabled=False,
        watcher_plan_default=False,
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


def test_job_runner_state_replacement_token_marks_old_worker_aborted(job_runner_state):
    """同一 job 被新 fencing_token 接管后，旧 worker 应视为已中止。"""
    job_runner_state.lock_register(26, "63:6", 63, "SERIAL-63")

    assert job_runner_state.try_mark_worker_started(26, "63:6") is True
    assert job_runner_state.is_aborted(26, "63:6") is False

    job_runner_state.lock_register(26, "63:7", 63, "SERIAL-63")

    assert job_runner_state.is_aborted(26, "63:6") is True
    assert job_runner_state.is_aborted(26, "63:7") is False
    assert job_runner_state.try_mark_worker_started(26, "63:7") is True

    job_runner_state.release(26, "63:6", 63)
    assert 26 in job_runner_state.active_job_ids
    assert job_runner_state.active_job_tokens[26] == "63:7"


def test_abort_request_keeps_worker_current_until_terminal_ack(job_runner_state):
    job_runner_state.lock_register(26, "63:6", 63, "SERIAL-63", "worker-1")
    runner = MagicMock()
    job_runner_state.attach_runner(26, "worker-1", runner)

    assert job_runner_state.request_abort(26) is True

    runner.cancel.assert_called_once()
    assert job_runner_state.is_aborted(26, "worker-1") is True
    assert job_runner_state.is_current_worker(26, "worker-1") is True
    assert 26 in job_runner_state.active_job_ids
    assert job_runner_state.active_job_tokens[26] == "worker-1"

    job_runner_state.release(
        26, "63:6", 63, local_worker_token="worker-1",
    )
    assert 26 not in job_runner_state.abort_requested_job_ids


def test_runner_attached_after_abort_request_is_cancelled(job_runner_state):
    job_runner_state.lock_register(26, "63:6", 63, "SERIAL-63", "worker-1")
    assert job_runner_state.request_abort(26) is True
    runner = MagicMock()

    job_runner_state.attach_runner(26, "worker-1", runner)

    runner.cancel.assert_called_once()
    assert 26 not in job_runner_state.active_runners


def test_job_runner_state_same_fencing_token_new_local_worker_replaces_old(job_runner_state):
    """同 fencing_token 的恢复 worker 也必须能接管本地执行权。"""
    job_runner_state.lock_register(26, "63:6", 63, "SERIAL-63", "worker-old")

    assert job_runner_state.try_mark_worker_started(26, "worker-old") is True
    assert job_runner_state.is_aborted(26, "worker-old") is False

    job_runner_state.lock_register(26, "63:6", 63, "SERIAL-63", "worker-new")

    assert job_runner_state.is_aborted(26, "worker-old") is True
    assert job_runner_state.is_aborted(26, "worker-new") is False
    assert job_runner_state.try_mark_worker_started(26, "worker-new") is True

    job_runner_state.release(26, "63:6", 63, local_worker_token="worker-old")
    assert 26 in job_runner_state.active_job_ids
    assert job_runner_state.active_job_tokens[26] == "worker-new"


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


def test_run_task_wrapper_skips_complete_when_local_worker_superseded(job_runner_state):
    """同 fencing_token 下旧 worker 被新本地 worker 接管后，不得再上报 /complete。"""
    run = {
        "id": 109,
        "device_id": 2,
        "device_serial": "SN-109",
        "fencing_token": "2:9",
        "local_worker_token": "worker-old",
        "pipeline_def": {
            "lifecycle": {
                "init": [{"step_id": "x", "action": "script:noop", "version": "1.0.0", "timeout_seconds": 1}],
                "teardown": [],
            }
        },
    }
    mock_adb = MagicMock()

    def supersede_before_complete(*_args, **_kwargs):
        job_runner_state.lock_register(109, "2:9", 2, "SN-109", "worker-new")
        return {"status": "ABORTED", "exit_code": 1, "error_code": "JOB_ABORTED"}

    with patch("backend.agent.job_runner.update_job"), \
         patch("backend.agent.job_runner.complete_job") as mock_complete, \
         patch("backend.agent.job_runner.execute_pipeline_run", side_effect=supersede_before_complete):
        run_task_wrapper(
            run, mock_adb, "http://x", "h1",
            job_runner_state, None, None, None,
        )

    mock_complete.assert_not_called()
    assert 109 in job_runner_state.active_job_ids
    assert job_runner_state.active_job_tokens[109] == "worker-new"


def test_run_task_wrapper_reports_aborted_after_local_abort_request(job_runner_state):
    run = {
        "id": 110,
        "device_id": 2,
        "device_serial": "SN-110",
        "fencing_token": "2:10",
        "local_worker_token": "worker-1",
        "pipeline_def": {
            "lifecycle": {
                "init": [{
                    "step_id": "x",
                    "action": "script:noop",
                    "version": "1.0.0",
                    "timeout_seconds": 1,
                }],
                "teardown": [],
            }
        },
    }

    def abort_before_complete(*_args, **_kwargs):
        assert job_runner_state.request_abort(110) is True
        return {
            "status": "ABORTED",
            "exit_code": 1,
            "error_code": "JOB_ABORTED",
        }

    with patch("backend.agent.job_runner.update_job"), \
         patch("backend.agent.job_runner.complete_job") as mock_complete, \
         patch(
             "backend.agent.job_runner.execute_pipeline_run",
             side_effect=abort_before_complete,
         ):
        run_task_wrapper(
            run, MagicMock(), "http://x", "h1",
            job_runner_state, None, None, None,
        )

    mock_complete.assert_called_once()
    assert mock_complete.call_args.args[2]["status"] == "ABORTED"


def test_run_task_wrapper_passes_session_watcher_capability(job_runner_state):
    """启用 watcher 时，run_task_wrapper 应把当前 capability 传给 patrol heartbeat 链路。"""
    run = {
        "id": 199,
        "device_id": 3,
        "device_serial": "SN-199",
        "fencing_token": "3:9",
        "pipeline_def": {
            "lifecycle": {
                "init": [{"step_id": "x", "action": "script:noop", "version": "1.0.0", "timeout_seconds": 1}],
                "patrol": {"interval_seconds": 60, "steps": []},
                "teardown": [],
            }
        },
    }
    mock_adb = MagicMock()
    session = MagicMock()
    session.summary.watcher_capability = "inotifyd_root"
    session.summary.to_complete_payload.return_value = {"watcher_capability": "inotifyd_root"}

    with patch("backend.agent.job_runner.update_job"), \
         patch("backend.agent.job_runner.complete_job"), \
         patch("backend.agent.job_runner.execute_pipeline_run") as mock_exec, \
         patch("backend.agent.job_runner._validate_pipeline_def", return_value=None), \
         patch("backend.agent.job_runner.job_wants_watcher", return_value=True), \
         patch("backend.agent.job_runner.JobSession", return_value=session):
        mock_exec.return_value = {"status": "FINISHED", "exit_code": 0}

        run_task_wrapper(
            run, mock_adb, "http://x", "h1",
            job_runner_state, None, None, None,
        )

    assert mock_exec.call_args.kwargs["watcher_capability"] == "inotifyd_root"


def test_run_task_wrapper_skips_watcher_when_policy_explicitly_disabled_even_if_global_on(
    job_runner_state,
):
    """host 被 admin 标记 inactive 后，claim 下发 enabled=false 时，即使 Agent 全局 watcher 打开，
    当前 job 也不得启动 JobSession。"""
    job_runner_state.watcher_globally_enabled = True
    job_runner_state.watcher_plan_default = True

    run = {
        "id": 299,
        "plan_id": 8,
        "device_id": 4,
        "device_serial": "SN-299",
        "fencing_token": "4:9",
        "watcher_policy": {"enabled": False},
        "pipeline_def": {
            "lifecycle": {
                "init": [{"step_id": "x", "action": "script:noop", "version": "1.0.0", "timeout_seconds": 1}],
                "patrol": {"interval_seconds": 60, "steps": []},
                "teardown": [],
            }
        },
    }
    mock_adb = MagicMock()

    with patch("backend.agent.job_runner.update_job"), \
         patch("backend.agent.job_runner.complete_job"), \
         patch("backend.agent.job_runner.execute_pipeline_run") as mock_exec, \
         patch("backend.agent.job_runner._validate_pipeline_def", return_value=None), \
         patch("backend.agent.job_runner.JobSession") as mock_session:
        mock_exec.return_value = {"status": "FINISHED", "exit_code": 0}

        run_task_wrapper(
            run, mock_adb, "http://x", "h1",
            job_runner_state, None, None, None,
        )

    mock_session.assert_not_called()
    assert mock_exec.call_args.kwargs["watcher_capability"] is None


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


class _FakeStepTraceDB:
    def __init__(self, traces):
        self._traces = list(traces)
        self.acked = []

    def get_unacked_traces(self, after_id=0):
        return [
            t for t in self._traces
            if t["id"] > after_id and t["id"] not in self.acked
        ]

    def mark_acked(self, trace_id):
        self.acked.append(trace_id)


class _FakeHTTPResponse:
    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def _trace(trace_id, job_id):
    return {
        "id": trace_id,
        "job_id": job_id,
        "step_id": f"step-{trace_id}",
        "stage": "init",
        "event_type": "FAILED",
        "status": "FAILED",
        "output": None,
        "error_message": "boom",
        "original_ts": "2026-05-06T00:00:00+00:00",
        "fencing_token": f"{job_id}:1",
    }


def test_step_trace_uploader_acks_single_409_rejection():
    """失效 fencing_token 的 step_trace 不应无限重试刷 409。"""
    local_db = _FakeStepTraceDB([_trace(1, 101)])
    uploader = StepTraceUploader("http://server", local_db)

    with patch(
        "backend.agent.step_trace_uploader.requests.post",
        return_value=_FakeHTTPResponse(409),
    ):
        uploaded = uploader._upload_once()

    assert uploaded == 1
    assert local_db.acked == [1]


def test_step_trace_uploader_splits_batch_conflict_before_ack():
    """批量 409 时逐条确认，避免把同批次有效 trace 直接丢弃。"""
    local_db = _FakeStepTraceDB([_trace(1, 101), _trace(2, 102)])
    uploader = StepTraceUploader("http://server", local_db)

    def post_side_effect(url, json, headers, timeout):
        if len(json) > 1:
            return _FakeHTTPResponse(409)
        return _FakeHTTPResponse(200 if json[0]["job_id"] == 101 else 409)

    with patch(
        "backend.agent.step_trace_uploader.requests.post",
        side_effect=post_side_effect,
    ) as mock_post:
        uploaded = uploader._upload_once()

    assert uploaded == 2
    assert local_db.acked == [1, 2]
    assert mock_post.call_count == 3


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
