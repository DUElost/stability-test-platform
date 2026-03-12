"""Built-in action registry for pipeline steps.

Each action is a plain function: (StepContext) -> StepResult
"""

from ..pipeline_engine import StepContext, StepResult
from .device_actions import (
    check_device, clean_env, push_resources,
    ensure_root, fill_storage, connect_wifi, install_apk,
    setup_device_commands,
)
from .process_actions import (
    start_process, monitor_process, stop_process, run_instrument,
    guard_process, run_shell_script,
)
from .file_actions import adb_pull, collect_bugreport, scan_aee, export_mobilelogs
from .log_actions import aee_extract, log_scan
from .tool_actions import run_tool_script

ACTION_REGISTRY = {
    # Device actions
    "check_device": check_device,
    "clean_env": clean_env,
    "push_resources": push_resources,
    "ensure_root": ensure_root,
    "fill_storage": fill_storage,
    "connect_wifi": connect_wifi,
    "install_apk": install_apk,
    "setup_device_commands": setup_device_commands,
    # Process actions
    "start_process": start_process,
    "monitor_process": monitor_process,
    "stop_process": stop_process,
    "run_instrument": run_instrument,
    "guard_process": guard_process,
    "run_shell_script": run_shell_script,
    # File actions
    "adb_pull": adb_pull,
    "collect_bugreport": collect_bugreport,
    "scan_aee": scan_aee,
    "export_mobilelogs": export_mobilelogs,
    # Log actions
    "aee_extract": aee_extract,
    "log_scan": log_scan,
    # Tool bridge
    "run_tool_script": run_tool_script,
}
