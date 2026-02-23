"""Pipeline execution engine for the agent.

Parses pipeline definitions and executes phases/steps according to the
defined topology: phases run serially, steps within a phase can run
in parallel via ThreadPoolExecutor.
"""

import logging
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


@dataclass
class StepResult:
    """Result returned by each action function."""
    success: bool
    exit_code: int = 0
    error_message: str = ""
    metrics: dict = field(default_factory=dict)


class PipelineEngine:
    """Executes a pipeline definition: phase-serial, intra-phase parallel."""

    def __init__(self, adb, serial: str, run_id: int, ws_client=None, http_fallback=None):
        """
        Args:
            adb: AdbWrapper instance
            serial: Device serial number
            run_id: TaskRun ID
            ws_client: AgentWSClient for real-time log/status streaming
            http_fallback: Callable(run_id, step_id, status, **kwargs) for HTTP fallback
        """
        self._adb = adb
        self._serial = serial
        self._run_id = run_id
        self._ws = ws_client
        self._http_fallback = http_fallback
        self._shared: dict = {}  # Shared data between steps (e.g., PIDs)
        self._canceled = False

    def cancel(self):
        """Signal cancellation to the engine."""
        self._canceled = True

    def execute(self, pipeline_def: dict) -> StepResult:
        """Execute the full pipeline. Returns aggregate result."""
        phases = pipeline_def.get("phases", [])
        if not phases:
            return StepResult(success=False, exit_code=1, error_message="Empty pipeline: no phases defined")

        overall_success = True
        overall_error = ""

        for phase_idx, phase in enumerate(phases):
            if self._canceled:
                logger.info(f"Pipeline canceled before phase '{phase.get('name')}'")
                # Cancel all remaining phases' steps
                self._cancel_remaining_phases(phases, phase_idx)
                break

            phase_name = phase.get("name", "unknown")
            parallel = phase.get("parallel", False)
            steps = phase.get("steps", [])

            logger.info(f"[Pipeline] Starting phase: {phase_name} (parallel={parallel}, steps={len(steps)})")

            # Emit fold group start marker for frontend rendering
            self._emit_fold_marker("start", phase_name, len(steps), parallel)

            if parallel:
                phase_result = self._execute_parallel(phase_name, steps)
            else:
                phase_result = self._execute_serial(phase_name, steps)

            # Emit fold group end marker
            self._emit_fold_marker("end", phase_name, len(steps), parallel, not phase_result.success)

            if not phase_result.success:
                overall_success = False
                overall_error = phase_result.error_message
                # Check if any failed step had on_failure=stop
                if self._should_stop_pipeline(steps, phase_result):
                    logger.info(f"[Pipeline] Phase '{phase_name}' failed with stop policy, aborting pipeline")
                    # Cancel all subsequent phases' steps (fix: #6)
                    self._cancel_remaining_phases(phases, phase_idx + 1)
                    break
                else:
                    logger.info(f"[Pipeline] Phase '{phase_name}' had failures but continuing (on_failure=continue)")

        return StepResult(
            success=overall_success,
            exit_code=0 if overall_success else 1,
            error_message=overall_error,
        )

    def _should_stop_pipeline(self, steps: list, result: StepResult) -> bool:
        """Check if the pipeline should stop based on failure policies."""
        # If any step failed with on_failure=stop, abort
        for step_def in steps:
            if step_def.get("_failed", False) and step_def.get("on_failure", "stop") == "stop":
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
        stop_failures = [s for s in steps if s.get("_failed") and s.get("on_failure", "stop") == "stop"]
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
                                retried = self._retry_step(phase_name, idx, step_def, max_retries)
                                results[idx] = retried
                                if retried.success:
                                    step_def["_failed"] = False
                except Exception as e:
                    step_def["_failed"] = True
                    results[idx] = StepResult(success=False, exit_code=1, error_message=str(e))

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
            logger.warning(f"[Pipeline] Phase '{phase_name}' had {len(continue_error_msgs)} continue-policy failures: {'; '.join(continue_error_msgs)}")

        return StepResult(
            success=not has_stop_failure,
            exit_code=1 if has_stop_failure else 0,
            error_message="; ".join(stop_error_msgs) if stop_error_msgs else "",
        )

    def _execute_step(self, phase_name: str, idx: int, step_def: dict) -> StepResult:
        """Execute a single step: resolve action, run, report status."""
        from backend.agent.ws_client import StepLogger

        step_name = step_def.get("name", f"step_{idx}")
        action_ref = step_def.get("action", "")
        params = step_def.get("params", {})
        timeout = step_def.get("timeout", 300)

        # Create step logger
        step_id = step_def.get("_db_step_id", 0)
        step_logger = StepLogger(self._ws, self._run_id, step_id) if self._ws else None

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
                result = StepResult(success=False, exit_code=1, error_message=f"Unknown action: {action_ref}")
            else:
                # Execute with timeout
                result = self._run_with_timeout(action_fn, ctx, timeout)
        except Exception as e:
            result = StepResult(success=False, exit_code=1, error_message=str(e))

        elapsed = time.time() - start_time

        # Report step completion
        status = "COMPLETED" if result.success else "FAILED"
        self._report_step_status(
            step_id, status,
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
        """Resolve an action reference to a callable."""
        if action_ref.startswith("builtin:"):
            action_name = action_ref[len("builtin:"):]
            try:
                from backend.agent.actions import ACTION_REGISTRY
                return ACTION_REGISTRY.get(action_name)
            except ImportError:
                logger.error(f"Cannot import ACTION_REGISTRY")
                return None
        elif action_ref.startswith("tool:"):
            # Wrap tool execution as an action
            tool_id_str = action_ref[len("tool:"):]
            try:
                tool_id = int(tool_id_str)
            except ValueError:
                return None
            return lambda ctx: self._run_tool_action(ctx, tool_id)
        elif action_ref.startswith("shell:"):
            command = action_ref[len("shell:"):]
            return lambda ctx: self._run_shell_action(ctx, command)
        return None

    def _run_tool_action(self, ctx: StepContext, tool_id: int) -> StepResult:
        """Execute a registered tool as a pipeline step.

        Resolves the tool via tool_snapshot in shared context or falls back
        to run_tool_script action with tool_id.
        """
        # Try to resolve tool_snapshot from pipeline context (injected by main.py)
        tool_snapshot = self._shared.get("_tool_snapshots", {}).get(str(tool_id))
        if tool_snapshot:
            ctx.params.setdefault("tool_id", tool_id)
            for key in ("script_path", "script_class", "default_params", "timeout"):
                if key in tool_snapshot and key not in ctx.params:
                    ctx.params[key] = tool_snapshot[key]

        ctx.params["tool_id"] = tool_id
        try:
            from backend.agent.actions import ACTION_REGISTRY
            run_tool = ACTION_REGISTRY.get("run_tool_script")
            if run_tool:
                return run_tool(ctx)
        except ImportError:
            pass
        return StepResult(success=False, exit_code=1, error_message=f"run_tool_script action not available")

    def _run_shell_action(self, ctx: StepContext, command: str) -> StepResult:
        """Execute an ADB shell command as a pipeline step."""
        try:
            result = ctx.adb.shell(ctx.serial, command, timeout=ctx.params.get("timeout", 30))
            if ctx.logger:
                for line in (result.stdout or "").splitlines():
                    ctx.logger.info(line)
                if result.stderr:
                    for line in result.stderr.splitlines():
                        ctx.logger.warn(line)
            return StepResult(success=True, exit_code=0)
        except Exception as e:
            return StepResult(success=False, exit_code=1, error_message=str(e))

    def _run_with_timeout(self, action_fn: Callable, ctx: StepContext, timeout: int) -> StepResult:
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
                success=False, exit_code=124,
                error_message=f"Step timed out after {timeout}s",
            )

        if error_holder:
            return StepResult(success=False, exit_code=1, error_message=str(error_holder[0]))

        if result_holder:
            return result_holder[0]

        return StepResult(success=False, exit_code=1, error_message="Action returned no result")

    def _retry_step(self, phase_name: str, idx: int, step_def: dict, max_retries: int) -> StepResult:
        """Retry a failed step up to max_retries times."""
        for attempt in range(1, max_retries + 1):
            logger.info(f"[Pipeline] Retrying step '{step_def.get('name')}' (attempt {attempt}/{max_retries})")
            time.sleep(5)  # Fixed delay between retries
            result = self._execute_step(phase_name, idx, step_def)
            if result.success:
                step_def["_failed"] = False
                return result
        return StepResult(success=False, exit_code=1, error_message=f"Step failed after {max_retries} retries")

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

    def _emit_fold_marker(self, kind: str, phase_name: str, step_count: int,
                          parallel: bool, failed: bool = False):
        """Emit ANSI fold markers for frontend log grouping.

        Protocol (VS Code-style OSC 633):
          Start: \\x1b]633;A\\x07<title>
          End:   \\x1b]633;B\\x07
        """
        if not self._ws or not self._ws.connected:
            return

        try:
            from backend.agent.ws_client import StepLogger
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
