import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .adb_wrapper import AdbError, AdbWrapper


@dataclass
class TaskResult:
    status: str
    exit_code: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    log_summary: Optional[str] = None


class TaskExecutor:
    def __init__(self, adb: AdbWrapper) -> None:
        self.adb = adb

    def execute_task(self, task_type: str, params: Dict[str, Any], device_serial: str) -> TaskResult:
        try:
            if task_type.upper() == "MONKEY":
                return self._run_monkey(device_serial, params)
            return self._run_local_script(params)
        except AdbError as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="ADB_ERROR", error_message=str(exc))
        except subprocess.TimeoutExpired:
            return TaskResult(status="FAILED", exit_code=1, error_code="TIMEOUT", error_message="command timeout")
        except Exception as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="UNKNOWN", error_message=str(exc))

    def _run_monkey(self, serial: str, params: Dict[str, Any]) -> TaskResult:
        packages = params.get("packages") or []
        event_count = int(params.get("event_count", 10000))
        throttle = int(params.get("throttle", 100))
        seed = params.get("seed")
        cmd = ["monkey"]
        for pkg in packages:
            cmd += ["-p", pkg]
        cmd += ["--throttle", str(throttle)]
        if seed is not None:
            cmd += ["-s", str(seed)]
        cmd += [str(event_count)]
        result = self.adb.shell(serial, cmd)
        return TaskResult(status="FINISHED", exit_code=result.returncode, log_summary=result.stdout[-2000:])

    def _run_local_script(self, params: Dict[str, Any]) -> TaskResult:
        script_path = params.get("script_path")
        args = params.get("args") or []
        timeout = params.get("timeout")
        if not script_path:
            return TaskResult(status="FAILED", exit_code=1, error_code="NO_SCRIPT", error_message="script_path required")
        start = time.time()
        result = subprocess.run(
            [script_path] + args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start
        if result.returncode != 0:
            return TaskResult(
                status="FAILED",
                exit_code=result.returncode,
                error_code="SCRIPT_FAILED",
                error_message=result.stderr.strip(),
                log_summary=(result.stdout or "")[-2000:],
            )
        return TaskResult(
            status="FINISHED",
            exit_code=result.returncode,
            log_summary=f"ok duration={duration:.2f}s",
        )
