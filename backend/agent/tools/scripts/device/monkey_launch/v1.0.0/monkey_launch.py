"""Monkey 测试启动脚本：对单台设备启动 AIMonkeyTest。

通过平台 ScriptBatch 调用，接收 STP_DEVICE_SERIAL 环境变量，
加载 AIMonkeyTest_20260317 目录中的 MonkeyTest.py 并调用 startTest()。

环境变量:
    STP_DEVICE_SERIAL    (required) 目标设备序列号
    STP_ADB_PATH         (default: adb)
    STP_STEP_PARAMS      (optional JSON) 覆盖启动参数

STP_STEP_PARAMS 结构:
{
    "aimonkey_dir": "/opt/stability-test-agent/agent/tools/AIMonkeyTest_20260317",
    "need_nohup": true,
    "push_resources": true,
    "sleep_mode": false,
    "blacklist": true,
    "use_watcher": true
}

输出 (stdout):
    {"success": true/false, "error_message": "...", "metrics": {...}}
"""

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

from _adb import adb_path, device_serial, output_result, params


def _resolve_aimonkey_dir(cfg: dict) -> Path:
    """Resolve AIMonkeyTest directory from config or default location."""
    explicit = cfg.get("aimonkey_dir", "")
    if explicit and Path(explicit).is_dir():
        return Path(explicit).resolve()

    # Default: relative to this script's grandparent tools directory
    script_dir = Path(__file__).resolve().parent  # v1.0.0/
    tools_dir = script_dir.parent.parent.parent.parent  # tools/
    default = tools_dir / "AIMonkeyTest_20260317"
    if default.is_dir():
        return default

    # Fallback: same as tools_dir sibling
    alt = tools_dir.parent / "AIMonkeyTest_20260317"
    if alt.is_dir():
        return alt

    return default  # Let caller report error


def _load_monkey_test(aimonkey_dir: Path):
    """Dynamically load MonkeyTest class from AIMonkeyTest directory."""
    script_path = aimonkey_dir / "MonkeyTest.py"
    if not script_path.exists():
        raise FileNotFoundError(f"MonkeyTest.py not found at {script_path}")

    module_name = f"_stp_monkey_launcher_{abs(hash(str(script_path)))}"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {script_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _verify_device(serial: str) -> dict:
    """Quick pre-check: device is connected and online."""
    from _adb import adb_shell_quiet
    try:
        result = adb_shell_quiet("echo ready", timeout=10)
        if result.returncode != 0:
            return {"ok": False, "error": f"Device {serial} not reachable"}
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"Device check failed: {exc}"}


def main():
    serial = device_serial()
    args = params()

    aimonkey_dir = _resolve_aimonkey_dir(args)
    if not aimonkey_dir.is_dir():
        output_result(False, error_message=f"AIMonkeyTest dir not found: {aimonkey_dir}")
        return

    # Pre-check device
    check = _verify_device(serial)
    if not check["ok"]:
        output_result(False, error_message=check["error"])
        return

    need_nohup = bool(args.get("need_nohup", True))
    push_resources = bool(args.get("push_resources", True))
    sleep_mode = bool(args.get("sleep_mode", False))
    blacklist = bool(args.get("blacklist", True))

    old_cwd = os.getcwd()
    inserted = False
    try:
        # Change to AIMonkeyTest dir (required by MonkeyTest relative paths)
        os.chdir(str(aimonkey_dir))

        # Add to sys.path for modules.common imports
        if str(aimonkey_dir) not in sys.path:
            sys.path.insert(0, str(aimonkey_dir))
            inserted = True

        module = _load_monkey_test(aimonkey_dir)
        MonkeyTest = getattr(module, "MonkeyTest")
        runner = MonkeyTest(need_nohup, push_resources, sleep_mode, blacklist)

        t0 = time.time()
        runner.startTest(serial)
        elapsed = round(time.time() - t0, 1)

        output_result(
            True,
            metrics={
                "serial": serial,
                "aimonkey_dir": str(aimonkey_dir),
                "push_resources": push_resources,
                "sleep_mode": sleep_mode,
                "blacklist": blacklist,
                "duration_s": elapsed,
            },
        )
    except Exception as exc:
        output_result(False, error_message=f"Monkey launch failed: {exc}")
    finally:
        os.chdir(old_cwd)
        if inserted:
            try:
                sys.path.remove(str(aimonkey_dir))
            except ValueError:
                pass


if __name__ == "__main__":
    main()
