"""ScriptBatchRunner: lightweight script-sequence executor.

claim → JobSession(Watcher) → subprocess per item → report → complete.
Reuses existing JobSession/Watcher infrastructure, skips PipelineEngine.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

from .api_client import _post_with_retry
from .job_session import JobSession

logger = logging.getLogger(__name__)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def fetch_pending_batches(api_url: str, host_id: str) -> Optional[dict]:
    """Claim a pending script batch for this host."""
    try:
        from .api_client import _get_agent_secret
        agent_secret = _get_agent_secret()
        headers = {"X-Agent-Secret": agent_secret} if agent_secret else {}
        resp = requests.post(
            f"{api_url}/api/v1/agent/script-batches/claim?host_id={host_id}",
            json=None,
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and data.get("data"):
            return data["data"]
        return None
    except Exception:
        logger.debug("claim_script_batch_none host=%s", host_id)
        return None


def report_item_status(
    api_url: str,
    batch_id: int,
    item_index: int,
    status: str,
    exit_code: Optional[int] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    metrics: Optional[dict] = None,
) -> None:
    payload = {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "metrics": metrics,
    }
    _post_with_retry(
        f"{api_url}/api/v1/agent/script-batches/{batch_id}/runs/{item_index}/status",
        payload,
        context=f"script_run_status:{batch_id}:{item_index}",
    )


def complete_batch(
    api_url: str,
    batch_id: int,
    status: str,
    watcher_summary: Optional[dict] = None,
) -> None:
    payload = {"status": status}
    if watcher_summary:
        payload["watcher_summary"] = watcher_summary
    _post_with_retry(
        f"{api_url}/api/v1/agent/script-batches/{batch_id}/complete",
        payload,
        context=f"script_batch_complete:{batch_id}",
    )


class ScriptBatchRunnerState:
    """Shared state for ScriptBatchRunner, mirroring JobRunnerState pattern."""

    def __init__(
        self,
        active_jobs_lock,
        active_job_ids: set,
        active_device_ids: set,
        watcher_enabled: bool = False,
        lock_register=None,
        lock_deregister=None,
        device_id_register=None,
        device_id_deregister=None,
    ):
        self.active_jobs_lock = active_jobs_lock
        self.active_job_ids = active_job_ids
        self.active_device_ids = active_device_ids
        self.watcher_enabled = watcher_enabled
        self.register_active_job = lock_register
        self.deregister_active_job = lock_deregister
        self.register_active_device = device_id_register
        self.deregister_active_device = device_id_deregister


def run_script_batch(
    claim: dict,
    adb,
    api_url: str,
    host_id: str,
    state: ScriptBatchRunnerState,
    script_registry=None,
    local_db=None,
) -> None:
    """Execute a script batch: claim → session → subprocess per item → complete."""
    batch_id = int(claim["batch_id"])
    device_id = int(claim["device_id"])
    device_serial = claim.get("device_serial", "")
    items: List[dict] = claim.get("items", [])
    on_failure = claim.get("on_failure", "stop")

    logger.info("run_script_batch_START batch=%d device=%s items=%d watcher=%s", batch_id, device_serial, len(items), state.watcher_enabled)

    log_dir = claim.get("log_dir") or os.path.join(
        os.environ.get("STP_LOG_BASE", "/opt/stability-test-agent/logs"),
        f"batch_{batch_id}",
    )
    os.makedirs(log_dir, exist_ok=True)

    # ── 1. JobSession (Watcher start) ──
    session: Optional[JobSession] = None
    if state.watcher_enabled and state.register_active_job:
        try:
            session = JobSession(
                job_payload={
                    "id": batch_id,
                    "device_id": device_id,
                    "device_serial": device_serial,
                    "host_id": host_id,
                    "pipeline_def": {},
                    "watcher_policy": None,
                    "batch_items": items,
                },
                host_id=host_id,
                log_dir=log_dir,
                lock_register=state.register_active_job,
                lock_deregister=state.deregister_active_job,
                device_id_register=state.register_active_device,
                device_id_deregister=state.deregister_active_device,
            )
            session.__enter__()
        except Exception as exc:
            logger.warning("script_batch_watcher_start_failed batch=%d: %s", batch_id, exc)

    try:
        # ── 2. Execute scripts in order ──
        prev_failed = False
        results: list[dict] = []
        nfs_root = os.environ.get("STP_NFS_ROOT", "/mnt/nfs")

        for item in items:
            idx = item["item_index"]

            if prev_failed and on_failure == "stop":
                report_item_status(api_url, batch_id, idx, "SKIPPED", exit_code=0,
                                   stderr="previous step failed")
                results.append({"item_index": idx, "success": False, "skipped": True})
                continue

            report_item_status(api_url, batch_id, idx, "RUNNING")
            result = _execute_script(device_serial, log_dir, item, nfs_root, script_registry)
            status = "COMPLETED" if result["success"] else "FAILED"
            report_item_status(
                api_url, batch_id, idx, status,
                exit_code=result.get("exit_code"),
                stdout=result.get("stdout", ""),
                stderr=result.get("stderr", ""),
                metrics=result.get("metrics"),
            )
            results.append(result)
            if not result["success"]:
                prev_failed = True

        # ── 3. Compute final status ──
        all_success = all(r.get("success") or r.get("skipped") for r in results)
        any_failed = any(not r.get("success") and not r.get("skipped") for r in results)

        if all_success and not any_failed:
            final_status = "COMPLETED"
        elif any(r.get("success") for r in results):
            final_status = "PARTIAL"
        else:
            final_status = "FAILED"

        watcher_summary = session.summary.to_complete_payload() if session else None
        complete_batch(api_url, batch_id, final_status, watcher_summary)

    except Exception as exc:
        logger.exception("script_batch_failed batch=%d: %s", batch_id, exc)
        watcher_summary = session.summary.to_complete_payload() if session else None
        complete_batch(api_url, batch_id, "FAILED", watcher_summary)
    finally:
        if session:
            try:
                session.__exit__(None, None, None)
            except Exception:
                logger.exception("script_batch_session_exit_failed batch=%d", batch_id)
        # Release device lock
        with state.active_jobs_lock:
            state.active_job_ids.discard(batch_id)
            state.active_device_ids.discard(device_id)


def _execute_script(
    serial: str,
    log_dir: str,
    item: dict,
    nfs_root: str,
    script_registry=None,
) -> dict:
    name = item["script_name"]
    version = item.get("script_version", "")
    item_index = item.get("item_index", 0)
    timeout = item.get("timeout_seconds", 300)

    # ── 1. Try NFS path via ScriptRegistry first ──
    nfs_path = None
    script_type = "python"
    if script_registry:
        try:
            entry = script_registry.resolve(name, version)
            if os.path.isfile(entry.nfs_path):
                nfs_path = entry.nfs_path
                script_type = entry.script_type
        except Exception:
            pass

    tmp_file = None
    # ── 2. Fall back to inline script content if NFS path not available ──
    if not nfs_path:
        script_content_b64 = item.get("script_content", "")
        if script_content_b64:
            try:
                raw = base64.b64decode(script_content_b64)
                suffix = ".py"
                if raw[:2] == b"#!" or raw[:5] == b"#!/bi" or raw[:5] == b"#!/us":
                    suffix = ".sh"
                tmp = tempfile.NamedTemporaryFile(
                    mode="wb", suffix=suffix, delete=False,
                    dir=tempfile.gettempdir() or "/tmp",
                )
                tmp.write(raw)
                tmp.close()
                tmp_file = tmp.name
                os.chmod(tmp_file, 0o755)
                nfs_path = tmp_file
                script_type = "python" if suffix == ".py" else "shell"
                logger.info("script_inline_content name=%s version=%s tmp=%s", name, version, tmp_file)
            except Exception as exc:
                return {
                    "success": False, "exit_code": 1,
                    "stderr": f"Inline script decode failed: {exc}",
                    "item_index": item_index,
                }

    if not nfs_path:
        return {
            "success": False,
            "exit_code": 1,
            "stderr": f"Script not found: {name}:{version}",
            "item_index": item_index,
        }

    runners = {
        "python": [sys.executable, nfs_path],
        "shell": ["bash", nfs_path],
        "bat": ["cmd.exe", "/c", nfs_path],
    }
    cmd = runners.get(script_type, [sys.executable, nfs_path])

    cwd = os.path.dirname(nfs_path) or None if nfs_path else None

    env = os.environ.copy()
    env.update({
        "STP_DEVICE_SERIAL": serial,
        "STP_ADB_PATH": os.environ.get("STP_ADB_PATH", "adb"),
        "STP_LOG_DIR": log_dir,
        "STP_STEP_PARAMS": json.dumps(item.get("params", {}), ensure_ascii=False),
        "STP_NFS_ROOT": nfs_root,
        "STP_JOB_ID": str(item.get("batch_id", "")),
    })

    # Inject WiFi from claim-level env if not in params
    if "STP_WIFI_SSID" in os.environ and "wifi" in name.lower():
        env["STP_WIFI_SSID"] = os.environ["STP_WIFI_SSID"]
        env["STP_WIFI_PASSWORD"] = os.environ.get("STP_WIFI_PASSWORD", "")

    try:
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired:
            return {"success": False, "exit_code": 124, "stderr": "script timeout", "item_index": item_index}
        except Exception as exc:
            return {"success": False, "exit_code": 1, "stderr": str(exc), "item_index": item_index}

        stdout = (proc.stdout or "")[:64000]
        stderr = (proc.stderr or "")[:10000]

        metrics = {}
        if proc.stdout and proc.stdout.strip():
            try:
                parsed = json.loads(proc.stdout.strip())
                if isinstance(parsed, dict):
                    metrics = parsed
            except (json.JSONDecodeError, ValueError):
                pass

        return {
            "success": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "metrics": metrics,
            "item_index": item_index,
        }
    finally:
        if tmp_file:
            try:
                os.unlink(tmp_file)
            except OSError:
                pass
