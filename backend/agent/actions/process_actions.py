"""Process lifecycle pipeline actions: start_process, monitor_process, stop_process, run_instrument."""

import logging
import time
from backend.agent.pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


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
            output = ctx.adb.shell(ctx.serial, full_cmd, timeout=30)
            pid = output.strip().split("\n")[-1].strip()
            if ctx.logger:
                ctx.logger.info(f"Started process (PID={pid}): {command}")
            return StepResult(success=True, metrics={"pid": pid, "command": command})
        else:
            output = ctx.adb.shell(ctx.serial, command, timeout=ctx.params.get("timeout", 300))
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
            ps_output = ctx.adb.shell(ctx.serial, f"kill -0 {pid} 2>&1; echo $?", timeout=10)
            alive = ps_output.strip().endswith("0")
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
                    ls_output = ctx.adb.shell(ctx.serial, f"ls {log_path} 2>/dev/null", timeout=10)
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
        output = ctx.adb.shell(ctx.serial, cmd, timeout=timeout)
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
