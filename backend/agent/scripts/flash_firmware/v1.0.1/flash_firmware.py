"""Flash firmware via SP Flash Tool (MTK platform).

Environment:
    STP_NFS_ROOT         (prepended to relative firmware_dir)
    STP_STEP_PARAMS      (required JSON)
    STP_FLASH_TOOL_DIR   (optional override for flash_tool location)
    STP_JOB_ID           (used to tag metrics)

STP_STEP_PARAMS schema:
    firmware_dir            : str  (required; NFS-relative or absolute)
    da_file                 : str  (required; relative-to-firmware_dir or absolute)
    scatter_file            : str  (required; relative-to-firmware_dir or absolute)
    command                 : str  (optional, default firmware-upgrade)
    boot_mode               : str  (optional, default auto)
    timeout_seconds         : int  (optional, default 1200)
    flash_tool_dir          : str  (optional; overrides STP_FLASH_TOOL_DIR)
    reboot_to_flash         : bool (optional, default true; adb reboot before flash_tool)
    reboot_target           : str  (optional, default "bootloader"; passed to adb reboot)
    pre_reboot_wait_seconds : int  (optional, default 5; sleep after adb reboot)

Output (stdout, single JSON line):
    success/skipped/metrics/error_message per STP script contract.
"""

import json
import os
import platform
import subprocess
import time


_PASS_TOKENS = (
    "All command exec done",
    "All commands are executed successfully",
)
_FAIL_TOKENS = (
    "S_DA_HANDSHAKE_FAILED",
    "S_FT_DOWNLOAD_FAIL",
    "S_NOT_ENOUGH_STORAGE_SPACE",
    "S_FT_FORMAT_FAIL",
    "S_FT_GET_DEV_INFO_FAIL",
    "FAIL",
    "ERROR",
)

_LOCK_PATH = "/tmp/stp-flash-firmware.lock"
_DEFAULT_REL_FLASH_TOOL = (
    "..", "..", "..", "resources", "flashtool",
    "SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100",
)


def _step_params() -> dict:
    raw = os.environ.get("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _output(success: bool, **kwargs) -> None:
    payload = {"success": success, "skipped": False, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))


def _resolve_under(root: str, candidate: str) -> str:
    if os.path.isabs(candidate):
        return candidate
    return os.path.normpath(os.path.join(root, candidate))


def _resolve_firmware_dir(rel: str) -> str:
    if os.path.isabs(rel):
        return rel
    nfs_root = os.environ.get("STP_NFS_ROOT", "")
    if nfs_root:
        return os.path.normpath(os.path.join(nfs_root, rel))
    return rel


def _locate_flash_tool_dir(params_override) -> str:
    if params_override:
        return params_override
    env_override = os.environ.get("STP_FLASH_TOOL_DIR", "")
    if env_override:
        return env_override
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(script_dir, *_DEFAULT_REL_FLASH_TOOL))


def _pick_flash_tool_exe(tool_dir: str):
    names = ("flash_tool", "flash_tool.exe")
    search_roots = [tool_dir, os.path.join(tool_dir, "SP_Flash_Tool_V5")]
    for root in search_roots:
        for name in names:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return path
    return None


def _scan_output_for_verdict(stdout: str, stderr: str):
    combined = "\n".join(s for s in (stdout, stderr) if s)
    upper = combined.upper()
    for tok in _FAIL_TOKENS:
        if tok.upper() in upper:
            return False, f"fail token hit: {tok}"
    for tok in _PASS_TOKENS:
        if tok in combined:
            return True, f"pass token hit: {tok}"
    return False, "no pass token found"


def _acquire_host_lock():
    if platform.system() == "Windows":
        return None
    import fcntl
    lock_fd = open(_LOCK_PATH, "w")
    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
    return lock_fd


def _release_host_lock(lock_fd) -> None:
    if lock_fd is None:
        return
    try:
        lock_fd.close()
    except OSError:
        pass


def _build_subprocess_env(tool_dir: str) -> dict:
    """Mirror flash_tool.sh: prepend tool_dir and tool_dir/lib to LD_LIBRARY_PATH on Linux.

    Without this, flash_tool fails to dlopen libflashtool.so / libQt5Core.so under lib/.
    No-op on Windows (Qt DLLs resolved via PATH or co-located).
    """
    env = os.environ.copy()
    if platform.system() == "Windows":
        return env
    import posixpath
    lib_dir = posixpath.join(tool_dir, "lib")
    prefix = f"{tool_dir}:{lib_dir}"
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{prefix}:{existing}" if existing else prefix
    return env


def _adb_device_state(serial: str, adb_path: str) -> str:
    """Probe ADB get-state; returns 'device', 'offline', 'unauthorized', 'no-device', or 'unknown'."""
    if not serial or not adb_path:
        return "no-device"
    try:
        proc = subprocess.run(
            [adb_path, "-s", serial, "get-state"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"
    if proc.returncode != 0:
        # adb returns non-zero when device isn't visible; stderr usually contains 'not found' / 'no device'
        return "no-device"
    return (proc.stdout or "").strip() or "unknown"


def _reboot_into_flash_mode(serial: str, target: str, adb_path: str, wait_seconds: int) -> dict:
    """Best-effort: ask the device to reboot into <target> via ADB.

    Returns a metrics dict; never raises. flash_tool will USB-poll for the device
    regardless of this outcome — if the device is already in preloader/BROM, the
    adb call simply no-ops (no device visible) and flash_tool takes over.
    """
    result: dict = {"attempted": False, "target": target}
    if not serial:
        result["skip_reason"] = "STP_DEVICE_SERIAL not set"
        return result
    if not adb_path:
        result["skip_reason"] = "STP_ADB_PATH not set"
        return result

    pre_state = _adb_device_state(serial, adb_path)
    result["pre_state"] = pre_state
    # Only "device" means the ADB channel is fully usable. offline / unauthorized /
    # no-device / unknown all mean reboot via adb would either be rejected, hang the
    # 15s best-effort timeout, or produce confusing stderr — let flash_tool USB-poll instead.
    if pre_state != "device":
        result["skip_reason"] = f"device not ready for adb reboot (state={pre_state}); flash_tool will wait on USB"
        return result

    result["attempted"] = True
    try:
        proc = subprocess.run(
            [adb_path, "-s", serial, "reboot", target],
            capture_output=True, text=True, timeout=15,
        )
        result["exit_code"] = proc.returncode
        if proc.returncode != 0:
            result["stderr_tail"] = (proc.stderr or "")[-300:]
    except subprocess.TimeoutExpired:
        result["error"] = "adb reboot timed out after 15s"
    except FileNotFoundError as exc:
        result["error"] = f"adb not found: {exc}"
    except Exception as exc:
        result["error"] = f"adb reboot failed: {exc}"

    if wait_seconds > 0:
        time.sleep(wait_seconds)
        result["waited_seconds"] = wait_seconds
    return result


def main() -> None:
    args = _step_params()

    firmware_dir_raw = args.get("firmware_dir") or ""
    da_file_raw = args.get("da_file") or ""
    scatter_file_raw = args.get("scatter_file") or ""

    if not firmware_dir_raw:
        _output(False, error_message="firmware_dir is required")
        return
    if not da_file_raw:
        _output(False, error_message="da_file is required")
        return
    if not scatter_file_raw:
        _output(False, error_message="scatter_file is required")
        return

    firmware_dir = _resolve_firmware_dir(firmware_dir_raw)
    if not os.path.isdir(firmware_dir):
        _output(False, error_message=f"firmware_dir not found: {firmware_dir}")
        return

    da_file = _resolve_under(firmware_dir, da_file_raw)
    scatter_file = _resolve_under(firmware_dir, scatter_file_raw)
    if not os.path.isfile(da_file):
        _output(False, error_message=f"da_file not found: {da_file}")
        return
    if not os.path.isfile(scatter_file):
        _output(False, error_message=f"scatter_file not found: {scatter_file}")
        return

    tool_dir = _locate_flash_tool_dir(args.get("flash_tool_dir"))
    if not os.path.isdir(tool_dir):
        _output(False, error_message=f"flash_tool_dir not found: {tool_dir}")
        return
    flash_tool_exe = _pick_flash_tool_exe(tool_dir)
    if not flash_tool_exe:
        _output(False, error_message=f"flash_tool executable not found under {tool_dir}")
        return

    command = args.get("command") or "firmware-upgrade"
    boot_mode = args.get("boot_mode") or "auto"
    try:
        timeout = int(args.get("timeout_seconds", 1200))
    except (TypeError, ValueError):
        timeout = 1200

    cmd = [flash_tool_exe, "-c", command, "-d", da_file, "-s", scatter_file, "-b", boot_mode]
    subprocess_env = _build_subprocess_env(os.path.dirname(flash_tool_exe))

    started_at = time.time()
    try:
        lock_fd = _acquire_host_lock()
    except OSError as exc:
        _output(False, error_message=f"lock setup failed: {exc}")
        return
    lock_acquired_at = time.time()

    # Best-effort: hand the device into flash mode via ADB before invoking flash_tool.
    # flash_tool itself USB-polls, so a failed reboot doesn't break the flow.
    pre_reboot: dict = {"attempted": False, "skip_reason": "disabled by params"}
    if bool(args.get("reboot_to_flash", True)):
        pre_reboot = _reboot_into_flash_mode(
            serial=os.environ.get("STP_DEVICE_SERIAL", ""),
            target=args.get("reboot_target") or "bootloader",
            adb_path=os.environ.get("STP_ADB_PATH", "adb"),
            wait_seconds=int(args.get("pre_reboot_wait_seconds", 5) or 0),
        )

    try:
        proc = subprocess.run(
            cmd,
            cwd=os.path.dirname(flash_tool_exe),
            env=subprocess_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _output(False,
                error_message=f"flash_tool timed out after {timeout}s",
                metrics={"command_argv": cmd,
                         "pre_reboot": pre_reboot,
                         "duration_seconds": round(time.time() - started_at, 2)})
        return
    except FileNotFoundError as exc:
        _output(False,
                error_message=f"flash_tool not executable ({exc}); chmod +x or check libs",
                metrics={"command_argv": cmd, "pre_reboot": pre_reboot})
        return
    except Exception as exc:
        _output(False,
                error_message=f"flash_tool launch failed: {exc}",
                metrics={"command_argv": cmd, "pre_reboot": pre_reboot})
        return
    finally:
        _release_host_lock(lock_fd)

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    duration = round(time.time() - started_at, 2)
    lock_wait = round(lock_acquired_at - started_at, 2)

    metrics = {
        "duration_seconds": duration,
        "lock_wait_seconds": lock_wait,
        "exit_code": proc.returncode,
        "command_argv": cmd,
        "da_file": da_file,
        "scatter_file": scatter_file,
        "firmware_dir": firmware_dir,
        "pre_reboot": pre_reboot,
        "stdout_tail": stdout[-1500:],
        "stderr_tail": stderr[-500:],
    }

    if proc.returncode != 0:
        _output(False,
                error_message=f"flash_tool exited {proc.returncode}: {(stderr or stdout)[:1500]}",
                metrics=metrics)
        return

    verdict_ok, evidence = _scan_output_for_verdict(stdout, stderr)
    if not verdict_ok:
        _output(False, error_message=f"verdict failed: {evidence}", metrics=metrics)
        return

    metrics["verdict"] = evidence
    _output(True, metrics=metrics)


if __name__ == "__main__":
    main()
