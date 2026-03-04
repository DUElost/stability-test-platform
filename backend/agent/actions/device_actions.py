"""Device-related pipeline actions: check_device, clean_env, push_resources, ensure_root, fill_storage, connect_wifi, install_apk."""

import logging
import subprocess
import time
from ..pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def _stdout(result) -> str:
    """Extract stdout string from subprocess.CompletedProcess or plain string."""
    if hasattr(result, "stdout"):
        return result.stdout or ""
    return str(result) if result is not None else ""


def check_device(ctx: StepContext) -> StepResult:
    """Verify device is reachable via ADB."""
    try:
        # 输出设备标识，便于日志页快速确认连接与设备
        if ctx.logger:
            try:
                prop = ctx.adb.shell(ctx.serial, "getprop ro.serialno", timeout=5)
                ctx.logger.info(f"adb_check: serial={ctx.serial}, ro.serialno={_stdout(prop).strip()}")
            except Exception as e:
                ctx.logger.warn(f"adb_check: failed to read ro.serialno: {e}")

        result = ctx.adb.shell(ctx.serial, "echo test", timeout=10)
        output = _stdout(result)
        if "test" in output:
            if ctx.logger:
                ctx.logger.info(f"Device {ctx.serial} is reachable")
            return StepResult(success=True)
        else:
            return StepResult(success=False, exit_code=1, error_message=f"Device check failed: unexpected output '{output}'")
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"Device unreachable: {e}")


def clean_env(ctx: StepContext) -> StepResult:
    """Clean test environment: uninstall packages, clear logs, set properties."""
    errors = []

    # Uninstall packages
    packages = ctx.params.get("uninstall_packages", [])
    for pkg in packages:
        try:
            result = ctx.adb.shell(ctx.serial, f"pm uninstall {pkg}", timeout=30)
            if ctx.logger:
                ctx.logger.info(f"Uninstall {pkg}: {_stdout(result).strip()}")
        except Exception as e:
            if "not installed" not in str(e).lower():
                errors.append(f"Failed to uninstall {pkg}: {e}")
            elif ctx.logger:
                ctx.logger.info(f"Package {pkg} not installed, skipping")

    # Clear logs
    if ctx.params.get("clear_logs", False):
        log_dirs = ctx.params.get("log_dirs", ["/data/aee_exp", "/data/vendor/aee_exp", "/data/debuglogger/mobilelog"])
        for d in log_dirs:
            try:
                ctx.adb.shell(ctx.serial, f"rm -rf {d}/*", timeout=30)
                ctx.adb.shell(ctx.serial, f"mkdir -p {d}", timeout=10)
                if ctx.logger:
                    ctx.logger.info(f"Cleared log directory: {d}")
            except Exception as e:
                errors.append(f"Failed to clear {d}: {e}")

    # Set system properties
    properties = ctx.params.get("set_properties", {})
    for key, value in properties.items():
        try:
            ctx.adb.shell(ctx.serial, f"setprop {key} {value}", timeout=10)
            if ctx.logger:
                ctx.logger.info(f"Set property {key}={value}")
        except Exception as e:
            errors.append(f"Failed to set property {key}: {e}")

    if errors:
        return StepResult(success=False, exit_code=1, error_message="; ".join(errors))
    return StepResult(success=True)


def push_resources(ctx: StepContext) -> StepResult:
    """Push files from host to device."""
    files = ctx.params.get("files", [])
    if not files:
        if ctx.logger:
            ctx.logger.info("No files to push")
        return StepResult(success=True)

    errors = []
    pushed = 0
    for f in files:
        local = f.get("local", "")
        remote = f.get("remote", "")
        if not local or not remote:
            continue
        try:
            ctx.adb.push(ctx.serial, local, remote)
            # Set executable permissions if requested
            if f.get("chmod"):
                ctx.adb.shell(ctx.serial, f"chmod {f['chmod']} {remote}", timeout=10)
            pushed += 1
            if ctx.logger:
                ctx.logger.info(f"Pushed {local} -> {remote}")
        except Exception as e:
            errors.append(f"Failed to push {local}: {e}")

    if errors:
        return StepResult(success=False, exit_code=1, error_message="; ".join(errors))
    return StepResult(success=True, metrics={"files_pushed": pushed})


def ensure_root(ctx: StepContext) -> StepResult:
    """Ensure device has root access via adb root."""
    max_attempts = ctx.params.get("max_attempts", 3)

    for attempt in range(1, max_attempts + 1):
        try:
            result = ctx.adb.shell(ctx.serial, "id -u", timeout=10)
            output = _stdout(result).strip()
            if output == "0":
                if ctx.logger:
                    ctx.logger.info("Device already has root access")
                return StepResult(success=True)

            # Try adb root command (host-side, not shell)
            try:
                subprocess.run(
                    [ctx.adb.adb_path, "-s", ctx.serial, "root"],
                    capture_output=True, text=True, timeout=10,
                )
                time.sleep(3)
            except Exception as e:
                logger.debug(f"adb root command: {e}")

            # Re-check
            result = ctx.adb.shell(ctx.serial, "id -u", timeout=10)
            output = _stdout(result).strip()
            if output == "0":
                if ctx.logger:
                    ctx.logger.info("Root access granted")
                return StepResult(success=True)
        except Exception as e:
            if attempt == max_attempts:
                return StepResult(success=False, exit_code=1, error_message=f"Failed to get root: {e}")
            time.sleep(2)

    return StepResult(success=False, exit_code=1, error_message="Failed to get root access")


def fill_storage(ctx: StepContext) -> StepResult:
    """Fill device storage to a target percentage using dd."""
    target_pct = ctx.params.get("target_percentage", 60)

    try:
        result = ctx.adb.shell(ctx.serial, "df /data", timeout=10)
        output = _stdout(result)
        lines = output.strip().splitlines()
        if len(lines) < 2:
            return StepResult(success=False, exit_code=1, error_message="Cannot parse df output")

        parts = lines[1].split()
        if len(parts) < 4:
            return StepResult(success=False, exit_code=1, error_message="Cannot parse df columns")

        total_kb = int(parts[1])
        used_kb = int(parts[2])
        target_used = total_kb * target_pct // 100
        need_kb = target_used - used_kb

        if need_kb <= 0:
            if ctx.logger:
                ctx.logger.info(f"Storage already at {used_kb * 100 // total_kb}% (target {target_pct}%)")
            return StepResult(success=True, metrics={"already_met": True})

        block_size_kb = 1024
        blocks = max(need_kb // block_size_kb, 1)
        if ctx.logger:
            ctx.logger.info(f"Filling {need_kb}KB ({blocks} blocks) to reach {target_pct}%")

        ctx.adb.shell(
            ctx.serial,
            f"dd if=/dev/zero of=/data/local/tmp/fill.bin bs={block_size_kb}k count={blocks}",
            timeout=300,
        )
        if ctx.logger:
            ctx.logger.info("Storage fill complete")
        return StepResult(success=True, metrics={"filled_kb": need_kb})
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"fill_storage failed: {e}")


def connect_wifi(ctx: StepContext) -> StepResult:
    """Connect device to a WiFi network."""
    ssid = ctx.params.get("ssid", "")
    password = ctx.params.get("password", "")

    if not ssid:
        if ctx.logger:
            ctx.logger.info("No WiFi SSID specified, skipping")
        return StepResult(success=True)

    try:
        ctx.adb.shell(ctx.serial, "svc wifi enable", timeout=10)
        time.sleep(1)
        cmd = f'cmd -w wifi connect-network "{ssid}" wpa2 "{password}"'
        result = ctx.adb.shell(ctx.serial, cmd, timeout=30)
        if ctx.logger:
            ctx.logger.info(f"WiFi connect to {ssid}: {_stdout(result).strip()}")
        return StepResult(success=True, metrics={"ssid": ssid})
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"WiFi connect failed: {e}")


def setup_device_commands(ctx: StepContext) -> StepResult:
    """Execute an ordered list of ADB shell commands for device initialization."""
    commands = ctx.params.get("commands", [])
    if not commands:
        return StepResult(success=True, metrics={"executed": 0, "failed": 0, "errors": []})

    executed = 0
    failed = 0
    errors = []

    for i, cmd_def in enumerate(commands):
        cmd = cmd_def.get("cmd", "")
        timeout = cmd_def.get("timeout", 15)
        on_failure = cmd_def.get("on_failure", "continue")

        if not cmd:
            continue

        try:
            result = ctx.adb.shell(ctx.serial, cmd, timeout=timeout)
            executed += 1
            if ctx.logger:
                output = _stdout(result).strip()
                ctx.logger.info(f"[{i+1}/{len(commands)}] {cmd} -> {output[:200]}")
        except Exception as e:
            failed += 1
            err_msg = f"Command '{cmd}' failed: {e}"
            errors.append(err_msg)
            if ctx.logger:
                ctx.logger.warn(f"[{i+1}/{len(commands)}] {err_msg}")
            if on_failure == "stop":
                return StepResult(
                    success=False,
                    exit_code=1,
                    error_message="; ".join(errors),
                    metrics={"executed": executed, "failed": failed, "errors": errors},
                )

    # All failures were on_failure=continue if we reach here
    return StepResult(
        success=True,
        exit_code=0,
        error_message="; ".join(errors) if errors else "",
        metrics={"executed": executed, "failed": failed, "errors": errors},
    )


def install_apk(ctx: StepContext) -> StepResult:
    """Install an APK on the device via adb install."""
    apk_path = ctx.params.get("apk_path", "")
    reinstall = ctx.params.get("reinstall", True)

    if not apk_path:
        return StepResult(success=False, exit_code=1, error_message="No apk_path specified")

    try:
        flags = ["-r"] if reinstall else []
        cmd = [ctx.adb.adb_path, "-s", ctx.serial, "install"] + flags + [apk_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        output = result.stdout.strip()
        if ctx.logger:
            ctx.logger.info(f"Install APK: {output}")

        if result.returncode != 0 or "Failure" in output:
            return StepResult(success=False, exit_code=1, error_message=f"APK install failed: {output}")
        return StepResult(success=True, metrics={"apk_path": apk_path})
    except subprocess.TimeoutExpired:
        return StepResult(success=False, exit_code=124, error_message="APK install timed out")
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"APK install failed: {e}")
