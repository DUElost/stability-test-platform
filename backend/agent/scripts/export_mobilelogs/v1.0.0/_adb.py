"""ADB helpers for export_mobilelogs script."""

import json
import os
import sys


def device_serial() -> str:
    serial = os.environ.get("STP_DEVICE_SERIAL", "")
    if not serial:
        print(json.dumps({"success": False, "error_message": "STP_DEVICE_SERIAL missing"}))
        sys.exit(1)
    return serial


def params() -> dict:
    raw = os.environ.get("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def shared_metrics() -> dict:
    raw = os.environ.get("STP_SHARED_METRICS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def output_result(success: bool, **kwargs) -> None:
    payload = {"success": success, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))
