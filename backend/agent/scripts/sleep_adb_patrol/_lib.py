# -*- coding: utf-8 -*-
"""宿主侧 ADB 驱动休眠唤醒专项共享库（sleep_adb_setup / patrol / finish 共用）。

完全摆脱设备端 AutoTestTool APK、platform 签名、Alarm 唤醒拦截与系统权限限制：
- 通过 ``adb shell input keyevent KEYCODE_SLEEP`` / ``KEYCODE_WAKEUP`` 驱动灭屏休眠与亮屏唤醒；
- 通过 ``dumpsys power`` 中的 ``mWakefulness=`` 权威回读真实电源状态（Awake vs Asleep/Dozing）；
- 状态按 ``STP_JOB_ID + serial`` 持久化在 Agent 主机临时文件，设备端零自启足迹。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

_DEFAULT_PROJECT = "legacy"
_DEFAULT_TEST_TIMES = 100
_DEFAULT_WAKE_SECONDS = 60
_DEFAULT_SLEEP_SECONDS = 300
_DEFAULT_WAKE_TIMEOUT_SECONDS = 60
_DEFAULT_SLEEP_CONFIRM_SECONDS = 15
_DEFAULT_INLINE_WAIT_MAX_SECONDS = 20
_DEFAULT_READY_TIMEOUT_SECONDS = 90
_DEFAULT_WAKE_RETRY_ATTEMPTS = 2
_DEFAULT_SLEEP_RETRY_ATTEMPTS = 2
_READY_POLL_INTERVAL_SECONDS = 3.0
_READY_MAX_POLLS_DEFAULT = 400

_TZ_SHANGHAI = timezone(timedelta(hours=8))
_WAKEFULNESS_RE = re.compile(r"\bmWakefulness=([A-Za-z]+)")

# 解锁至主界面：亮屏 → wm dismiss-keyguard → 3 次 MENU(82) 兜底 → HOME(3)
_UNLOCK_TO_HOME_CMD = (
    "input keyevent KEYCODE_WAKEUP; "
    "wm dismiss-keyguard 2>/dev/null || true; "
    "input keyevent 82; sleep 0.3; "
    "input keyevent 82; sleep 0.3; "
    "input keyevent 82; sleep 0.3; "
    "input keyevent KEYCODE_HOME"
)


# ---------------------------------------------------------------------------
# 环境 / 参数 / 输出契约
# ---------------------------------------------------------------------------


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def param_or_env(cfg: dict[str, Any], key: str, env_key: str, default: Any) -> Any:
    value = cfg.get(key)
    if value is not None and str(value).strip() != "":
        return value
    raw = env(env_key, "").strip()
    if raw != "":
        return raw
    return default


def parse_bool(val: Any, default: bool = True) -> bool:
    if isinstance(val, bool):
        return val
    if val is None or str(val).strip() == "":
        return default
    text = str(val).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"非法布尔参数值: {val!r}")


def parse_int(val: Any, *, name: str, minimum: int) -> int:
    if isinstance(val, bool):
        raise ValueError(f"参数 {name} 必须为整数，当前为布尔值: {val!r}")
    try:
        parsed = int(str(val).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {name} 必须为整数: {val!r}") from exc
    if parsed < minimum:
        raise ValueError(f"参数 {name} 不能小于 {minimum}，当前为 {parsed}")
    return parsed


def adb_path() -> str:
    return env("STP_ADB_PATH", "adb") or "adb"


def device_serial() -> str:
    serial = env("STP_DEVICE_SERIAL", "").strip()
    if not serial:
        print(
            json.dumps(
                {"success": False, "error_message": "STP_DEVICE_SERIAL is not set"},
                ensure_ascii=False,
            )
        )
        sys.exit(1)
    return serial


def job_id() -> str:
    return env("STP_JOB_ID", "").strip()


def params() -> dict[str, Any]:
    raw = env("STP_STEP_PARAMS", "{}")
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def output_result(success: bool, **kwargs: Any) -> None:
    print(json.dumps({"success": success, **kwargs}, ensure_ascii=False))


def progress_stamp(payload: dict[str, Any]) -> None:
    """往 stderr 输出 PROGRESS 戳，供引擎停滞钟与运行日志消费。"""
    sys.stderr.write(f"PROGRESS {json.dumps(payload, ensure_ascii=False)}\n")
    sys.stderr.flush()


def format_ts(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=_TZ_SHANGHAI).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# ADB 与电源状态交互
# ---------------------------------------------------------------------------


def decode_device_output(raw: bytes | None) -> str:
    return (raw or b"").decode("utf-8", errors="replace")


def adb(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    cmd = [adb_path(), "-s", device_serial(), *args]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except OSError as exc:
        return -1, "", str(exc)
    return (
        result.returncode,
        decode_device_output(result.stdout),
        decode_device_output(result.stderr),
    )


def adb_shell(command: str, timeout: int = 30) -> tuple[int, str, str]:
    return adb("shell", command, timeout=timeout)


def device_online(timeout: int = 15) -> bool:
    rc, out, _ = adb("get-state", timeout=timeout)
    return rc == 0 and out.strip() == "device"


def boot_completed(timeout: int = 15) -> bool:
    rc, out, _ = adb_shell("getprop sys.boot_completed", timeout=timeout)
    return rc == 0 and out.strip() == "1"


def system_ready() -> tuple[bool, str]:
    if not device_online():
        return False, "offline"
    if not boot_completed():
        return False, "booting"
    return True, "ready"


def wait_system_ready(
    timeout_seconds: float,
    *,
    poll_interval: float = _READY_POLL_INTERVAL_SECONDS,
    max_polls: int = _READY_MAX_POLLS_DEFAULT,
    now_fn: Callable[[], float] = time.time,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_poll: Callable[[int, str], None] | None = None,
) -> tuple[bool, str]:
    deadline = now_fn() + max(0.0, float(timeout_seconds))
    last_state = "unknown"
    polls = 0
    while polls < max(1, int(max_polls)):
        polls += 1
        ready, observed = system_ready()
        last_state = observed
        if ready:
            return True, "ready"
        if on_poll is not None:
            on_poll(polls, observed)
        remaining = deadline - now_fn()
        if remaining <= 0:
            break
        sleep_fn(min(poll_interval, remaining))
    return False, last_state


def read_wakefulness(timeout: int = 15) -> str:
    """读取设备当前电源唤醒态（Awake / Asleep / Dreaming / Dozing；不可达返回空串）。"""
    rc, out, _ = adb_shell("dumpsys power | grep mWakefulness=", timeout=timeout)
    if rc != 0:
        return ""
    match = _WAKEFULNESS_RE.search(out or "")
    return match.group(1) if match else ""


def is_awake_state(wakefulness: str) -> bool:
    return wakefulness.lower() == "awake"


def is_asleep_state(wakefulness: str) -> bool:
    return wakefulness.lower() in ("asleep", "dozing")


def configure_sleep_environment() -> bool:
    """配置允许休眠环境（关闭插电常亮，设长亮屏超时以由宿主精准控时）。"""
    rc_stayon, _, _ = adb_shell("svc power stayon false", timeout=15)
    adb_shell("settings put global stay_on_while_plugged_in 0", timeout=15)
    adb_shell("settings put system screen_off_timeout 2147483647", timeout=15)
    adb_shell("locksettings set-disabled true 2>/dev/null || true", timeout=15)
    return rc_stayon == 0


def restore_awake_environment() -> None:
    """测试收尾恢复插电常亮，便于设备归还后续专项。"""
    adb_shell("svc power stayon usb", timeout=15)


def trigger_wakeup(*, unlock: bool = True, timeout: int = 20) -> bool:
    cmd = _UNLOCK_TO_HOME_CMD if unlock else "input keyevent KEYCODE_WAKEUP"
    rc, _, _ = adb_shell(cmd, timeout=timeout)
    return rc == 0


def trigger_sleep(timeout: int = 15) -> bool:
    rc, _, _ = adb_shell("input keyevent KEYCODE_SLEEP", timeout=timeout)
    return rc == 0


# ---------------------------------------------------------------------------
# 状态文件与结果目录契约
# ---------------------------------------------------------------------------


def state_file_path(serial: str | None = None) -> Path:
    target = serial if serial is not None else device_serial()
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", target)
    return Path(tempfile.gettempdir()) / f"sleep_adb_{safe}.json"


def load_state(*, expected_job_id: str | None = None, serial: str | None = None) -> dict[str, Any] | None:
    path = state_file_path(serial)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if expected_job_id is not None and str(data.get("job_id", "")) != expected_job_id:
        return None
    return data


def save_state(state: dict[str, Any], *, serial: str | None = None) -> None:
    path = state_file_path(serial)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def project_name(cfg: dict[str, Any], state: dict[str, Any] | None = None) -> str:
    raw = cfg.get("project")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    if state and str(state.get("project") or "").strip():
        return str(state["project"]).strip()
    return env("STP_SLEEP_PROJECT", _DEFAULT_PROJECT).strip() or _DEFAULT_PROJECT


def results_dir(project: str) -> Path:
    root = env("STP_AEE_NFS_ROOT", "").strip()
    if not root:
        raise RuntimeError("STP_AEE_NFS_ROOT is not set")
    return Path(root) / "sleep" / project / "results"
