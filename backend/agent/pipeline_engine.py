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
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


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


@dataclass
class StepResult:
    """Result returned by each action function."""

    success: bool
    exit_code: int = 0
    error_message: str = ""
    metrics: dict = field(default_factory=dict)
    artifact: Optional[dict] = None


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
        local_db=None,
    ):
        self._adb = adb
        self._serial = serial
        self._run_id = run_id
        self._log_dir = log_dir
        self._ws = ws_client
        self._http_fallback = http_fallback
        self._mq = mq_producer
        self._registry = tool_registry
        self._local_db = local_db
        self._shared: dict = {}
        self._canceled = False

    def cancel(self):
        """Signal cancellation to the engine."""
        self._canceled = True

    def execute(self, pipeline_def: dict) -> StepResult:
        """Execute the full pipeline in stages format."""
        if "stages" in pipeline_def:
            return self._execute_stages_format(pipeline_def)

        return StepResult(
            success=False,
            exit_code=1,
            error_message="legacy phases format is unsupported; use pipeline_def.stages",
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

    def _should_stop_pipeline(self, steps: list, result: StepResult) -> bool:
        """Check if the pipeline should stop based on failure policies."""
        # If any step failed with on_failure=stop, abort
        for step_def in steps:
            if (
                step_def.get("_failed", False)
                and step_def.get("on_failure", "stop") == "stop"
            ):
                return True
        return False

    def _execute_serial(self, phase_name: str, steps: list) -> StepResult:
        """Execute steps sequentially within a phase."""
        for idx, step_def in enumerate(steps):
            if self._canceled:
                self._mark_remaining_canceled(steps, idx)
                return StepResult(success=False, error_message="Canceled")

            result = self._execute_step(phase_name, idx, step_def)
            if not result.success:
                step_def["_failed"] = True
                policy = step_def.get("on_failure", "stop")
                if policy == "stop":
                    self._mark_remaining_canceled(steps, idx + 1)
                    return result
                elif policy == "retry":
                    max_retries = step_def.get("max_retries", 0)
                    retried = self._retry_step(phase_name, idx, step_def, max_retries)
                    if not retried.success:
                        self._mark_remaining_canceled(steps, idx + 1)
                        return retried
                # on_failure=continue: proceed

        # on_failure=continue steps may have _failed set, but don't fail the phase
        stop_failures = [
            s
            for s in steps
            if s.get("_failed") and s.get("on_failure", "stop") == "stop"
        ]
        return StepResult(success=len(stop_failures) == 0)

    def _execute_parallel(self, phase_name: str, steps: list) -> StepResult:
        """Execute steps concurrently within a phase using ThreadPoolExecutor."""
        max_workers = min(len(steps), 4)  # Cap at 4 concurrent steps
        results = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for idx, step_def in enumerate(steps):
                future = executor.submit(self._execute_step, phase_name, idx, step_def)
                futures[future] = (idx, step_def)

            for future in as_completed(futures):
                idx, step_def = futures[future]
                try:
                    result = future.result()
                    results[idx] = result
                    if not result.success:
                        step_def["_failed"] = True
                        # Apply per-step on_failure policy in parallel mode (fix: #5)
                        policy = step_def.get("on_failure", "stop")
                        if policy == "retry":
                            max_retries = step_def.get("max_retries", 0)
                            if max_retries > 0:
                                retried = self._retry_step(
                                    phase_name, idx, step_def, max_retries
                                )
                                results[idx] = retried
                                if retried.success:
                                    step_def["_failed"] = False
                except Exception as e:
                    step_def["_failed"] = True
                    results[idx] = StepResult(
                        success=False, exit_code=1, error_message=str(e)
                    )

        # Determine phase result:
        # - has_stop_failure: any step with on_failure=stop (or default) failed → phase fails
        # - on_failure=continue failures are logged but do NOT fail the phase
        has_stop_failure = False
        stop_error_msgs = []
        continue_error_msgs = []
        for idx, step_def in enumerate(steps):
            if step_def.get("_failed"):
                r = results.get(idx)
                policy = step_def.get("on_failure", "stop")
                if policy == "stop":
                    has_stop_failure = True
                    if r and r.error_message:
                        stop_error_msgs.append(r.error_message)
                else:
                    # continue/retry failures — record but don't fail phase
                    if r and r.error_message:
                        continue_error_msgs.append(r.error_message)

        if continue_error_msgs:
            logger.warning(
                f"[Pipeline] Phase '{phase_name}' had {len(continue_error_msgs)} continue-policy failures: {'; '.join(continue_error_msgs)}"
            )

        return StepResult(
            success=not has_stop_failure,
            exit_code=1 if has_stop_failure else 0,
            error_message="; ".join(stop_error_msgs) if stop_error_msgs else "",
        )

    def _execute_step(self, phase_name: str, idx: int, step_def: dict) -> StepResult:
        """Execute a single step: resolve action, run, report status."""
        from .ws_client import StepLogger

        step_name = step_def.get("name", f"step_{idx}")
        action_ref = step_def.get("action", "")
        params = step_def.get("params", {})
        timeout = step_def.get("timeout", 300)

        # Create step logger
        step_id = step_def.get("_db_step_id", 0)
        log_file = None
        if self._log_dir:
            import re

            safe_name = re.sub(r"[^\w\-]", "_", step_name)
            log_file = os.path.join(self._log_dir, f"step_{idx:02d}_{safe_name}.log")

        step_logger = (
            StepLogger(self._ws, self._run_id, step_id, log_file=log_file)
            if self._ws
            else None
        )

        # Report step RUNNING
        self._report_step_status(step_id, "RUNNING", started_at=datetime.utcnow())

        if step_logger:
            step_logger.info(f"=== Step: {step_name} ({action_ref}) ===")

        # Build step context
        ctx = StepContext(
            adb=self._adb,
            serial=self._serial,
            params=params,
            run_id=self._run_id,
            step_id=step_id,
            logger=step_logger,
            shared=self._shared,
        )

        # Resolve and execute action
        start_time = time.time()
        try:
            action_fn = self._resolve_action(action_ref)
            if action_fn is None:
                result = StepResult(
                    success=False,
                    exit_code=1,
                    error_message=f"Unknown action: {action_ref}",
                )
            else:
                # Execute with timeout
                result = self._run_with_timeout(action_fn, ctx, timeout)
        except Exception as e:
            result = StepResult(success=False, exit_code=1, error_message=str(e))

        elapsed = time.time() - start_time

        # Report step completion
        status = "COMPLETED" if result.success else "FAILED"
        self._report_step_status(
            step_id,
            status,
            finished_at=datetime.utcnow(),
            exit_code=result.exit_code,
            error_message=result.error_message if not result.success else None,
        )

        if step_logger:
            step_logger.info(f"=== Step {step_name} {status} ({elapsed:.1f}s) ===")

        # Store metrics in shared context
        if result.metrics:
            self._shared[step_name] = result.metrics

        return result

    def _resolve_action(self, action_ref: str) -> Optional[Callable]:
        """Resolve an action reference to a callable (legacy phases format)."""
        if action_ref.startswith("builtin:"):
            action_name = action_ref[len("builtin:") :]
            try:
                from .actions import ACTION_REGISTRY

                return ACTION_REGISTRY.get(action_name)
            except ImportError:
                logger.error("Cannot import ACTION_REGISTRY")
                return None
        elif action_ref.startswith("tool:"):
            # Legacy phases format: tool: actions are not supported without ToolRegistry
            return lambda ctx: StepResult(
                success=False,
                exit_code=1,
                error_message="tool: actions require stages format with ToolRegistry",
            )
        elif action_ref.startswith("shell:"):
            command = action_ref[len("shell:") :]
            return lambda ctx: self._run_shell_action(ctx, command)
        return None

    def _run_shell_action(self, ctx: StepContext, command: str) -> StepResult:
        """Execute an ADB shell command as a pipeline step."""
        try:
            result = ctx.adb.shell(
                ctx.serial, command, timeout=ctx.params.get("timeout", 30)
            )
            if ctx.logger:
                for line in (result.stdout or "").splitlines():
                    ctx.logger.info(line)
                if result.stderr:
                    for line in result.stderr.splitlines():
                        ctx.logger.warn(line)
            return StepResult(success=True, exit_code=0)
        except Exception as e:
            return StepResult(success=False, exit_code=1, error_message=str(e))

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

    def _retry_step(
        self, phase_name: str, idx: int, step_def: dict, max_retries: int
    ) -> StepResult:
        """Retry a failed step up to max_retries times."""
        for attempt in range(1, max_retries + 1):
            logger.info(
                f"[Pipeline] Retrying step '{step_def.get('name')}' (attempt {attempt}/{max_retries})"
            )
            time.sleep(5)  # Fixed delay between retries
            result = self._execute_step(phase_name, idx, step_def)
            if result.success:
                step_def["_failed"] = False
                return result
        return StepResult(
            success=False,
            exit_code=1,
            error_message=f"Step failed after {max_retries} retries",
        )

    def _mark_remaining_canceled(self, steps: list, start_idx: int):
        """Mark remaining steps as CANCELED."""
        for idx in range(start_idx, len(steps)):
            step_id = steps[idx].get("_db_step_id", 0)
            self._report_step_status(step_id, "CANCELED")

    def _cancel_remaining_phases(self, phases: list, start_phase_idx: int):
        """Cancel all steps in remaining phases (fix: on_failure=stop cascade)."""
        for phase_idx in range(start_phase_idx, len(phases)):
            phase = phases[phase_idx]
            steps = phase.get("steps", [])
            for step_def in steps:
                step_id = step_def.get("_db_step_id", 0)
                if step_id:
                    self._report_step_status(step_id, "CANCELED")

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

    def _emit_fold_marker(
        self,
        kind: str,
        phase_name: str,
        step_count: int,
        parallel: bool,
        failed: bool = False,
    ):
        """Emit ANSI fold markers for frontend log grouping.

        Protocol (VS Code-style OSC 633):
          Start: \\x1b]633;A\\x07<title>
          End:   \\x1b]633;B\\x07
        """
        if not self._ws or not self._ws.connected:
            return

        try:
            from .ws_client import StepLogger

            # Use step_id=0 as fold markers are phase-level, not step-level
            fold_logger = StepLogger(self._ws, self._run_id, 0)
            if kind == "start":
                mode = "parallel" if parallel else "serial"
                title = f"Phase: {phase_name} ({step_count} steps, {mode})"
                fold_logger.info(f"\x1b]633;A\x07{title}")
            else:
                status = "FAILED" if failed else "OK"
                fold_logger.info(f"\x1b]633;B\x07Phase {phase_name} {status}")
        except Exception:
            pass  # Fold markers are non-critical

    # ==================================================================
    # Stages-format execution (stp-spec §2)
    # ==================================================================

    def _execute_stages_format(self, pipeline_def: dict) -> StepResult:
        """Execute new stages-format pipeline: prepare → execute → post_process (serial)."""
        # Replace {log_dir} placeholders in pipeline params
        if self._log_dir:
            raw = json.dumps(pipeline_def)
            raw = raw.replace("{log_dir}", self._log_dir.replace("\\", "/"))
            pipeline_def = json.loads(raw)

        stages_def = pipeline_def.get("stages", {})

        for stage_name in ("prepare", "execute", "post_process"):
            steps = stages_def.get(stage_name, [])
            for step in steps:
                success = self._run_step_with_retry_stages(stage_name, step)
                if not success:
                    step_id = step.get("step_id", "unknown")
                    self._report_job_status_mq(
                        "FAILED", reason=f"step_failed:{step_id}"
                    )
                    return StepResult(
                        success=False,
                        exit_code=1,
                        error_message=f"step failed in {stage_name}: {step_id}",
                    )

        self._report_job_status_mq("COMPLETED")
        artifact = self._archive_logs()
        return StepResult(success=True, artifact=artifact)

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
        self._report_step_trace_mq(
            step_id,
            stage,
            event_type,
            "COMPLETED" if result.success else "FAILED",
            error_message=result.error_message if not result.success else None,
        )

        # Store metrics in shared context (mirrors legacy _execute_step behavior)
        if result.metrics:
            self._shared[step_id] = result.metrics

        return result

    def _resolve_action_stages(self, action: str, step: dict) -> Optional[Callable]:
        """Resolve action for stages format. tool: uses ToolRegistry; no shell: allowed."""
        if action.startswith("builtin:"):
            action_name = action[len("builtin:") :]
            try:
                from .actions import ACTION_REGISTRY

                return ACTION_REGISTRY.get(action_name)
            except ImportError:
                logger.error("Cannot import ACTION_REGISTRY")
                return None

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

        return None

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
        """Dynamically load and execute a tool script from its local path."""
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
        """Create a simple logger that writes to MQ stp:logs and a local file."""
        return _MQStepLogger(
            mq_producer=self._mq,
            run_id=self._run_id,
            step_id_str=step_id,
            log_file=log_file,
        )


class _MQStepLogger:
    """Lightweight logger that sends lines to stp:logs via MQ and optionally to a file."""

    def __init__(
        self, mq_producer, run_id: int, step_id_str: str, log_file: Optional[str] = None
    ):
        self._mq = mq_producer
        self._run_id = run_id
        self._step_id = step_id_str
        self._log_file = log_file
        if log_file:
            try:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
            except Exception:
                pass

    def _write(self, message: str, level: str) -> None:
        if self._mq and self._mq.connected:
            self._mq.send_log(
                job_id=self._run_id,
                device_id=0,
                level=level,
                tag=self._step_id,
                message=message,
            )
        if self._log_file:
            try:
                ts = datetime.utcnow().isoformat() + "Z"
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
