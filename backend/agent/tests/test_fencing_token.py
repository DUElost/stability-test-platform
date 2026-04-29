"""ADR-0019 Phase 2b Agent 级 fencing_token 贯穿测试。

5 个测试，覆盖 token 流转、lock_manager skip、pipeline lock verify、
complete_run 透传、run_task_wrapper 入口校验。
"""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from backend.agent.api_client import complete_run, _build_complete_payload
from backend.agent.job_runner import JobRunnerState, run_task_wrapper
from backend.agent.lock_manager import LockRenewalManager
from backend.agent.pipeline_engine import PipelineEngine


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def lock_manager():
    lock = threading.Lock()
    ids = {1, 2}
    stop = threading.Event()
    mgr = LockRenewalManager(
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

    def register(jid: int, token: str = ""):
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
        "pipeline_def": {"stages": {"execute": [{"step_id": "x", "action": "builtin:noop"}]}},
        # 故意不写 fencing_token
    }
    mock_adb = MagicMock()
    with pytest.raises(KeyError, match="fencing_token"):
        run_task_wrapper(
            run, mock_adb, "http://x", "h1", None,
            job_runner_state, None, None, None, None,
        )


# ── Test 17: run_task_wrapper heartbeat + complete 带 token ──────────────────

def test_run_task_wrapper_heartbeat_and_complete_include_token(job_runner_state):
    """心跳 payload 和 complete_job 调用均显式传递 fencing_token。"""
    run = {
        "id": 99,
        "device_id": 2,
        "device_serial": "SN-99",
        "fencing_token": "2:3",
        "pipeline_def": {"stages": {"execute": [{"step_id": "x", "action": "builtin:noop"}]}},
    }
    mock_adb = MagicMock()

    with patch("backend.agent.job_runner.update_job") as mock_update, \
         patch("backend.agent.job_runner.complete_job") as mock_complete, \
         patch("backend.agent.job_runner.execute_pipeline_run") as mock_exec:
        mock_exec.return_value = {"status": "FINISHED", "exit_code": 0}

        run_task_wrapper(
            run, mock_adb, "http://x", "h1", None,
            job_runner_state, None, None, None, None,
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


# ── Test 18: LockRenewalManager skip when no token ───────────────────────────

def test_lock_renewal_manager_skips_extend_when_no_token(lock_manager):
    """无 fencing_token（如 ScriptBatch ID）→ extend_lock 直接 skip，不发 HTTP。"""
    with patch("requests.post") as mock_post:
        lock_manager._extend_lock(999)  # 999 has no token registered

    mock_post.assert_not_called()


def test_lock_renewal_manager_sends_token_when_present(lock_manager):
    """有 fencing_token → extend_lock POST body 包含 {"fencing_token": token}。"""
    lock_manager.set_fencing_token(1, "test:token:1")

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        lock_manager._extend_lock(1)

    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["json"] == {"fencing_token": "test:token:1"}


# ── Test 19: PipelineEngine._verify_device_lock sends fencing_token ──────────

def test_pipeline_engine_verify_device_lock_sends_fencing_token():
    """lock verify 阶段 POST extend_lock 携带 json={"fencing_token": token}。"""
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

        result = engine._verify_device_lock()

    assert result is None, "Valid lock should return None (verified)"
    mock_post.assert_called_once()
    assert mock_post.call_args.kwargs["json"] == {"fencing_token": "42:7"}, (
        "_verify_device_lock must POST fencing_token in body"
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
