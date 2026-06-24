"""Pipeline execution engine for the agent.



Parses lifecycle-format pipeline definitions and executes script steps:

init -> patrol loop -> teardown.

"""



import hashlib

import json

import logging

import os

import shutil

import signal

import subprocess

import sys

import tarfile

import time

from concurrent.futures import ThreadPoolExecutor, as_completed

from dataclasses import dataclass, field

from datetime import datetime, timedelta, timezone

from typing import Any, Callable, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)



_MAX_STEP_OUTPUT_CHARS = 64 * 1024


_IS_WINDOWS = sys.platform == "win32"

# Windows 上 signal.SIGKILL 不存在;只在 POSIX 分支会访问到,但模块在 Windows 上
# 也要能 import + 单测 monkeypatch。Fallback 到 SIGTERM 仅作占位,运行期不会走到。
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _popen_isolation_kwargs() -> Dict[str, Any]:
    """#3: 跨平台 process group 隔离 — 让超时 kill 能覆盖孙进程。

    Why: 脚本(python/sh/bat) fork 出的 adb/monkey/uiautomator 等子进程,
         若不在自己的进程组内,父进程被 SIGKILL 后会变孤儿继续占用 Android 设备
         和 adb 端口,后续 patrol 步骤会因设备状态污染而连续失败。
    How to apply: POSIX 用 start_new_session=True (等价 preexec_fn=os.setsid);
                  Windows 用 CREATE_NEW_PROCESS_GROUP — 配合 taskkill /T 杀整树。
    """
    if _IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _terminate_process_tree(proc: subprocess.Popen, *, grace_seconds: float = 2.0) -> None:
    """#3: 跨平台 kill 进程树 — POSIX 走 killpg(SIGTERM → wait → SIGKILL),
    Windows 走 taskkill /T /F。

    Why: proc.kill() 只发 SIGKILL 给 pid;若我们靠 _popen_isolation_kwargs 起了
         新进程组,必须用 killpg / taskkill /T 才能扫到组内所有孙进程。
    How to apply: 已退出 proc 直接返回;ProcessLookupError 吞掉(race 状态正常);
                  taskkill 失败兜底 proc.kill()。
    """
    if proc.poll() is not None:
        return

    if _IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:
            logger.warning("taskkill_failed pid=%d falling back to proc.kill", proc.pid)
            try:
                proc.kill()
            except Exception:
                pass
        return

    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    except Exception:
        pgid = proc.pid

    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        logger.exception("killpg_sigterm_failed pgid=%d", pgid)

    try:
        proc.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass

    try:
        os.killpg(pgid, _SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        logger.exception("killpg_sigkill_failed pgid=%d", pgid)





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

    # LocalDB instance for cross-run persistent state (e.g., watcher AEE dedup state)

    local_db: Any = None

    log_dir: str = ""

    adb_path: str = ""

    nfs_root: str = ""



    @property

    def job_id(self) -> int:

        """Alias for run_id — new code should use ctx.job_id.



        The underlying field remains run_id for backward compatibility with

        existing lifecycle steps and tests (20+ call sites). A future governance

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





class PipelineEngine:

    """Executes a pipeline definition: phase-serial, intra-phase parallel."""



    def __init__(

        self,

        adb,

        serial: str,

        run_id: int,

        log_dir: Optional[str] = None,

        mq_producer=None,

        script_registry=None,

        local_db=None,

        api_url: Optional[str] = None,

        agent_secret: str = "",

        nfs_root: Optional[str] = None,

        is_aborted: Optional[Callable[[], bool]] = None,

        fencing_token: Optional[str] = None,

        patrol_heartbeat_uploader=None,  # ADR-0022

        watcher_capability: Optional[str] = None,

        patrol_cycle_checkpoint_store=None,

    ):

        self._adb = adb

        self._serial = serial

        self._run_id = run_id

        self._log_dir = log_dir

        self._mq = mq_producer

        self._script_registry = script_registry

        self._local_db = local_db

        self._api_url = api_url

        self._agent_secret = agent_secret

        self._adb_path = getattr(adb, "adb_path", os.getenv("ADB_PATH", "adb"))

        self._nfs_root = nfs_root if nfs_root is not None else os.getenv("STP_NFS_ROOT", "")

        self._is_aborted = is_aborted

        self._fencing_token = fencing_token or ""  # ADR-0019 Phase 2b

        self._patrol_heartbeat = patrol_heartbeat_uploader  # ADR-0022

        self._watcher_capability = watcher_capability or None

        self._patrol_cycle_checkpoint_store = patrol_cycle_checkpoint_store

        self._patrol_cycle_checkpoint: Optional[dict] = None

        self._patrol_cycle_resume: bool = False

        self._shared: dict = {}

        self._canceled = False



    def set_patrol_cycle_resume(self, checkpoint: dict) -> None:

        """Restore patrol loop from a persisted checkpoint (agent crash recovery)."""

        self._patrol_cycle_checkpoint = checkpoint

        self._patrol_cycle_resume = True



    def clear_patrol_cycle_checkpoint(self) -> None:

        self._patrol_cycle_checkpoint = None

        self._patrol_cycle_resume = False

        self._drop_patrol_cycle_checkpoint_row()



    def _persist_patrol_cycle_checkpoint(self, payload: dict) -> None:

        store = self._patrol_cycle_checkpoint_store

        if store is None:

            return

        try:

            store.save(str(self._run_id), payload)

        except Exception as exc:

            from .registry.patrol_checkpoint_store import (

                PatrolCycleCheckpointStoreRecoverableError,

            )

            if isinstance(exc, PatrolCycleCheckpointStoreRecoverableError):

                logger.warning(

                    "patrol_checkpoint_save_failed job_id=%s: %s",

                    self._run_id,

                    exc,

                )

            else:

                raise



    def _drop_patrol_cycle_checkpoint_row(self) -> None:

        store = self._patrol_cycle_checkpoint_store

        if store is None:

            return

        try:

            store.drop(str(self._run_id))

        except Exception as exc:

            from .registry.patrol_checkpoint_store import (

                PatrolCycleCheckpointStoreRecoverableError,

            )

            if isinstance(exc, PatrolCycleCheckpointStoreRecoverableError):

                logger.warning(

                    "patrol_checkpoint_drop_failed job_id=%s: %s",

                    self._run_id,

                    exc,

                )

            else:

                raise



    def cancel(self):

        """Signal cancellation to the engine."""

        self._canceled = True



    def execute(self, pipeline_def: dict) -> StepResult:

        """Execute the full lifecycle pipeline."""

        # Verify device lock is held before executing

        lock_err = self._verify_device_lease()

        if lock_err:

            return lock_err



        if "stages" in pipeline_def:

            return StepResult(

                success=False,

                exit_code=1,

                error_message="stages format is not supported; use 'lifecycle'",

            )



        if "phases" in pipeline_def:

            return StepResult(

                success=False,

                exit_code=1,

                error_message="legacy phases format is not supported; use 'lifecycle'",

            )



        if "lifecycle" in pipeline_def:

            return self._execute_lifecycle(pipeline_def)



        return StepResult(

            success=False,

            exit_code=1,

            error_message="pipeline_def must contain 'lifecycle'",

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

                # 审计 #2: 5xx 视为瞬态,进入重试;其它 4xx (非 409/401) 视为契约错误立即 fail。
                # Why: 服务端瞬断会让 long-running patrol 立刻 abort,代价过大。
                # How to apply: 与 _extend_lock 同口径退避;最终耗尽再返回失败。
                status_code = resp.status_code if resp is not None else None
                if status_code is not None and 500 <= status_code < 600:
                    logger.warning(
                        "lock_verify_attempt_%d_failed_5xx run=%d status=%s",
                        attempt, self._run_id, status_code,
                    )
                    if attempt < len(retry_delays):
                        time.sleep(delay)
                        continue
                    return StepResult(
                        success=False,
                        exit_code=1,
                        error_message=f"lock_verification_http_{status_code}",
                    )

                logger.error("lock_verification_failed run=%d status=%s", self._run_id, status_code)

                return StepResult(

                    success=False,

                    exit_code=1,

                    error_message=f"lock_verification_http_{status_code}",

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

        """Run an action that performs its own timeout cleanup.


        """

        del timeout
        result = action_fn(ctx)
        if result is None:
            return StepResult(
                success=False, exit_code=1, error_message="Action returned no result"
            )
        return result




    # ==================================================================

    # Lifecycle step execution

    # ==================================================================



    def _run_lifecycle_steps(self, phase: str, steps: List[dict]) -> StepResult:

        """Execute one lifecycle phase without terminal side effects."""

        for step in steps or []:

            # Check for lock lost (LeaseRenewer removed us from active set)

            if self._is_lock_lost():

                return StepResult(

                    success=False,

                    exit_code=1,

                    error_message="device_lease_lost",

                )



            success = self._run_step_with_retry(phase, step)

            if not success:

                step_id = step.get("step_id", "unknown")

                return StepResult(

                    success=False,

                    exit_code=1,

                    error_message=f"step failed in {phase}: {step_id}",

                )



        return StepResult(success=True)



    def _is_lock_lost(self) -> bool:

        """Check if the run has been aborted (e.g. LeaseRenewer received 409)."""

        if self._is_aborted is not None:

            return self._is_aborted()

        return False



    def _run_step_with_retry(self, phase: str, step: dict) -> bool:

        """Execute a step with retry logic. Returns True on success."""

        max_retry = step.get("retry", 0)

        for attempt in range(max_retry + 1):

            result = self._execute_step(phase, step)

            if result.success:

                return True

            if attempt < max_retry:

                time.sleep(5 * (attempt + 1))

        return False



    def _execute_step(

        self,

        phase: str,

        step: dict,

        *,

        suppress_success_trace: bool = False,

    ) -> StepResult:

        """Execute a single lifecycle step. Reports STARTED/COMPLETED/FAILED via MQ.



        ADR-0022: when ``suppress_success_trace=True`` (used for patrol stage

        success-path), the STARTED + COMPLETED traces are skipped to avoid

        per-cycle volume blow-up.  Failure / SKIPPED traces are always written.

        """

        step_id = step.get("step_id", "unknown")

        action = step.get("action", "")

        params = step.get("params", {})

        timeout = step.get("timeout_seconds", step.get("timeout", 300))



        if step.get("enabled") is False:

            result = StepResult(success=True, skipped=True, skip_reason="step disabled")

            # Always trace SKIPPED — meaningful signal even in patrol.

            self._report_step_trace_mq(

                step_id,

                phase,

                "COMPLETED",

                "SKIPPED",

                output=result.skip_reason,

            )

            return result



        if not suppress_success_trace:

            self._report_step_trace_mq(step_id, phase, "STARTED", "RUNNING")



        log_file = None

        if self._log_dir:

            import re



            safe = re.sub(r"[^\w\-]", "_", step_id)

            log_file = os.path.join(self._log_dir, f"{phase}_{safe}.log")



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

            action_fn = self._resolve_action(action, step)

            if action_fn is None:

                result = StepResult(

                    success=False,

                    exit_code=1,

                    error_message=f"Unsupported action: {action}; only script:<name> is supported",

                )

            else:

                result = self._run_with_timeout(action_fn, ctx, timeout)

        except Exception as e:

            result = StepResult(success=False, exit_code=1, error_message=str(e))



        event_type = "COMPLETED" if result.success else "FAILED"

        status = "SKIPPED" if result.success and result.skipped else (

            "COMPLETED" if result.success else "FAILED"

        )



        # ADR-0022: skip COMPLETED trace for patrol-success path; FAILED always traces.

        skip_completion_trace = (

            suppress_success_trace

            and result.success

            and not result.skipped  # SKIPPED still traces (rare, meaningful)

        )

        if not skip_completion_trace:

            self._report_step_trace_mq(

                step_id,

                phase,

                event_type,

                status,

                output=result.skip_reason if result.skipped else (result.output or None),

                error_message=result.error_message if not result.success else None,

            )



        # Store metrics in shared context (mirrors legacy _execute_step behavior)

        if result.metrics:

            self._shared[step_id] = result.metrics



        return result



    def _resolve_action(self, action: str, step: dict) -> Optional[Callable]:

        """Resolve supported lifecycle actions."""

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

            "STP_SHARED_METRICS": json.dumps(self._shared, ensure_ascii=False),

        })

        if self._local_db is not None:
            db_path = getattr(self._local_db, "_db_path", None)
            if db_path:
                env["STP_AGENT_STATE_DB"] = str(db_path)

        try:
            from .config import BASE_DIR
            env["STP_AGENT_INSTALL_DIR"] = str(BASE_DIR)
        except Exception:
            pass



        timeout_seconds = step.get("timeout_seconds", step.get("timeout", 300))

        try:

            proc = subprocess.Popen(

                cmd,

                env=env,

                stdout=subprocess.PIPE,

                stderr=subprocess.PIPE,

                text=True,

                cwd=os.path.dirname(entry.nfs_path) or None,

                **_popen_isolation_kwargs(),

            )

            try:

                stdout, stderr = proc.communicate(timeout=timeout_seconds)

            except subprocess.TimeoutExpired:

                _terminate_process_tree(proc)

                stdout, stderr = proc.communicate()

                combined_output = "\n".join(
                    part for part in ((stdout or "").strip(), (stderr or "").strip()) if part
                )

                return StepResult(

                    success=False,

                    exit_code=124,

                    error_message="script timeout",

                    output=_truncate_step_output(combined_output),

                )

        except Exception as exc:

            return StepResult(success=False, exit_code=1, error_message=str(exc))

        stdout = stdout or ""

        stderr = stderr or ""

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

        """Send step_trace via MQ (local_db → StepTraceUploader HTTP batch)."""

        if self._mq and self._mq.connected:

            self._mq.send_step_trace(

                job_id=self._run_id,

                step_id=step_id,

                stage=stage,

                event_type=event_type,

                status=status,

                output=output,

                error_message=error_message,

                fencing_token=self._fencing_token,

            )



    def _report_job_status_mq(self, status: str, reason: str = "") -> None:

        """Send job_status event via MQ."""

        if self._mq and self._mq.connected:

            self._mq.send_job_status(self._run_id, status, reason)



    def _make_mq_logger(self, step_id: str, log_file: Optional[str] = None):

        """Create a logger that writes to MQ and a local file."""

        return _MQStepLogger(

            mq_producer=self._mq,

            run_id=self._run_id,

            step_id_str=step_id,

            log_file=log_file,

        )



    # ==================================================================

    # Lifecycle execution (init → patrol loop → teardown)

    # ==================================================================



    def _execute_lifecycle(self, pipeline_def: dict) -> StepResult:

        """Execute a lifecycle pipeline: init → patrol_loop → teardown (best-effort).



        The lifecycle key contains direct script step lists for init and

        teardown, and an optional patrol object with interval_seconds + steps.

        Patrol runs in a loop until a termination condition is met. Teardown

        always runs via try/finally.



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



        patrol_resume: Optional[dict] = None

        skip_init = False

        if self._patrol_cycle_resume and self._patrol_cycle_checkpoint is not None:

            patrol_resume = self._patrol_cycle_checkpoint

            skip_init = True



        try:

            # ── Phase 1: Init ──

            if skip_init:

                logger.info(

                    "[Lifecycle] run=%d — skipping init (patrol checkpoint resume)",

                    self._run_id,

                )

                init_result = StepResult(success=True, exit_code=0)

            else:

                self._report_job_status_mq("INIT_RUNNING")

                logger.info("[Lifecycle] run=%d — executing init", self._run_id)



                init_result = self._run_lifecycle_steps("init", init_def)

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

                # ADR-0022: per-cycle aggregate via patrol_heartbeat_uploader,

                # exponential backoff on consecutive failure, manual_action

                # observation for runtime intervention.

                termination_reason, lifecycle_error = self._run_patrol_loop(

                    patrol_def, timeout_seconds, init_completed_at=time.time(),

                    resume=patrol_resume,

                )



        finally:

            # ── Phase 3: Teardown (best-effort) ──

            # ADR-0022 BO4: manual_exit skips teardown entirely; the device

            # is reclaimed via lease release + the next Plan's init re-checks.

            if termination_reason == "manual_exit":

                logger.info(

                    "[Lifecycle] run=%d — manual_exit: skipping teardown (ADR-0022 BO4)",

                    self._run_id,

                )

                teardown_result = None

            else:

                self._report_job_status_mq("TEARDOWN_RUNNING", reason=f"termination_reason={termination_reason}")

                logger.info("[Lifecycle] run=%d — executing teardown (reason: %s)", self._run_id, termination_reason)

                teardown_result = self._execute_teardown_best_effort(teardown_def)



        # ── Unified exit: terminal MQ + artifact + StepResult ──

        success = termination_reason in ("completed", "timeout")

        artifact = self._archive_logs()



        # Map termination_reason to MQ terminal status

        if success:

            mq_status = "COMPLETED"

        elif termination_reason in ("abort", "manual_exit"):

            mq_status = "ABORTED"

        else:

            mq_status = "FAILED"



        self._report_job_status_mq(

            mq_status,

            reason=f"termination_reason={termination_reason}",

        )



        # Merge teardown metadata into final result

        final_metadata = {"termination_reason": termination_reason}

        if teardown_result is None:

            # ADR-0022 BO4: manual_exit explicitly skipped teardown

            final_metadata["teardown_status"] = "SKIPPED"

        elif isinstance(teardown_result.metadata, dict):

            final_metadata["teardown_status"] = teardown_result.metadata.get("teardown_status", "UNKNOWN")



        return StepResult(

            success=success,

            exit_code=0 if success else 1,

            error_message="" if success else (lifecycle_error or f"lifecycle ended: {termination_reason}"),

            artifact=artifact,

            metadata=final_metadata,

        )



    # ==================================================================

    # ADR-0022: Patrol loop with heartbeat aggregation + backoff +

    #           manual_action observation

    # ==================================================================



    def _run_patrol_loop(

        self,

        patrol_def: dict,

        timeout_seconds: int,

        *,

        init_completed_at: float,

        resume: Optional[dict] = None,

    ) -> Tuple[str, str]:

        """Execute the patrol stage with ADR-0022 semantics.



        Returns ``(termination_reason, lifecycle_error)`` in the same format

        the caller expects: termination_reason ∈ {completed, timeout, abort,

        patrol_failure, manual_exit}.



        Per-cycle behavior (vs. legacy "fail-fast on any step"):

          1. Run all patrol steps best-effort (single-step failure no longer

             aborts the cycle).  Failures are still traced; successes are not

             (suppress_success_trace=True).

          2. Aggregate ``success_delta`` / ``failed_delta`` per cycle and POST

             /patrol-heartbeat to the server.  Server returns pending

             ``manual_action`` so we can short-circuit sleep / exit patrol.

          3. If any step failed, increment ``failure_streak`` and compute

             ``backoff_seconds`` via exponential formula (D4).  If

             ``failure_streak == 0`` (cycle clean), reset and use the

             configured ``interval_seconds`` instead.

          4. If ``manual_action == EXIT_REQUESTED`` from any heartbeat,

             return termination_reason='manual_exit' and skip teardown

             (BO4: handled in the caller's finally / unified-exit block by

             treating manual_exit identically to abort).

        """

        from .patrol_heartbeat_uploader import compute_backoff_seconds



        interval = patrol_def.get("interval_seconds", 300)

        backoff_policy = patrol_def.get("backoff_policy") or {}

        backoff_base = float(backoff_policy.get("base_seconds", 60.0))

        backoff_growth = float(backoff_policy.get("growth_factor", 2.0))

        backoff_max = float(backoff_policy.get("max_interval_seconds", 3600.0))



        steps = patrol_def.get("steps", [])

        iteration = 0

        failure_streak = 0

        last_lease_verify = 0.0

        last_observed_action: Optional[str] = None

        _LEASE_REVERIFY_INTERVAL = 300



        if resume:

            iteration = int(resume.get("cycle", 0))

            failure_streak = int(resume.get("failure_streak", 0))

            last_observed_action = resume.get("last_observed_action")

            logger.info(

                "[Lifecycle] run=%d — resuming patrol from cycle=%d streak=%d",

                self._run_id,

                iteration,

                failure_streak,

            )



        def _end_patrol(reason: str, err: str = "") -> Tuple[str, str]:

            self._drop_patrol_cycle_checkpoint_row()

            return reason, err



        self._report_job_status_mq("PATROL_RUNNING")

        termination_reason = "completed"

        lifecycle_error = ""



        while True:

            # ── Termination checks before each cycle ──

            if self._is_lock_lost() or self._canceled:

                logger.info("[Lifecycle] run=%d — abort detected, ending patrol loop", self._run_id)

                return _end_patrol("abort", "")



            if timeout_seconds > 0 and (time.time() - init_completed_at) >= timeout_seconds:

                logger.info("[Lifecycle] run=%d — timeout reached (%ds), ending patrol loop", self._run_id, timeout_seconds)

                return _end_patrol("timeout", "")



            if last_observed_action == "EXIT_REQUESTED":

                logger.info("[Lifecycle] run=%d — manual EXIT_REQUESTED observed, ending patrol", self._run_id)

                return _end_patrol("manual_exit", "")



            # Periodic lease re-verification: defense-in-depth

            if time.time() - last_lease_verify > _LEASE_REVERIFY_INTERVAL:

                lock_err = self._verify_device_lease()

                if lock_err:

                    logger.error("[Lifecycle] run=%d — lease lost during patrol", self._run_id)

                    return _end_patrol("abort", f"lease re-verification failed: {lock_err.error_message}")

                last_lease_verify = time.time()



            iteration += 1

            logger.info("[Lifecycle] run=%d — [Patrol #%d] starting (streak=%d)", self._run_id, iteration, failure_streak)



            # ── Run all steps best-effort, collect success/failure aggregate ──

            success_count, failed_count, last_failed_step = self._run_patrol_cycle_steps(steps)



            # If lease was lost mid-cycle, _run_patrol_cycle_steps surfaces it

            # via the canceled flag; check explicitly.

            if self._is_lock_lost() or self._canceled:

                return _end_patrol("abort", "")



            had_failure = failed_count > 0

            if had_failure:

                failure_streak += 1

                next_sleep = compute_backoff_seconds(

                    failure_streak,

                    base_seconds=backoff_base,

                    growth_factor=backoff_growth,

                    max_seconds=backoff_max,

                )

                logger.warning(

                    "[Lifecycle] run=%d — [Patrol #%d] %d/%d steps failed (last=%s), backoff=%.0fs streak=%d",

                    self._run_id, iteration, failed_count, success_count + failed_count,

                    last_failed_step or "?", next_sleep, failure_streak,

                )

            else:

                failure_streak = 0

                next_sleep = float(interval)



            # Compute next_retry_at for server-side display

            next_retry_dt = (

                datetime.now(timezone.utc) + timedelta(seconds=int(next_sleep))

                if next_sleep > 0 else None

            )



            # ── Send heartbeat (best-effort) and observe manual_action ──

            current_step_for_ui = last_failed_step or (steps[-1].get("step_id") if steps else None)

            ack = None

            if self._patrol_heartbeat is not None:

                ack = self._patrol_heartbeat.send(

                    job_id=self._run_id,

                    fencing_token=self._fencing_token,

                    cycle_index=iteration,

                    success_delta=1 if success_count > 0 and not had_failure else 0,

                    failed_delta=1 if had_failure else 0,

                    current_step=current_step_for_ui,

                    current_failure_streak=failure_streak,

                    next_retry_at=next_retry_dt if had_failure else None,

                    watcher_capability=self._watcher_capability,

                    manual_action_observed=last_observed_action,

                )

                if isinstance(ack, dict):

                    if ack.get("_job_not_running"):

                        logger.warning(

                            "[Lifecycle] run=%d — patrol heartbeat rejected JOB_NOT_RUNNING, stopping patrol",

                            self._run_id,

                        )

                        self._canceled = True

                        return _end_patrol("abort", "job_not_running")

                    last_observed_action = ack.get("manual_action") or None



            # ── Status MQ update for live UI ──

            time_elapsed = time.time() - init_completed_at

            time_remaining = max(0, timeout_seconds - time_elapsed) if timeout_seconds > 0 else -1

            self._report_job_status_mq(

                "PATROL_RUNNING",

                reason=(

                    f"iteration={iteration} next_in={int(next_sleep)}s "

                    f"remaining={int(time_remaining)}s streak={failure_streak}"

                ),

            )



            self._persist_patrol_cycle_checkpoint({

                "cycle": iteration,

                "failure_streak": failure_streak,

                "last_failed_step_id": last_failed_step,

                "last_observed_action": last_observed_action,

            })



            # ── Sleep until next cycle, breakable by abort/manual_action ──

            if last_observed_action == "EXIT_REQUESTED":

                logger.info("[Lifecycle] run=%d — manual EXIT_REQUESTED observed post-cycle, ending patrol", self._run_id)

                return _end_patrol("manual_exit", "")

            if last_observed_action == "RETRY_NOW":

                # Skip sleep; loop immediately

                logger.info("[Lifecycle] run=%d — manual RETRY_NOW observed, skipping backoff sleep", self._run_id)

                last_observed_action = None  # consume locally; server-side cleared via next heartbeat

                continue



            sleep_remaining = next_sleep

            while sleep_remaining > 0:

                chunk = min(sleep_remaining, 5.0)

                time.sleep(chunk)

                sleep_remaining -= chunk

                if self._is_lock_lost() or self._canceled:

                    return _end_patrol("abort", "")

                if timeout_seconds > 0 and (time.time() - init_completed_at) >= timeout_seconds:

                    logger.info("[Lifecycle] run=%d — timeout reached during sleep", self._run_id)

                    return _end_patrol("timeout", "")



    def _run_patrol_cycle_steps(self, steps: List[dict]) -> Tuple[int, int, Optional[str]]:

        """ADR-0022: best-effort execution of one patrol cycle.



        Unlike :func:`_run_lifecycle_steps`, a single step failure does NOT

        abort the cycle — we keep running so the cycle's success/failed counts

        are accurate.  Returns ``(success_count, failed_count, last_failed_step_id)``.



        Lease loss is the one exception: it sets ``self._canceled`` so the

        outer loop terminates promptly without finishing the cycle.

        """

        success = 0

        failed = 0

        last_failed: Optional[str] = None

        for step in steps or []:

            if self._is_lock_lost():

                self._canceled = True

                return success, failed, last_failed



            step_id = step.get("step_id", "unknown")

            try:

                result = self._execute_step("patrol", step, suppress_success_trace=True)

            except Exception as exc:

                # Defensive: _execute_step already catches; this is paranoia.

                logger.warning("[Patrol] step %s exception: %s", step_id, exc)

                failed += 1

                last_failed = step_id

                continue



            if result.success:

                success += 1

            else:

                failed += 1

                last_failed = step_id



        return success, failed, last_failed



    def _execute_teardown_best_effort(self, teardown_def: List[dict]) -> StepResult:

        """Execute teardown with best-effort semantics: each step runs independently.



        Returns a StepResult with metadata["teardown_status"]:

        - "SUCCESS" — all steps passed

        - "DEGRADED" — some steps failed but at least one succeeded

        - "FAILED" — all steps failed

        """

        total_steps = 0

        failed_steps = 0

        errors = []



        for step in teardown_def or []:

            total_steps += 1

            step_id = step.get("step_id", "unknown")

            try:

                result = self._execute_step("teardown", step)

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





    def drain_workers(self, grace_seconds: int = 5) -> None:

        """Compatibility no-op: step timeouts now kill the child process inline."""

        del grace_seconds





class _MQStepLogger:

    """Lightweight logger that sends lines via MQ and writes to local file."""



    def __init__(

        self,

        mq_producer,

        run_id: int,

        step_id_str: str,

        log_file: Optional[str] = None,

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
