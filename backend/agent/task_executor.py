import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

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
        self._logger = logging.getLogger(__name__)
        self._heartbeat_interval = 60

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
            if task_name == "AIMONKEY":
                return self._run_aimonkey(device_serial, params)
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

    # ---------------- AIMONKEY 任务实现 ----------------

    def _run_aimonkey(self, serial: str, params: Dict[str, Any]) -> TaskResult:
        """AIMONKEY 主流程：设备配置 → 启动 Monkey → 监控 → 日志收集"""
        run_id = params.get("run_id")
        api_url = params.get("api_url", "")
        log_dir = params.get("log_dir") or self._aimonkey_log_dir(run_id)

        runtime_minutes = int(params.get("runtime_minutes", 10080))
        throttle_ms = int(params.get("throttle_ms", 500))
        max_restarts = int(params.get("max_restarts", 1))

        try:
            self._aimonkey_setup(serial, params)
            pid = self._aimonkey_start_monkey(serial, params)
            if not pid:
                return TaskResult(
                    status="FAILED",
                    exit_code=1,
                    error_code="MONKEY_START_FAILED",
                    error_message="Failed to start monkey or capture PID",
                )

            summary, final_pid = self._aimonkey_monitor(
                serial, pid, runtime_minutes, throttle_ms, max_restarts, log_dir, params, api_url, run_id
            )

            self._aimonkey_stop_monkey(serial, final_pid or pid)
            self._aimonkey_collect_logs(serial, log_dir)

            return TaskResult(status="FINISHED", exit_code=0, log_summary=self._tail(summary))
        except AdbError as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="ADB_ERROR", error_message=str(exc))
        except Exception as exc:
            self._logger.exception("aimonkey_failed")
            return TaskResult(status="FAILED", exit_code=1, error_code="UNKNOWN", error_message=str(exc))

    def _aimonkey_log_dir(self, run_id: Optional[int]) -> str:
        """生成日志目录路径"""
        base_dir = Path("logs") / "runs"
        if run_id:
            return str(base_dir / str(run_id))
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        return str(base_dir / f"aimonkey_{timestamp}")

    def _aimonkey_setup(self, serial: str, params: Dict[str, Any]) -> None:
        """设备前置配置：root 权限、开发者选项、日志服务、WiFi、存储填充、清理日志"""
        self._ensure_root_access(serial)
        self._enable_dev_settings(serial)
        self._start_mobile_logger(serial)

        wifi_ssid = params.get("wifi_ssid", "")
        if wifi_ssid:
            self._connect_wifi(serial, wifi_ssid, params.get("wifi_password", ""))

        if bool(params.get("enable_fill_storage", True)):
            target_pct = int(params.get("target_fill_percentage", 60))
            self._fill_storage(serial, target_pct)

        if bool(params.get("enable_clear_logs", True)):
            self._clear_device_logs(serial)

    def _ensure_root_access(self, serial: str, max_attempts: int = 3) -> None:
        """确保设备具有 root 权限，失败则抛出 AdbError"""
        for attempt in range(max_attempts):
            try:
                result = self.adb.shell(serial, ["id", "-u"])
                if result.stdout.strip() == "0":
                    return

                self.adb.shell(serial, ["root"])
                time.sleep(3)

                result = self.adb.shell(serial, ["id", "-u"])
                if result.stdout.strip() == "0":
                    return
            except AdbError:
                if attempt == max_attempts - 1:
                    raise
                time.sleep(2)
        raise AdbError("Failed to obtain root access")

    def _enable_dev_settings(self, serial: str) -> None:
        """开启开发者选项和 ADB 调试"""
        try:
            self.adb.shell(serial, ["settings", "put", "global", "development_settings_enabled", "1"])
            self.adb.shell(serial, ["settings", "put", "global", "adb_enabled", "1"])
        except AdbError as exc:
            self._logger.debug("enable_dev_settings_failed: %s", exc)

    def _start_mobile_logger(self, serial: str) -> None:
        """启动 mobile logger，失败仅记录日志不中断"""
        props = [
            ("persist.vendor.debuglogger", "1"),
            ("persist.vendor.sys.modemlog.enable", "1"),
            ("persist.sys.logkit.ctrlcode", "1"),
        ]
        for prop, val in props:
            try:
                self.adb.shell(serial, ["setprop", prop, val])
            except AdbError:
                self._logger.debug("setprop_failed: %s", prop)

    def _connect_wifi(self, serial: str, ssid: str, password: str) -> None:
        """连接 WiFi，失败仅记录日志不中断"""
        try:
            safe_ssid = self._safe(ssid, "wifi_ssid")
            safe_pwd = self._safe_optional(password) or ""
            self.adb.shell(serial, ["svc", "wifi", "enable"])
            time.sleep(1)
            cmd = f'cmd -w wifi connect-network "{safe_ssid}" wpa2 "{safe_pwd}"'
            self.adb.shell(serial, ["sh", "-c", cmd])
        except (ValueError, AdbError) as exc:
            self._logger.warning("wifi_connect_failed: %s", exc)

    def _fill_storage(self, serial: str, target_pct: int) -> None:
        """填充存储到目标百分比"""
        try:
            result = self.adb.shell(serial, ["df", "/data"])
            lines = result.stdout.strip().splitlines()
            if len(lines) < 2:
                return

            parts = lines[1].split()
            if len(parts) < 4:
                return

            total_kb = int(parts[1])
            used_kb = int(parts[2])
            target_used = total_kb * target_pct // 100
            need_kb = target_used - used_kb

            if need_kb <= 0:
                return

            block_size_kb = 1024
            blocks = max(need_kb // block_size_kb, 1)
            self.adb.shell(serial, ["sh", "-c", f"dd if=/dev/zero of=/data/local/tmp/fill.bin bs={block_size_kb}k count={blocks}"])
        except (ValueError, AdbError) as exc:
            self._logger.warning("fill_storage_failed: %s", exc)

    def _clear_device_logs(self, serial: str) -> None:
        """清理设备历史日志"""
        cleanup_cmds = [
            "rm -rf /data/debuglogger/mobilelog/*",
            "rm -rf /data/aee_exp/*",
            "rm -rf /data/vendor/aee_exp/*",
        ]
        for cmd in cleanup_cmds:
            try:
                self.adb.shell(serial, ["sh", "-c", cmd])
            except AdbError:
                pass

        for path in ["/data/aee_exp", "/data/vendor/aee_exp", "/data/debuglogger/mobilelog"]:
            try:
                self.adb.shell(serial, ["mkdir", "-p", path])
            except AdbError:
                pass

    def _aimonkey_start_monkey(self, serial: str, params: Dict[str, Any]) -> Optional[str]:
        """推送资源并启动 Monkey，返回进程 PID"""
        resource_dir = params.get("resource_dir", "")
        if not resource_dir:
            # 优先从环境变量读取，支持 Linux/Windows 不同部署路径
            resource_dir = os.environ.get("AIMONKEY_RESOURCE_DIR", "")
        if not resource_dir:
            # 默认路径：尝试基于当前文件位置推导
            try:
                resource_dir = str(Path(__file__).resolve().parents[3] / "Monkey_test" / "AIMonkeyTest_2025mtk")
            except Exception:
                resource_dir = "/opt/stability-test-agent/resources/aimonkey"

        files_to_push = [
            ("aim", "/data/local/tmp/aim"),
            ("aimwd", "/data/local/tmp/aimwd"),
            ("aim.jar", "/data/local/tmp/aim.jar"),
            ("blacklist.txt", "/sdcard/blacklist.txt"),
        ]

        for fname, remote in files_to_push:
            local_path = os.path.join(resource_dir, fname)
            if os.path.exists(local_path):
                try:
                    self.adb.push(serial, local_path, remote)
                    self.adb.shell(serial, ["chmod", "755", remote])
                except AdbError as exc:
                    self._logger.debug("push_failed: %s - %s", fname, exc)

        try:
            self.adb.shell(serial, ["sh", "-c", "nohup /data/local/tmp/aimwd >/dev/null 2>&1 &"])
        except AdbError:
            pass

        throttle_ms = int(params.get("throttle_ms", 500))
        runtime_minutes = int(params.get("runtime_minutes", 10080))

        monkey_cmd = (
            f"nohup /data/local/tmp/aim --pkg-blacklist-file /sdcard/blacklist.txt "
            f"--smartuiautomator true --hprof --ignore-crashes --ignore-security-exceptions "
            f"--ignore-timeouts --throttle {throttle_ms} --runtime-minutes {runtime_minutes} "
            f"--switchuimode -v >/dev/null 2>&1 & echo $!"
        )

        try:
            result = self.adb.shell(serial, ["sh", "-c", monkey_cmd])
            pid = result.stdout.strip().splitlines()[-1] if result.stdout else ""
            if pid and pid.isdigit():
                return pid
        except AdbError as exc:
            self._logger.warning("start_monkey_failed: %s", exc)

        return self.adb.get_pid(serial, "com.android.commands.monkey.transsion")

    def _aimonkey_stop_monkey(self, serial: str, pid: str) -> None:
        """停止 Monkey 进程"""
        if not pid:
            return
        try:
            self.adb.kill_process(serial, pid)
        except AdbError as exc:
            self._logger.debug("stop_monkey_failed: %s", exc)

    def _aimonkey_monitor(
        self,
        serial: str,
        pid: str,
        runtime_minutes: int,
        throttle_ms: int,
        max_restarts: int,
        log_dir: str,
        params: Dict[str, Any],
        api_url: str,
        run_id: Optional[int],
    ) -> Tuple[str, Optional[str]]:
        """监控 Monkey 运行：5秒存活检测、60秒心跳、自动重启，返回(摘要, 最终PID)"""
        start_time = time.time()
        end_time = start_time + runtime_minutes * 60
        last_heartbeat = start_time
        restart_count = 0
        current_pid = pid
        process_name = params.get("process_name", "com.android.commands.monkey.transsion")
        events: list[str] = []

        Path(log_dir).mkdir(parents=True, exist_ok=True)
        logcat_path = Path(log_dir) / "logcat.txt"

        while time.time() < end_time:
            time.sleep(5)
            now = time.time()

            alive = self.adb.get_pid(serial, process_name)
            if not alive:
                if restart_count >= max_restarts:
                    events.append(f"monkey died after {restart_count} restarts")
                    break
                restart_count += 1
                events.append(f"restart {restart_count}")
                new_pid = self._aimonkey_start_monkey(serial, params)
                if new_pid:
                    current_pid = new_pid
                continue

            self._collect_realtime_logcat(serial, logcat_path)

            if now - last_heartbeat >= self._heartbeat_interval:
                elapsed_min = int((now - start_time) / 60)
                progress = min(int((now - start_time) / (end_time - start_time) * 100), 100)
                self._send_heartbeat(api_url, run_id, progress, f"running {elapsed_min}min")
                last_heartbeat = now

        if time.time() >= end_time:
            events.append("runtime completed")

        return ("; ".join(events) if events else "completed", current_pid)

    def _collect_realtime_logcat(self, serial: str, log_path: Path) -> None:
        """收集实时 logcat 追加到文件"""
        try:
            result = self.adb.shell(serial, ["logcat", "-d", "-t", "100"])
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(result.stdout)
        except AdbError:
            pass

    def _send_heartbeat(self, api_url: str, run_id: Optional[int], progress: int, message: str) -> None:
        """向后端发送进度心跳"""
        if not api_url or not run_id:
            return
        try:
            payload = {"status": "RUNNING", "progress": progress, "log_summary": message}
            requests.post(
                f"{api_url}/api/v1/agent/runs/{run_id}/heartbeat",
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            self._logger.debug("heartbeat_failed: %s", exc)

    def _aimonkey_collect_logs(self, serial: str, log_dir: str) -> None:
        """收集 AEE、mobilelog、bugreport 等日志"""
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        pull_targets = [
            ("/data/aee_exp", "aee_exp"),
            ("/data/vendor/aee_exp", "aee_exp_vendor"),
            ("/data/debuglogger", "debuglogger"),
        ]

        for remote, local_name in pull_targets:
            try:
                local_path = Path(log_dir) / local_name
                local_path.mkdir(exist_ok=True)
                self.adb.pull(serial, remote, str(local_path))
            except AdbError as exc:
                self._logger.debug("pull_failed: %s - %s", remote, exc)

        try:
            bugreport_path = "/sdcard/bugreport-aimonkey.txt"
            self.adb.shell(serial, ["bugreport", bugreport_path])
            self.adb.pull(serial, bugreport_path, str(Path(log_dir) / "bugreport.txt"))
        except AdbError as exc:
            self._logger.debug("bugreport_failed: %s", exc)
