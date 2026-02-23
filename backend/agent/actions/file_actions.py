"""File transfer pipeline actions: adb_pull, collect_bugreport, scan_aee."""

import logging
import os
from pathlib import Path
from backend.agent.pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def adb_pull(ctx: StepContext) -> StepResult:
    """Pull files/directories from device to host."""
    remote_path = ctx.params.get("remote_path", "")
    local_path = ctx.params.get("local_path", "/tmp/")

    if not remote_path:
        return StepResult(success=False, exit_code=1, error_message="No remote_path specified")

    os.makedirs(local_path, exist_ok=True)

    try:
        ctx.adb.pull(ctx.serial, remote_path, local_path)
        if ctx.logger:
            ctx.logger.info(f"Pulled {remote_path} -> {local_path}")
        return StepResult(success=True, metrics={"remote_path": remote_path, "local_path": local_path})
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"adb pull failed: {e}")


def collect_bugreport(ctx: StepContext) -> StepResult:
    """Generate a bugreport on device and pull it to host."""
    remote_path = ctx.params.get("remote_path", "/sdcard/bugreport.txt")
    local_dir = ctx.params.get("local_dir", "/tmp/")

    os.makedirs(local_dir, exist_ok=True)
    local_path = os.path.join(local_dir, "bugreport.txt")

    try:
        if ctx.logger:
            ctx.logger.info("Generating bugreport (this may take a while)...")
        ctx.adb.shell(ctx.serial, f"bugreport {remote_path}", timeout=300)
        ctx.adb.pull(ctx.serial, remote_path, local_path)
        if ctx.logger:
            ctx.logger.info(f"Bugreport saved to {local_path}")
        return StepResult(success=True, metrics={"local_path": local_path})
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=f"Bugreport failed: {e}")


def scan_aee(ctx: StepContext) -> StepResult:
    """Scan AEE directories on device and pull exception entries to host."""
    aee_dirs = ctx.params.get("aee_dirs", ["/data/aee_exp", "/data/vendor/aee_exp"])
    local_dir = ctx.params.get("local_dir", "/tmp/aee")

    os.makedirs(local_dir, exist_ok=True)

    total_scanned = 0
    total_pulled = 0
    errors = []

    for aee_dir in aee_dirs:
        try:
            ls_output = ctx.adb.shell(ctx.serial, f"ls -1 {aee_dir}/ 2>/dev/null", timeout=10)
            entries = [e.strip() for e in ls_output.strip().splitlines() if e.strip()]
            total_scanned += len(entries)

            for entry in entries:
                remote_entry = f"{aee_dir}/{entry}"
                local_entry = os.path.join(local_dir, entry)
                try:
                    os.makedirs(local_entry, exist_ok=True)
                    ctx.adb.pull(ctx.serial, remote_entry, local_entry)
                    total_pulled += 1
                except Exception as e:
                    errors.append(f"Failed to pull {remote_entry}: {e}")
        except Exception as e:
            logger.debug(f"Cannot list {aee_dir}: {e}")

    if ctx.logger:
        ctx.logger.info(f"AEE scan: {total_scanned} entries found, {total_pulled} pulled")
        if errors:
            ctx.logger.warn(f"AEE pull errors: {len(errors)}")

    return StepResult(
        success=True,
        metrics={"scanned": total_scanned, "pulled": total_pulled, "errors": len(errors)},
    )
