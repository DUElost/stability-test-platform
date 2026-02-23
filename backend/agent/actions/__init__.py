"""Built-in action registry for pipeline steps.

Each action is a plain function: (StepContext) -> StepResult
"""

from backend.agent.pipeline_engine import StepContext, StepResult
from backend.agent.actions.device_actions import (
    check_device, clean_env, push_resources,
    ensure_root, fill_storage, connect_wifi, install_apk,
)
from backend.agent.actions.process_actions import (
    start_process, monitor_process, stop_process, run_instrument,
)
from backend.agent.actions.file_actions import adb_pull, collect_bugreport, scan_aee
from backend.agent.actions.log_actions import aee_extract, log_scan
from backend.agent.actions.tool_actions import run_tool_script

ACTION_REGISTRY = {
    # Device actions
    "check_device": check_device,
    "clean_env": clean_env,
    "push_resources": push_resources,
    "ensure_root": ensure_root,
    "fill_storage": fill_storage,
    "connect_wifi": connect_wifi,
    "install_apk": install_apk,
    # Process actions
    "start_process": start_process,
    "monitor_process": monitor_process,
    "stop_process": stop_process,
    "run_instrument": run_instrument,
    # File actions
    "adb_pull": adb_pull,
    "collect_bugreport": collect_bugreport,
    "scan_aee": scan_aee,
    # Log actions
    "aee_extract": aee_extract,
    "log_scan": log_scan,
    # Tool bridge
    "run_tool_script": run_tool_script,
}
