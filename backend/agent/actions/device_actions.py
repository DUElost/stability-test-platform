"""Device-related pipeline actions: check_device, clean_env, push_resources, ensure_root, fill_storage, connect_wifi, install_apk."""

import logging
import subprocess
import time
from backend.agent.pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def check_device(ctx: StepContext) -> StepResult:
    """Verify device is reachable via ADB."""
    try:
        output = ctx.adb.shell(ctx.serial, "echo test", timeout=10)
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
            output = ctx.adb.shell(ctx.serial, f"pm uninstall {pkg}", timeout=30)
            if ctx.logger:
                ctx.logger.info(f"Uninstall {pkg}: {output.strip()}")
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
            output = ctx.adb.shell(ctx.serial, "id -u", timeout=10)
            if output.strip() == "0":
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
            output = ctx.adb.shell(ctx.serial, "id -u", timeout=10)
            if output.strip() == "0":
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
        output = ctx.adb.shell(ctx.serial, "df /data", timeout=10)
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
        output = ctx.adb.shell(ctx.serial, cmd, timeout=30)
        if ctx.logger:
            ctx.logger.info(f"WiFi connect to {ssid}: {output.strip()}")
        return StepResult(success=True, metrics={"ssid": ssid})
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"WiFi connect failed: {e}")


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
