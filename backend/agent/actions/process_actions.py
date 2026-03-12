"""Process lifecycle pipeline actions: start_process, monitor_process, stop_process, run_instrument, run_shell_script."""

import logging
import os
import subprocess
import threading
import time
from ..pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def _stdout(result) -> str:
    """Extract stdout string from subprocess.CompletedProcess or plain string."""
    if hasattr(result, "stdout"):
        return result.stdout or ""
    return str(result) if result is not None else ""


def start_process(ctx: StepContext) -> StepResult:
    """Start a process on the device, optionally in background."""
    command = ctx.params.get("command", "")
    if not command:
        return StepResult(success=False, exit_code=1, error_message="No command specified")

    background = ctx.params.get("background", True)

    try:
        if background:
            # Start in background and capture PID
            full_cmd = f"nohup {command} > /dev/null 2>&1 & echo $!"
            result = ctx.adb.shell(ctx.serial, full_cmd, timeout=30)
            output = _stdout(result)
            pid = output.strip().split("\n")[-1].strip()
            if ctx.logger:
                ctx.logger.info(f"Started process (PID={pid}): {command}")
            return StepResult(success=True, metrics={"pid": pid, "command": command})
        else:
            result = ctx.adb.shell(ctx.serial, command, timeout=ctx.params.get("timeout", 300))
            output = _stdout(result)
            if ctx.logger:
                for line in output.splitlines()[:50]:  # Log first 50 lines
                    ctx.logger.info(line)
            return StepResult(success=True, metrics={"output": output[:1000]})
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=str(e))


def monitor_process(ctx: StepContext) -> StepResult:
    """Monitor a running process: check alive, watch log paths, pull errors."""
    pid_from_step = ctx.params.get("pid_from_step", "")
    pid = ctx.shared.get(pid_from_step, {}).get("pid") if pid_from_step else ctx.params.get("pid")

    if not pid:
        return StepResult(success=False, exit_code=1, error_message=f"No PID found (pid_from_step={pid_from_step})")

    check_interval = ctx.params.get("check_interval", 5)
    duration = ctx.params.get("duration", 0)  # 0 = monitor until process exits
    log_paths = ctx.params.get("log_paths", [])
    pull_on_error = ctx.params.get("pull_on_error", True)
    pull_dest = ctx.params.get("pull_dest", "/tmp/")

    start_time = time.time()
    error_files_pulled = []

    if ctx.logger:
        ctx.logger.info(f"Monitoring PID={pid}, interval={check_interval}s, log_paths={log_paths}")

    while True:
        # Check if process is alive
        try:
            ps_result = ctx.adb.shell(ctx.serial, f"kill -0 {pid} 2>&1; echo $?", timeout=10)
            alive = _stdout(ps_result).strip().endswith("0")
        except Exception:
            alive = False

        if not alive:
            if ctx.logger:
                ctx.logger.info(f"Process PID={pid} has exited")
            break

        # Check log paths for error files
        if log_paths and pull_on_error:
            for log_path in log_paths:
                try:
                    ls_result = ctx.adb.shell(ctx.serial, f"ls {log_path} 2>/dev/null", timeout=10)
                    ls_output = _stdout(ls_result)
                    files = [f.strip() for f in ls_output.strip().split("\n") if f.strip()]
                    for f in files:
                        full_path = f"{log_path.rstrip('/')}/{f}"
                        if full_path not in error_files_pulled:
                            error_files_pulled.append(full_path)
                            if ctx.logger:
                                ctx.logger.warn(f"Error file detected: {full_path}")
                            try:
                                ctx.adb.pull(ctx.serial, full_path, pull_dest)
                                if ctx.logger:
                                    ctx.logger.info(f"Pulled: {full_path} -> {pull_dest}")
                            except Exception as e:
                                if ctx.logger:
                                    ctx.logger.warn(f"Failed to pull {full_path}: {e}")
                except Exception:
                    pass

        # Check duration limit
        if duration > 0 and (time.time() - start_time) >= duration:
            if ctx.logger:
                ctx.logger.info(f"Monitor duration reached ({duration}s)")
            break

        time.sleep(check_interval)

    elapsed = time.time() - start_time
    return StepResult(
        success=True,
        metrics={"elapsed_seconds": int(elapsed), "error_files_pulled": len(error_files_pulled)},
    )


def stop_process(ctx: StepContext) -> StepResult:
    """Stop a process by PID."""
    pid_from_step = ctx.params.get("pid_from_step", "")
    pid = ctx.shared.get(pid_from_step, {}).get("pid") if pid_from_step else ctx.params.get("pid")

    if not pid:
        if ctx.logger:
            ctx.logger.info("No PID to stop, skipping")
        return StepResult(success=True)

    try:
        ctx.adb.shell(ctx.serial, f"kill -9 {pid} 2>/dev/null", timeout=10)
        if ctx.logger:
            ctx.logger.info(f"Killed process PID={pid}")
    except Exception as e:
        if ctx.logger:
            ctx.logger.info(f"Kill PID={pid} returned: {e} (process may have already exited)")

    return StepResult(success=True)


def run_instrument(ctx: StepContext) -> StepResult:
    """Run an Android instrumentation test via am instrument."""
    runner = ctx.params.get("runner", "")
    if not runner:
        return StepResult(success=False, exit_code=1, error_message="No runner specified")

    args = ctx.params.get("instrument_args", {})
    timeout = ctx.params.get("timeout", 86400)

    # Build command: am instrument -w [-e key value ...] <runner>
    cmd_parts = ["am", "instrument", "-w"]
    for key, value in args.items():
        cmd_parts.extend(["-e", str(key), str(value)])
    cmd_parts.append(runner)
    cmd = " ".join(cmd_parts)

    if ctx.logger:
        ctx.logger.info(f"Running instrumentation: {cmd}")

    try:
        result = ctx.adb.shell(ctx.serial, cmd, timeout=timeout)
        output = _stdout(result)
        if ctx.logger:
            for line in output.splitlines()[-20:]:
                ctx.logger.info(line)

        success = "OK" in output or "INSTRUMENTATION_STATUS" in output
        return StepResult(
            success=success,
            exit_code=0 if success else 1,
            error_message="" if success else f"Instrument may have failed: {output[-200:]}",
            metrics={"output_tail": output[-500:]},
        )
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"am instrument failed: {e}")


def guard_process(ctx: StepContext) -> StepResult:
    """Check process by name, restart if dead, deduplicate if multiple."""
    process_name = ctx.params.get("process_name", "")
    restart_command = ctx.params.get("restart_command", "")
    pre_restart_commands = ctx.params.get("pre_restart_commands", [])
    max_restarts = ctx.params.get("max_restarts", 3)
    resource_check_path = ctx.params.get("resource_check_path", "")

    if not process_name:
        return StepResult(success=False, exit_code=1, error_message="No process_name specified")

    # 1. pgrep -f to get PID list
    try:
        result = ctx.adb.shell(ctx.serial, f"pgrep -f '{process_name}'", timeout=10)
        raw = _stdout(result).strip()
        pids = [p.strip() for p in raw.splitlines() if p.strip().isdigit()]
    except Exception:
        pids = []

    killed_duplicates = 0

    # 2. Multiple instances: keep first, kill rest
    if len(pids) > 1:
        for extra_pid in pids[1:]:
            try:
                ctx.adb.shell(ctx.serial, f"kill -9 {extra_pid}", timeout=5)
                killed_duplicates += 1
                if ctx.logger:
                    ctx.logger.warn(f"Killed duplicate process PID={extra_pid}")
            except Exception:
                pass
        if ctx.logger:
            ctx.logger.info(f"Process '{process_name}' deduplicated, kept PID={pids[0]}")
        return StepResult(
            success=True,
            metrics={"status": "deduplicated", "pid": pids[0], "restart_count": 0, "killed_duplicates": killed_duplicates},
        )

    # 3. Single instance: alive
    if len(pids) == 1:
        if ctx.logger:
            ctx.logger.info(f"Process '{process_name}' alive, PID={pids[0]}")
        return StepResult(
            success=True,
            metrics={"status": "alive", "pid": pids[0], "restart_count": 0, "killed_duplicates": 0},
        )

    # 4. Zero instances: try restart
    if ctx.logger:
        ctx.logger.warn(f"Process '{process_name}' not found, attempting restart")

    # Resource check
    if resource_check_path:
        try:
            check = ctx.adb.shell(ctx.serial, f"[ -f {resource_check_path} ] && echo exists", timeout=10)
            if "exists" not in _stdout(check):
                if ctx.logger:
                    ctx.logger.error(f"Resource missing: {resource_check_path}")
                return StepResult(
                    success=False,
                    exit_code=1,
                    error_message=f"Resource missing: {resource_check_path}",
                    metrics={"status": "resource_missing", "pid": "", "restart_count": 0, "killed_duplicates": 0},
                )
        except Exception:
            if ctx.logger:
                ctx.logger.error(f"Resource missing: {resource_check_path}")
            return StepResult(
                success=False,
                exit_code=1,
                error_message=f"Resource missing: {resource_check_path}",
                metrics={"status": "resource_missing", "pid": "", "restart_count": 0, "killed_duplicates": 0},
            )

    if not restart_command:
        return StepResult(
            success=False,
            exit_code=1,
            error_message=f"Process '{process_name}' dead and no restart_command provided",
            metrics={"status": "dead_no_restart_cmd", "pid": "", "restart_count": 0, "killed_duplicates": 0},
        )

    # Execute pre-restart commands
    for pre_cmd in pre_restart_commands:
        try:
            ctx.adb.shell(ctx.serial, pre_cmd, timeout=15)
        except Exception as e:
            if ctx.logger:
                ctx.logger.warn(f"Pre-restart command failed (ignored): {pre_cmd}: {e}")

    # Execute restart
    restart_count = 0
    for attempt in range(max_restarts):
        try:
            ctx.adb.shell(ctx.serial, restart_command, timeout=30)
            restart_count += 1
        except Exception as e:
            if ctx.logger:
                ctx.logger.warn(f"Restart command failed: {e}")
            continue

        time.sleep(3)

        # Re-check
        try:
            result = ctx.adb.shell(ctx.serial, f"pgrep -f '{process_name}'", timeout=10)
            raw = _stdout(result).strip()
            new_pids = [p.strip() for p in raw.splitlines() if p.strip().isdigit()]
        except Exception:
            new_pids = []

        if new_pids:
            if ctx.logger:
                ctx.logger.info(f"Process '{process_name}' restarted successfully, PID={new_pids[0]}")
            return StepResult(
                success=True,
                metrics={"status": "restarted", "pid": new_pids[0], "restart_count": restart_count, "killed_duplicates": 0},
            )

    return StepResult(
        success=False,
        exit_code=1,
        error_message=f"Failed to restart '{process_name}' after {restart_count} attempts",
        metrics={"status": "restart_failed", "pid": "", "restart_count": restart_count, "killed_duplicates": 0},
    )


def run_shell_script(ctx: StepContext) -> StepResult:
    """Run a shell script or command on the Agent host (not on Android device).

    This action is designed for migrating existing test scripts to the platform
    with zero code changes. The script receives the device serial via environment
    variables and can call adb directly.

    Params:
        script_path (str): Absolute path to script file on the Agent host.
        command (str): Inline shell command string (mutually exclusive with script_path).
        args (list[str]): Extra arguments appended after script_path.
        env (dict): Additional environment variables to inject.
        timeout (int): Max execution time in seconds. Default: 3600.
        capture_output (bool): Stream stdout/stderr to platform logs. Default: True.
        inject_serial (bool): Auto-inject DEVICE_SERIAL / ADB_SERIAL env vars. Default: True.
        working_dir (str): Working directory for the script. Default: script's parent dir or cwd.

    Injected env vars (always):
        DEVICE_SERIAL   — ADB device serial
        ADB_SERIAL      — same as DEVICE_SERIAL (convenience alias)
        STP_JOB_ID      — current job ID
        STP_LOG_DIR     — log directory path (if available via ctx.params)
    """
    script_path: str = ctx.params.get("script_path", "")
    command: str = ctx.params.get("command", "")
    args: list = ctx.params.get("args", [])
    env_extra: dict = ctx.params.get("env", {})
    timeout: int = int(ctx.params.get("timeout", 3600))
    capture_output: bool = ctx.params.get("capture_output", True)
    inject_serial: bool = ctx.params.get("inject_serial", True)
    working_dir: str = ctx.params.get("working_dir", "")

    if not script_path and not command:
        return StepResult(
            success=False, exit_code=1,
            error_message="run_shell_script: neither 'script_path' nor 'command' specified in params",
        )

    # Build the command
    if script_path:
        cmd = [script_path] + [str(a) for a in args]
        use_shell = False
        cwd = working_dir or os.path.dirname(os.path.abspath(script_path)) or None
    else:
        cmd = command
        use_shell = True
        cwd = working_dir or None

    # Build environment
    env = os.environ.copy()
    if inject_serial and ctx.serial:
        env["DEVICE_SERIAL"] = ctx.serial
        env["ADB_SERIAL"] = ctx.serial
    if ctx.run_id:
        env["STP_JOB_ID"] = str(ctx.run_id)
    log_dir = ctx.params.get("log_dir", "")
    if log_dir:
        env["STP_LOG_DIR"] = log_dir
    env.update({str(k): str(v) for k, v in env_extra.items()})

    cmd_display = cmd if isinstance(cmd, str) else " ".join(str(c) for c in cmd)
    if ctx.logger:
        ctx.logger.info(f"[run_shell_script] exec: {cmd_display}")
        if inject_serial and ctx.serial:
            ctx.logger.info(f"[run_shell_script] DEVICE_SERIAL={ctx.serial}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=cwd,
            shell=use_shell,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return StepResult(
            success=False, exit_code=1,
            error_message=f"Script not found: {script_path or command}",
        )
    except Exception as exc:
        return StepResult(success=False, exit_code=1, error_message=str(exc))

    # Timeout guard: kill the process when deadline exceeded
    timed_out = threading.Event()

    def _kill_on_timeout():
        timed_out.set()
        try:
            proc.kill()
        except Exception:
            pass

    timer = threading.Timer(timeout, _kill_on_timeout)
    timer.start()

    output_lines: list = []
    try:
        for raw_line in proc.stdout:
            line = raw_line.rstrip()
            output_lines.append(line)
            if capture_output and ctx.logger:
                ctx.logger.info(line)
    finally:
        timer.cancel()

    proc.wait()

    if timed_out.is_set():
        if ctx.logger:
            ctx.logger.warn(f"[run_shell_script] timed out after {timeout}s, process killed")
        return StepResult(
            success=False, exit_code=124,
            error_message=f"Script timed out after {timeout}s",
            metrics={"lines": len(output_lines), "timeout": timeout},
        )

    exit_code = proc.returncode
    if ctx.logger:
        ctx.logger.info(f"[run_shell_script] exited with code {exit_code}")

    if exit_code == 0:
        return StepResult(
            success=True, exit_code=0,
            metrics={"lines": len(output_lines)},
        )

    tail = "\n".join(output_lines[-30:]) if output_lines else "(no output)"
    return StepResult(
        success=False,
        exit_code=exit_code,
        error_message=f"Script exited {exit_code}. Last output:\n{tail}",
        metrics={"lines": len(output_lines)},
    )
