import os
import re
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
        self._safe_pattern = re.compile(r"^[\w@%:/._+\-]+$")

    def execute_task(self, task_type: str, params: Dict[str, Any], device_serial: str) -> TaskResult:
        try:
            task_name = task_type.upper()
            if task_name == "MONKEY":
                return self._run_monkey(device_serial, params)
            if task_name == "MTBF":
                return self._run_mtbf(device_serial, params)
            if task_name == "DDR":
                return self._run_ddr(device_serial, params)
            if task_name == "GPU":
                return self._run_gpu_stress(device_serial, params)
            if task_name == "STANDBY":
                return self._run_standby(device_serial, params)
            return self._run_local_script(params)
        except ValueError as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="INVALID_PARAM", error_message=str(exc))
        except AdbError as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="ADB_ERROR", error_message=str(exc))
        except subprocess.TimeoutExpired:
            return TaskResult(status="FAILED", exit_code=1, error_code="TIMEOUT", error_message="command timeout")
        except Exception as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="UNKNOWN", error_message=str(exc))

    def _tail(self, text: str, limit: int = 2000) -> str:
        return (text or "")[-limit:]

    def _push_resource(self, serial: str, local_path: str, remote_dir: str) -> None:
        """推送本地资源到目标目录"""
        target = os.path.join(remote_dir, os.path.basename(local_path))
        self.adb.push(serial, local_path, target)

    def _safe(self, value: Any, field: str) -> str:
        """确保传入 adb shell 的参数不含空格/分号等危险字符"""
        if value is None:
            raise ValueError(f"{field} is required")
        sval = str(value)
        if not self._safe_pattern.match(sval):
            raise ValueError(f"{field} contains unsafe characters")
        return sval

    def _safe_optional(self, value: Any, field: str) -> Optional[str]:
        if value is None:
            return None
        sval = str(value)
        if not self._safe_pattern.match(sval):
            raise ValueError(f"{field} contains unsafe characters")
        return sval

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
        return TaskResult(status="FINISHED", exit_code=result.returncode, log_summary=self._tail(result.stdout))

    def _run_mtbf(self, serial: str, params: Dict[str, Any]) -> TaskResult:
        """MTBF 测试：推送资源 + 安装 APK + am instrument"""
        resource_dir = params.get("resource_dir")
        remote_dir = params.get("remote_dir", "/sdcard/mtbf")
        apk_path = params.get("apk_path")
        runner = self._safe(params.get("runner", "com.transsion.stresstest.test/androidx.test.runner.AndroidJUnitRunner"), "runner")
        instrument_args = params.get("instrument_args") or {}

        if resource_dir and os.path.exists(resource_dir):
            self._push_resource(serial, resource_dir, remote_dir)
        if apk_path:
            self.adb.install(serial, apk_path)

        cmd = ["am", "instrument", "-w"]
        for key, value in instrument_args.items():
            cmd += ["-e", self._safe(key, "instrument_arg_key"), self._safe(value, "instrument_arg_value")]
        cmd.append(runner)
        result = self.adb.shell(serial, cmd)
        return TaskResult(status="FINISHED", exit_code=result.returncode, log_summary=self._tail(result.stdout))

    def _run_ddr(self, serial: str, params: Dict[str, Any]) -> TaskResult:
        """DDR 测试：需 root，推送 memtester 并执行"""
        uid = self.adb.shell(serial, ["id", "-u"]).stdout.strip()
        if uid != "0":
            return TaskResult(status="FAILED", exit_code=1, error_code="NO_ROOT", error_message="device not rooted")

        memtester_path = params.get("memtester_path")
        remote_path = self._safe(params.get("remote_path", "/data/local/tmp/memtester"), "remote_path")
        mem_size_mb = int(params.get("mem_size_mb", 512))
        loops = int(params.get("loops", 1))

        if memtester_path:
            self.adb.push(serial, memtester_path, remote_path)
            self.adb.shell(serial, ["chmod", "755", remote_path])

        result = self.adb.shell(serial, [remote_path, str(mem_size_mb), str(loops)])
        status = "FINISHED" if result.returncode == 0 else "FAILED"
        err = None if status == "FINISHED" else "memtester_failed"
        return TaskResult(status=status, exit_code=result.returncode, error_code=err, log_summary=self._tail(result.stdout))

    def _run_gpu_stress(self, serial: str, params: Dict[str, Any]) -> TaskResult:
        """GPU 压力：循环启动 Antutu GPU 测试 Activity"""
        apk_path = params.get("apk_path")
        activity = self._safe(params.get("activity", "com.antutu.ABenchMark/.ABenchMarkStart"), "activity")
        loops = int(params.get("loops", 3))
        interval = int(params.get("interval", 120))

        if apk_path:
            self.adb.install(serial, apk_path)

        summaries = []
        last_code = 0
        for idx in range(loops):
            result = self.adb.shell(serial, ["am", "start", "-n", activity])
            last_code = result.returncode
            summaries.append(f"loop={idx+1}, exit={result.returncode}")
            time.sleep(interval)
        status = "FINISHED" if last_code == 0 else "FAILED"
        return TaskResult(status=status, exit_code=last_code, log_summary="; ".join(summaries)[-2000:])

    def _run_standby(self, serial: str, params: Dict[str, Any]) -> TaskResult:
        """待机测试：播放视频后灭屏保持待机"""
        video_url = self._safe(params.get("video_url", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "video_url")
        standby_seconds = int(params.get("standby_seconds", 1800))
        screen_off = bool(params.get("screen_off", True))

        self.adb.shell(serial, ["am", "start", "-a", "android.intent.action.VIEW", "-d", video_url])
        if screen_off:
            self.adb.shell(serial, ["input", "keyevent", "26"])

        time.sleep(standby_seconds)
        return TaskResult(status="FINISHED", exit_code=0, log_summary=f"standby {standby_seconds}s")

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
