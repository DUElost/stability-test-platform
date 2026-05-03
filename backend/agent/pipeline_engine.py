"""Pipeline execution engine for the agent.

Parses stages-format pipeline definitions and executes steps in stage order:
prepare -> execute -> post_process.
"""

import hashlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_STEP_OUTPUT_CHARS = 64 * 1024


def _truncate_step_output(value: str) -> str:
    if len(value) <= _MAX_STEP_OUTPUT_CHARS:
        return value
    return value[:_MAX_STEP_OUTPUT_CHARS] + "\n[truncated]"


@dataclass
class StepContext:
    """Context passed to each action function."""

    adb: Any  # AdbWrapper instance
    serial: str
    params: dict
    run_id: int
    step_id: int
    logger: Any  # StepLogger instance
    # Shared metrics store for cross-step data passing (e.g., PID from start_process)
    shared: dict = field(default_factory=dict)
    # LocalDB instance for cross-run persistent state (e.g., incremental scan_aee)
    local_db: Any = None
    log_dir: str = ""
    adb_path: str = ""
    nfs_root: str = ""

    @property
    def job_id(self) -> int:
        """Alias for run_id — new code should use ctx.job_id.

        The underlying field remains run_id for backward compatibility with
        existing actions / tools / tests (20+ call sites). A future governance
        PR may rename the field once all consumers migrate.
        """
        return self.run_id


@dataclass
class StepResult:
    """Result returned by each action function."""

    success: bool
    exit_code: int = 0
    error_message: str = ""
    metrics: dict = field(default_factory=dict)
    artifact: Optional[dict] = None
    output: str = ""
    metadata: dict = field(default_factory=dict)
    skipped: bool = False
    skip_reason: str = ""


class PipelineAction:
    """Marker base class for all ``tool:<id>`` Pipeline Action scripts.

    Subclasses **must** implement :meth:`run`.  Class-level attributes are used
    by :class:`~backend.agent.tool_discovery.ToolDiscovery` to register the
    action into the database automatically.

    Usage::

        class MyAction(PipelineAction):
            TOOL_CATEGORY = "MY_CATEGORY"
            TOOL_DESCRIPTION = "What this action does."

            @classmethod
            def get_default_params(cls) -> dict:
                return {"timeout": 300}

            def run(self, ctx: StepContext) -> StepResult:
                ctx.logger.info("running")
                return StepResult(success=True)
    """

    TOOL_CATEGORY: str = ""
    TOOL_DESCRIPTION: str = ""

    @classmethod
    def get_default_params(cls) -> dict:
        """Return the default parameter dict surfaced in the UI."""
        return {}

    def run(self, ctx: StepContext) -> StepResult:  # noqa: D102
        raise NotImplementedError(f"{type(self).__name__} must implement run(ctx)")


class PipelineEngine:
    """Executes a pipeline definition: phase-serial, intra-phase parallel."""

    def __init__(
        self,
        adb,
        serial: str,
        run_id: int,
        log_dir: Optional[str] = None,
        ws_client=None,
        http_fallback=None,
        mq_producer=None,
        tool_registry=None,
        script_registry=None,
        local_db=None,
        api_url: Optional[str] = None,
        agent_secret: str = "",
        nfs_root: Optional[str] = None,
        is_aborted: Optional[Callable[[], bool]] = None,
        fencing_token: Optional[str] = None,
    ):
        self._adb = adb
        self._serial = serial
        self._run_id = run_id
        self._log_dir = log_dir
        self._ws = ws_client
        self._http_fallback = http_fallback
        self._mq = mq_producer
        self._registry = tool_registry
        self._script_registry = script_registry
        self._local_db = local_db
        self._api_url = api_url
        self._agent_secret = agent_secret
        self._adb_path = getattr(adb, "adb_path", os.getenv("ADB_PATH", "adb"))
        self._nfs_root = nfs_root if nfs_root is not None else os.getenv("STP_NFS_ROOT", "")
        self._is_aborted = is_aborted
        self._fencing_token = fencing_token or ""  # ADR-0019 Phase 2b
        self._shared: dict = {}
        self._canceled = False

    def cancel(self):
        """Signal cancellation to the engine."""
        self._canceled = True

    def execute(self, pipeline_def: dict) -> StepResult:
        """Execute the full pipeline: stages format or lifecycle format."""
        # Verify device lock is held before executing
        lock_err = self._verify_device_lease()
        if lock_err:
            return lock_err

        if "lifecycle" in pipeline_def:
            return self._execute_lifecycle(pipeline_def)

        if "stages" in pipeline_def:
            return self._execute_stages_format(pipeline_def)

        if "phases" in pipeline_def:
            return StepResult(
                success=False,
                exit_code=1,
                error_message="legacy phases format is not supported; use 'stages' or 'lifecycle'",
            )

        return StepResult(
            success=False,
            exit_code=1,
            error_message="pipeline_def must contain 'stages' or 'lifecycle'",
        )

    def _verify_device_lease(self) -> Optional[StepResult]:
        """Verify device lease via extend_lock endpoint. Returns StepResult on failure, None on success."""
        if not self._api_url:
            return None  # No API URL configured — skip verification (dev mode)

        import requests

        url = f"{self._api_url}/api/v1/agent/jobs/{self._run_id}/extend_lock"
        headers = {}
        if self._agent_secret:
            headers["X-Agent-Secret"] = self._agent_secret
        retry_delays = [1, 2, 4]  # exponential backoff

        for attempt, delay in enumerate(retry_delays, 1):
            try:
                resp = requests.post(
                    url, json={"fencing_token": self._fencing_token}, headers=headers, timeout=10,
                )
                if resp.status_code == 409:
                    logger.error("device_lease_not_held run=%d — aborting pipeline", self._run_id)
                    return StepResult(
                        success=False,
                        exit_code=1,
                        error_message="device_lease_not_held",
                    )
                if resp.status_code == 401:
                    logger.error("lock_verify_auth_failed run=%d status=401", self._run_id)
                    return StepResult(
                        success=False,
                        exit_code=1,
                        error_message="lock_verify_auth_failed",
                    )
                resp.raise_for_status()
                logger.debug("lock_verified run=%d", self._run_id)
                return None  # Lock verified
            except requests.HTTPError:
                logger.error("lock_verification_failed run=%d status=%s", self._run_id, resp.status_code)
                return StepResult(
                    success=False,
                    exit_code=1,
                    error_message=f"lock_verification_http_{resp.status_code}",
                )
            except requests.RequestException as e:
                logger.warning("lock_verify_attempt_%d_failed run=%d: %s", attempt, self._run_id, e)
                if attempt < len(retry_delays):
                    time.sleep(delay)

        logger.error("lock_verification_unreachable run=%d", self._run_id)
        return StepResult(
            success=False,
            exit_code=1,
            error_message="lock_verification_unreachable",
        )

    def _archive_logs(self) -> Optional[dict]:
        """Archive the run log directory into a tar.gz file and return artifact info."""
        if not self._log_dir or not os.path.exists(self._log_dir):
            return None

        try:
            # Archive filename
            archive_path = f"{self._log_dir}.tar.gz"

            # Create tarball
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(self._log_dir, arcname=os.path.basename(self._log_dir))

            # Calculate size and checksum
            size_bytes = os.path.getsize(archive_path)
            sha256 = hashlib.sha256()
            with open(archive_path, "rb") as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    sha256.update(chunk)
            checksum = sha256.hexdigest()

            # Create storage_uri (file:// scheme for central storage)
            storage_uri = f"file://{os.path.abspath(archive_path)}"

            return {
                "storage_uri": storage_uri,
                "size_bytes": size_bytes,
                "checksum": checksum,
            }
        except Exception as e:
            logger.warning(f"Failed to archive logs: {e}")
            return None

    def _run_with_timeout(
        self, action_fn: Callable, ctx: StepContext, timeout: int
    ) -> StepResult:
        """Run an action with timeout enforcement.

        Uses a daemon thread so that on timeout, the calling thread returns
        immediately without blocking on ThreadPoolExecutor.__exit__.
        """
        result_holder: List[Any] = []  # [StepResult] or [Exception]
        error_holder: List[Exception] = []

        def _worker():
            try:
                result_holder.append(action_fn(ctx))
            except Exception as e:
                error_holder.append(e)

        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()
        worker.join(timeout=timeout)

        if worker.is_alive():
            # Thread still running after timeout — return failure immediately.
            # The daemon thread will be abandoned (cleaned up on process exit).
            logger.warning(f"Step timed out after {timeout}s, abandoning worker thread")
            return StepResult(
                success=False,
                exit_code=124,
                error_message=f"Step timed out after {timeout}s",
            )

        if error_holder:
            return StepResult(
                success=False, exit_code=1, error_message=str(error_holder[0])
            )

        if result_holder:
            return result_holder[0]

        return StepResult(
            success=False, exit_code=1, error_message="Action returned no result"
        )

    def _report_step_status(self, step_id: int, status: str, **kwargs):
        """Report step status via WS or HTTP fallback."""
        if not step_id:
            return

        if self._ws and self._ws.connected:
            self._ws.send_step_update(self._run_id, step_id, status, **kwargs)
        elif self._http_fallback:
            try:
                self._http_fallback(self._run_id, step_id, status, **kwargs)
            except Exception as e:
                logger.warning(f"HTTP step status fallback failed: {e}")

    # ==================================================================
    # Stages-format execution (stp-spec §2)
    # ==================================================================

    def _execute_stages_format(self, pipeline_def: dict) -> StepResult:
        """Execute new stages-format pipeline: prepare → execute → post_process (serial).

        This is the top-level entry for standalone stages jobs. It wraps
        ``_run_stages_only()`` and adds terminal side effects (MQ COMPLETED/FAILED,
        log archiving).  Lifecycle sub-pipelines should call ``_run_stages_only()``
        directly to avoid premature terminal events.
        """
        result = self._run_stages_only(pipeline_def)
        if result.success:
            self._report_job_status_mq("COMPLETED")
            artifact = self._archive_logs()
            result.artifact = artifact
        else:
            step_hint = result.error_message or "unknown"
            self._report_job_status_mq("FAILED", reason=f"step_failed:{step_hint}")
        return result

    def _run_stages_only(self, pipeline_def: dict) -> StepResult:
        """Execute stages (prepare → execute → post_process) without terminal side effects.

        Returns a StepResult indicating success or failure. Does NOT send
        MQ terminal status or archive logs — the caller is responsible for that.
        """
        # Replace {log_dir} placeholders in pipeline params
        if self._log_dir:
            raw = json.dumps(pipeline_def)
            raw = raw.replace("{log_dir}", self._log_dir.replace("\\", "/"))
            pipeline_def = json.loads(raw)

        stages_def = pipeline_def.get("stages", {})

        for stage_name in ("prepare", "execute", "post_process"):
            steps = stages_def.get(stage_name, [])
            for step in steps:
                # Check for lock lost (LeaseRenewer removed us from active set)
                if self._is_lock_lost():
                    return StepResult(
                        success=False,
                        exit_code=1,
                        error_message="device_lease_lost",
                    )

                success = self._run_step_with_retry_stages(stage_name, step)
                if not success:
                    step_id = step.get("step_id", "unknown")
                    return StepResult(
                        success=False,
                        exit_code=1,
                        error_message=f"step failed in {stage_name}: {step_id}",
                    )

        return StepResult(success=True)

    def _is_lock_lost(self) -> bool:
        """Check if the run has been aborted (e.g. LeaseRenewer received 409)."""
        if self._is_aborted is not None:
            return self._is_aborted()
        return False

    def _run_step_with_retry_stages(self, stage: str, step: dict) -> bool:
        """Execute a step with retry logic. Returns True on success."""
        max_retry = step.get("retry", 0)
        for attempt in range(max_retry + 1):
            result = self._execute_step_stages(stage, step)
            if result.success:
                return True
            if attempt < max_retry:
                time.sleep(5 * (attempt + 1))
        return False

    def _execute_step_stages(self, stage: str, step: dict) -> StepResult:
        """Execute a single step in stages format. Reports STARTED/COMPLETED/FAILED via MQ."""
        step_id = step.get("step_id", "unknown")
        action = step.get("action", "")
        params = step.get("params", {})
        timeout = step.get("timeout_seconds", step.get("timeout", 300))

        if step.get("enabled") is False:
            result = StepResult(success=True, skipped=True, skip_reason="step disabled")
            self._report_step_trace_mq(
                step_id,
                stage,
                "COMPLETED",
                "SKIPPED",
                output=result.skip_reason,
            )
            return result

        self._report_step_trace_mq(step_id, stage, "STARTED", "RUNNING")

        log_file = None
        if self._log_dir:
            import re

            safe = re.sub(r"[^\w\-]", "_", step_id)
            log_file = os.path.join(self._log_dir, f"{stage}_{safe}.log")

        ctx = StepContext(
            adb=self._adb,
            serial=self._serial,
            params=params,
            run_id=self._run_id,
            step_id=0,
            logger=self._make_mq_logger(step_id, log_file),
            shared=self._shared,
            local_db=self._local_db,
            log_dir=self._log_dir or "",
            adb_path=self._adb_path or "",
            nfs_root=self._nfs_root or "",
        )

        try:
            action_fn = self._resolve_action_stages(action, step)
            if action_fn is None:
                result = StepResult(
                    success=False,
                    exit_code=1,
                    error_message=f"Unknown action: {action}",
                )
            else:
                result = self._run_with_timeout(action_fn, ctx, timeout)
        except Exception as e:
            result = StepResult(success=False, exit_code=1, error_message=str(e))

        event_type = "COMPLETED" if result.success else "FAILED"
        status = "SKIPPED" if result.success and result.skipped else (
            "COMPLETED" if result.success else "FAILED"
        )
        self._report_step_trace_mq(
            step_id,
            stage,
            event_type,
            status,
            output=result.skip_reason if result.skipped else (result.output or None),
            error_message=result.error_message if not result.success else None,
        )

        # Store metrics in shared context (mirrors legacy _execute_step behavior)
        if result.metrics:
            self._shared[step_id] = result.metrics

        return result

    def _resolve_action_stages(self, action: str, step: dict) -> Optional[Callable]:
        """Resolve action for stages format. tool: uses ToolRegistry; no shell: allowed."""
        if action.startswith("tool:"):
            try:
                tool_id = int(action.split(":", 1)[1])
            except (IndexError, ValueError):
                return lambda ctx: StepResult(
                    success=False,
                    exit_code=1,
                    error_message=f"Invalid tool action format: {action}",
                )
            required_version = step.get("version", "")
            return lambda ctx: self._run_tool_action_stages(
                ctx, tool_id, required_version
            )

        if action.startswith("script:"):
            return lambda ctx: self._run_script_action(ctx, step)

        return None

    def _run_script_action(self, ctx: StepContext, step: dict) -> StepResult:
        """Execute a script:<name> action through ScriptRegistry metadata."""
        if self._script_registry is None:
            return StepResult(
                success=False,
                exit_code=1,
                error_message="ScriptRegistry not available — cannot execute script: action",
            )

        action = step.get("action", "")
        name = action.split(":", 1)[1]
        version = step.get("version", "")

        try:
            entry = self._script_registry.resolve(name, version)
        except Exception as exc:
            return StepResult(success=False, exit_code=1, error_message=str(exc))

        runners = {
            "python": [sys.executable, entry.nfs_path],
            "shell": ["bash", entry.nfs_path],
            "bat": ["cmd.exe", "/c", entry.nfs_path],
        }
        cmd = runners.get(entry.script_type)
        if cmd is None:
            return StepResult(
                success=False,
                exit_code=1,
                error_message=f"Unsupported script_type: {entry.script_type}",
            )

        env = os.environ.copy()
        env.update({
            "STP_DEVICE_SERIAL": ctx.serial,
            "STP_ADB_PATH": ctx.adb_path or self._adb_path or "",
            "STP_LOG_DIR": ctx.log_dir or "",
            "STP_STEP_PARAMS": json.dumps(ctx.params or {}, ensure_ascii=False),
            "STP_NFS_ROOT": ctx.nfs_root or self._nfs_root or "",
            "STP_JOB_ID": str(ctx.job_id),
        })

        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=step.get("timeout_seconds", 300),
                cwd=os.path.dirname(entry.nfs_path) or None,
            )
        except subprocess.TimeoutExpired:
            return StepResult(success=False, exit_code=124, error_message="script timeout")
        except Exception as exc:
            return StepResult(success=False, exit_code=1, error_message=str(exc))

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        combined_output = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)

        if proc.returncode != 0:
            return StepResult(
                success=False,
                exit_code=proc.returncode,
                error_message=(stderr or stdout or "")[:2000],
                output=_truncate_step_output(combined_output),
            )

        payload = {}
        clean_stdout = stdout.strip()
        if clean_stdout:
            try:
                payload = json.loads(clean_stdout)
            except json.JSONDecodeError:
                payload = {}

        return StepResult(
            success=True,
            metrics=payload.get("metrics", {}) if isinstance(payload, dict) else {},
            skipped=bool(payload.get("skipped")) if isinstance(payload, dict) else False,
            skip_reason=payload.get("skip_reason", "") if isinstance(payload, dict) else "",
            output=_truncate_step_output(combined_output),
        )

    def _run_tool_action_stages(
        self, ctx: StepContext, tool_id: int, required_version: str
    ) -> StepResult:
        """Execute a tool: action via ToolRegistry. Handles version mismatch + PENDING_TOOL."""
        if self._registry is None:
            return StepResult(
                success=False,
                exit_code=1,
                error_message="ToolRegistry not available — cannot execute tool: action",
            )

        from .registry.tool_registry import (
            ToolEntry,
            ToolNotFoundLocally,
            ToolVersionMismatch,
        )

        try:
            entry = self._registry.resolve(tool_id, required_version)
        except ToolVersionMismatch:
            success = self._registry.pull_tool_sync(tool_id, required_version)
            if not success:
                self._report_job_status_mq(
                    "PENDING_TOOL",
                    reason=f"tool_pull_failed:network tool_id={tool_id} version={required_version}",
                )
                return StepResult(
                    success=False,
                    exit_code=1,
                    error_message=f"tool_pull_failed: tool_id={tool_id} version={required_version}",
                )
            try:
                entry = self._registry.resolve(tool_id, required_version)
            except ToolNotFoundLocally:
                self._report_job_status_mq(
                    "FAILED",
                    reason=f"tool_version_not_exist:tool_id={tool_id} version={required_version}",
                )
                return StepResult(
                    success=False,
                    exit_code=1,
                    error_message=f"tool_version_not_exist: tool_id={tool_id} version={required_version}",
                )
        except ToolNotFoundLocally:
            return StepResult(
                success=False,
                exit_code=1,
                error_message=f"tool_not_found: tool_id={tool_id}",
            )

        return self._execute_tool_script(ctx, entry)

    def _execute_tool_script(self, ctx: StepContext, entry) -> StepResult:
        """Dynamically load and execute a tool script from its local path.

        Expects a native Pipeline Action class with run(ctx) -> StepResult interface.
        """
        script_path = entry.script_path
        script_class = entry.script_class

        if not script_path or not os.path.exists(script_path):
            return StepResult(
                success=False,
                exit_code=1,
                error_message=f"Tool script not found: {script_path!r}",
            )

        try:
            spec = importlib.util.spec_from_file_location("_dyn_tool", script_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            tool_cls = getattr(module, script_class)

            result = tool_cls().run(ctx)
            if not isinstance(result, StepResult):
                result = StepResult(success=bool(result))
            return result
        except Exception as e:
            return StepResult(success=False, exit_code=1, error_message=str(e))

    # ------------------------------------------------------------------
    # MQ reporting helpers
    # ------------------------------------------------------------------

    def _report_step_trace_mq(
        self,
        step_id: str,
        stage: str,
        event_type: str,
        status: str,
        output: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Send step_trace via MQ (primary) or WS fallback."""
        if self._mq and self._mq.connected:
            self._mq.send_step_trace(
                job_id=self._run_id,
                step_id=step_id,
                stage=stage,
                event_type=event_type,
                status=status,
                output=output,
                error_message=error_message,
            )
            return
        # WS fallback (best-effort)
        if self._ws:
            self._ws.send(
                {
                    "type": "step_trace",
                    "run_id": self._run_id,
                    "step_id": step_id,
                    "stage": stage,
                    "event_type": event_type,
                    "status": status,
                    "output": output,
                }
            )

    def _report_job_status_mq(self, status: str, reason: str = "") -> None:
        """Send job_status event via MQ (primary) or WS fallback."""
        if self._mq and self._mq.connected:
            self._mq.send_job_status(self._run_id, status, reason)
            return
        if self._ws:
            self._ws.send(
                {
                    "type": "job_status",
                    "run_id": self._run_id,
                    "status": status,
                    "reason": reason,
                }
            )

    def _make_mq_logger(self, step_id: str, log_file: Optional[str] = None):
        """Create a logger that writes to SocketIO (primary) and a local file."""
        return _MQStepLogger(
            mq_producer=self._mq,
            run_id=self._run_id,
            step_id_str=step_id,
            log_file=log_file,
            ws_client=self._ws,
        )

    # ==================================================================
    # Lifecycle execution (init → patrol loop → teardown)
    # ==================================================================

    def _execute_lifecycle(self, pipeline_def: dict) -> StepResult:
        """Execute a lifecycle pipeline: init → patrol_loop → teardown (best-effort).

        The lifecycle key contains sub-pipelines for each phase.  Patrol runs
        in a loop with ``interval_seconds`` between iterations until a
        termination condition is met.  Teardown always runs via try/finally.

        Uses ``_run_stages_only()`` for sub-pipelines to avoid premature
        terminal MQ events and log archiving.

        All exit paths flow through a single post-finally block that handles
        terminal MQ status, log archiving, and final StepResult construction.
        """
        lifecycle = pipeline_def["lifecycle"]
        timeout_seconds = lifecycle.get("timeout_seconds", 0)
        init_def = lifecycle["init"]
        patrol_def = lifecycle.get("patrol")
        teardown_def = lifecycle["teardown"]

        # Replace {log_dir} / {run_id} placeholders in all sub-pipelines
        if self._log_dir:
            raw = json.dumps(lifecycle)
            raw = raw.replace("{log_dir}", self._log_dir.replace("\\", "/"))
            raw = raw.replace("{run_id}", str(self._run_id))
            lifecycle = json.loads(raw)
            init_def = lifecycle["init"]
            patrol_def = lifecycle.get("patrol")
            teardown_def = lifecycle["teardown"]

        termination_reason = "completed"
        lifecycle_error = ""
        teardown_result = None

        try:
            # ── Phase 1: Init ──
            self._report_job_status_mq("INIT_RUNNING")
            logger.info("[Lifecycle] run=%d — executing init", self._run_id)

            init_result = self._run_stages_only({"stages": init_def["stages"]})
            if not init_result.success:
                # Distinguish lock_lost (abort) from genuine init failure
                if init_result.error_message == "device_lease_lost":
                    termination_reason = "abort"
                else:
                    termination_reason = "init_failure"
                lifecycle_error = f"lifecycle init failed: {init_result.error_message}"
                logger.error("[Lifecycle] run=%d — init failed: %s", self._run_id, init_result.error_message)
                # Do NOT return here — fall through to finally for teardown,
                # then to the unified exit block for MQ status + artifact.

            elif patrol_def:
                # ── Phase 2: Patrol loop (only if init succeeded) ──
                interval = patrol_def.get("interval_seconds", 300)
                init_completed_at = time.time()
                iteration = 0

                self._report_job_status_mq("PATROL_RUNNING")

                while True:
                    # Check termination conditions before each patrol
                    if self._is_lock_lost() or self._canceled:
                        termination_reason = "abort"
                        logger.info("[Lifecycle] run=%d — abort detected, ending patrol loop", self._run_id)
                        break

                    if timeout_seconds > 0 and (time.time() - init_completed_at) >= timeout_seconds:
                        termination_reason = "timeout"
                        logger.info("[Lifecycle] run=%d — timeout reached (%ds), ending patrol loop", self._run_id, timeout_seconds)
                        break

                    iteration += 1
                    logger.info("[Lifecycle] run=%d — [Patrol #%d] starting", self._run_id, iteration)

                    patrol_result = self._run_stages_only({"stages": patrol_def["stages"]})

                    if not patrol_result.success:
                        # Distinguish lock_lost (abort) from genuine patrol failure
                        if patrol_result.error_message == "device_lease_lost":
                            termination_reason = "abort"
                        else:
                            termination_reason = "patrol_failure"
                            lifecycle_error = f"patrol #{iteration} failed: {patrol_result.error_message}"
                        logger.error("[Lifecycle] run=%d — [Patrol #%d] failed: %s", self._run_id, iteration, patrol_result.error_message)
                        break

                    # Report patrol progress
                    time_elapsed = time.time() - init_completed_at
                    time_remaining = max(0, timeout_seconds - time_elapsed) if timeout_seconds > 0 else -1
                    next_patrol_at = (datetime.now(timezone.utc) + timedelta(seconds=interval)).isoformat() + "Z" if interval > 0 else ""

                    self._report_job_status_mq(
                        "PATROL_RUNNING",
                        reason=f"iteration={iteration} next_in={interval}s remaining={int(time_remaining)}s",
                    )

                    logger.info(
                        "[Lifecycle] run=%d — [Patrol #%d] completed, next in %ds (remaining: %ds)",
                        self._run_id, iteration, interval, int(time_remaining),
                    )

                    # Fixed-delay wait with abort/timeout checking (sleep in 5s chunks)
                    sleep_remaining = interval
                    while sleep_remaining > 0:
                        chunk = min(sleep_remaining, 5)
                        time.sleep(chunk)
                        sleep_remaining -= chunk
                        if self._is_lock_lost() or self._canceled:
                            termination_reason = "abort"
                            break
                        if timeout_seconds > 0 and (time.time() - init_completed_at) >= timeout_seconds:
                            termination_reason = "timeout"
                            logger.info("[Lifecycle] run=%d — timeout reached during sleep, ending patrol loop", self._run_id)
                            break
                    if termination_reason in ("abort", "timeout"):
                        break

        finally:
            # ── Phase 3: Teardown (best-effort, always runs) ──
            self._report_job_status_mq("TEARDOWN_RUNNING", reason=f"termination_reason={termination_reason}")
            logger.info("[Lifecycle] run=%d — executing teardown (reason: %s)", self._run_id, termination_reason)

            teardown_result = self._execute_teardown_best_effort(teardown_def)

        # ── Unified exit: terminal MQ + artifact + StepResult ──
        success = termination_reason in ("completed", "timeout")
        artifact = self._archive_logs()

        # Map termination_reason to MQ terminal status
        if success:
            mq_status = "COMPLETED"
        elif termination_reason == "abort":
            mq_status = "ABORTED"
        else:
            mq_status = "FAILED"

        self._report_job_status_mq(
            mq_status,
            reason=f"termination_reason={termination_reason}",
        )

        # Merge teardown metadata into final result
        final_metadata = {"termination_reason": termination_reason}
        if teardown_result and isinstance(teardown_result.metadata, dict):
            final_metadata["teardown_status"] = teardown_result.metadata.get("teardown_status", "UNKNOWN")

        return StepResult(
            success=success,
            exit_code=0 if success else 1,
            error_message="" if success else (lifecycle_error or f"lifecycle ended: {termination_reason}"),
            artifact=artifact,
            metadata=final_metadata,
        )

    def _execute_teardown_best_effort(self, teardown_def: dict) -> StepResult:
        """Execute teardown with best-effort semantics: each step runs independently.

        Returns a StepResult with metadata["teardown_status"]:
        - "SUCCESS" — all steps passed
        - "DEGRADED" — some steps failed but at least one succeeded
        - "FAILED" — all steps failed
        """
        stages = teardown_def.get("stages", {})
        total_steps = 0
        failed_steps = 0
        errors = []

        for stage_name in ("prepare", "execute", "post_process"):
            steps = stages.get(stage_name, [])
            for step in steps:
                total_steps += 1
                step_id = step.get("step_id", "unknown")
                try:
                    result = self._execute_step_stages(stage_name, step)
                    if not result.success:
                        failed_steps += 1
                        errors.append(f"{step_id}: {result.error_message}")
                        logger.warning("[Teardown] step '%s' failed: %s", step_id, result.error_message)
                except Exception as e:
                    failed_steps += 1
                    errors.append(f"{step_id}: {e}")
                    logger.warning("[Teardown] step '%s' exception: %s", step_id, e)

        if failed_steps > 0:
            logger.warning(
                "[Teardown] %d/%d steps failed: %s",
                failed_steps, total_steps, "; ".join(errors),
            )

        # Determine teardown status: SUCCESS / DEGRADED / FAILED
        if failed_steps == 0:
            teardown_status = "SUCCESS"
        elif failed_steps < total_steps:
            teardown_status = "DEGRADED"
        else:
            teardown_status = "FAILED"

        return StepResult(
            success=(total_steps == 0 or failed_steps < total_steps),  # DEGRADED still counts as success
            exit_code=0 if failed_steps == 0 else 1,
            error_message=f"teardown: {failed_steps}/{total_steps} steps failed" if failed_steps > 0 else "",
            metadata={"teardown_status": teardown_status},
        )


class _MQStepLogger:
    """Lightweight logger that sends lines via SocketIO (primary) and writes to local file."""

    def __init__(
        self,
        mq_producer,
        run_id: int,
        step_id_str: str,
        log_file: Optional[str] = None,
        ws_client=None,
    ):
        self._mq = mq_producer
        self._ws = ws_client
        self._run_id = run_id
        self._step_id = step_id_str
        self._log_file = log_file
        if log_file:
            try:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
            except Exception:
                pass

    def _write(self, message: str, level: str) -> None:
        sent = False
        if self._mq and self._mq.connected:
            self._mq.send_log(
                job_id=self._run_id,
                device_id=0,
                level=level,
                tag=self._step_id,
                message=message,
            )
            sent = True

        if not sent and self._ws and self._ws.connected:
            try:
                self._ws.send_log(self._run_id, self._step_id, level, message)
            except Exception:
                pass

        if self._log_file:
            try:
                ts = datetime.now(timezone.utc).isoformat() + "Z"
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(f"{ts} [{level}] {message}\n")
            except Exception:
                pass

    def info(self, message: str) -> None:
        self._write(message, "INFO")

    def warn(self, message: str) -> None:
        self._write(message, "WARN")

    def error(self, message: str) -> None:
        self._write(message, "ERROR")

    def debug(self, message: str) -> None:
        self._write(message, "DEBUG")

    def log(self, message: str, level: str = "INFO") -> None:
        self._write(message, level)
