# -*- coding: utf-8 -*-
"""
AIMonkey 测试用例
继承 BaseTestCase 框架，提供完整的 AIMonkey 测试能力
"""

import os
import re
import time
import tarfile
import hashlib
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..test_framework import BaseTestCase, TestResult, RiskEvent
from ..aimonkey_stages import AIMonkeyStage, stage_progress
from ..aimonkey_aee import scan_aee_entries, scan_and_pull_aee_entries
from ..aimonkey_risk import build_risk_summary, write_risk_summary


class AIMonkeyTest(BaseTestCase):
    """AIMonkey 测试用例"""

    TEST_TYPE = "AIMONKEY"

    def get_default_params(self) -> Dict[str, Any]:
        return {
            "runtime_minutes": 10080,
            "throttle_ms": 500,
            "max_restarts": 1,
            "target_fill_percentage": 60,
            "enable_fill_storage": True,
            "enable_clear_logs": True,
            "process_name": "com.android.commands.monkey.transsion",
        }

    def setup(self, serial: str, params: Dict[str, Any]) -> None:
        """设备前置配置：root 权限、开发者选项、日志服务、WiFi、存储填充、清理日志"""
        self._log("开始设备前置配置...")
        self.set_progress(stage_progress(AIMonkeyStage.PRECHECK), "AIMONKEY 任务开始")

        self._log("检查 root 权限...", "INFO")
        self._ensure_root_access(serial)
        self._log("Root 权限已获取", "INFO")

        self.set_progress(stage_progress(AIMonkeyStage.PREPARE), "设备前置准备中")

        self._log("启用开发者选项...", "INFO")
        self._run_shell(serial, ["settings", "put", "global", "development_settings_enabled", "1"])
        self._run_shell(serial, ["settings", "put", "global", "adb_enabled", "1"])

        self._log("启动 mobile logger...", "INFO")
        self._start_mobile_logger(serial)

        wifi_ssid = params.get("wifi_ssid", "")
        if wifi_ssid:
            self._log(f"连接 WiFi: {wifi_ssid}...", "INFO")
            self._connect_wifi(serial, wifi_ssid, params.get("wifi_password", ""))

        if params.get("enable_fill_storage", True):
            target_pct = int(params.get("target_fill_percentage", 60))
            self._log(f"填充存储到 {target_pct}%...", "INFO")
            self._fill_storage(serial, target_pct)
            self._log("存储填充完成", "INFO")

        if params.get("enable_clear_logs", True):
            self._log("清理设备日志...", "INFO")
            self._clear_device_logs(serial)

        self.set_progress(stage_progress(AIMonkeyStage.PREPARE), "设备前置准备完成")

    def execute(self, serial: str, params: Dict[str, Any]) -> TestResult:
        """执行 AIMonkey 测试"""
        runtime_minutes = int(params.get("runtime_minutes", 10080))
        throttle_ms = int(params.get("throttle_ms", 500))
        max_restarts = int(params.get("max_restarts", 1))

        self.set_progress(stage_progress(AIMonkeyStage.RUN), "开始推送资源并启动 Monkey")

        pid = self._start_monkey(serial, params)
        if not pid:
            return TestResult(
                status="FAILED",
                exit_code=1,
                error_code="MONKEY_START_FAILED",
                error_message="Failed to start monkey or capture PID",
            )

        self.set_progress(stage_progress(AIMonkeyStage.RUN), f"Monkey 进程已启动: PID={pid}")

        self.set_progress(stage_progress(AIMonkeyStage.MONITOR), "开始监控 Monkey 运行")
        summary, final_pid = self._monitor_monkey(
            serial, pid, runtime_minutes, throttle_ms, max_restarts, params
        )
        self.set_progress(stage_progress(AIMonkeyStage.MONITOR), f"监控结束: {summary}")

        self._stop_monkey(serial, final_pid or pid)

        return TestResult(
            status="FINISHED",
            exit_code=0,
            log_summary=summary,
        )

    def scan_risks(self, serial: str) -> List[RiskEvent]:
        """风险扫描"""
        self.set_progress(stage_progress(AIMonkeyStage.RISK_SCAN), "执行风险问题检查")

        aee_entries = scan_aee_entries(self.adb, serial)
        events = []

        for entry in aee_entries:
            events.append(RiskEvent(
                event_type=entry.severity or "FATAL",
                timestamp=entry.timestamp or "",
                device=serial,
                description=entry.description or "",
                log_path=entry.path,
                raw_data={"aee_path": entry.path},
            ))

        self._log(f"风险扫描完成: 发现 {len(events)} 个风险事件", "INFO")
        return events

    def collect(self, serial: str, log_dir: str) -> Dict[str, Any]:
        """收集日志"""
        self.set_progress(stage_progress(AIMonkeyStage.EXPORT), "日志回传导出中")

        export_meta: Dict[str, Any] = {
            "aee_scanned": 0,
            "aee_pulled": 0,
            "bugreport_exported": False,
        }

        Path(log_dir).mkdir(parents=True, exist_ok=True)

        scanned_entries, pulled_records = scan_and_pull_aee_entries(
            self.adb,
            serial,
            log_dir,
            entries=None,
        )
        export_meta["aee_scanned"] = len(scanned_entries)
        export_meta["aee_pulled"] = sum(1 for item in pulled_records if item.get("pulled"))

        try:
            debuglogger_path = Path(log_dir) / "debuglogger"
            debuglogger_path.mkdir(exist_ok=True)
            self.adb.pull(serial, "/data/debuglogger", str(debuglogger_path))
        except Exception as e:
            self._log(f"拉取 debuglogger 失败: {e}", "WARN")

        try:
            bugreport_path = "/sdcard/bugreport-aimonkey.txt"
            self.adb.shell(serial, ["bugreport", bugreport_path])
            self.adb.pull(serial, bugreport_path, str(Path(log_dir) / "bugreport.txt"))
            export_meta["bugreport_exported"] = True
        except Exception as e:
            self._log(f"导出 bugreport 失败: {e}", "WARN")

        self.set_progress(stage_progress(AIMonkeyStage.TEARDOWN), "测试后置完成")

        return export_meta

    def _ensure_root_access(self, serial: str, max_attempts: int = 3) -> bool:
        """确保设备具有 root 权限"""
        for attempt in range(max_attempts):
            result = self.adb.shell(serial, ["id", "-u"])
            if result.stdout.strip() == "0":
                return True

            try:
                subprocess.run(
                    [self.adb.adb_path, "-s", serial, "root"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                time.sleep(3)
            except Exception:
                pass

            result = self.adb.shell(serial, ["id", "-u"])
            if result.stdout.strip() == "0":
                return True

            time.sleep(2)

        self._log("无法获取 root 权限", "WARN")
        return False

    def _start_mobile_logger(self, serial: str) -> None:
        """启动 mobile logger"""
        props = [
            ("persist.vendor.debuglogger", "1"),
            ("persist.vendor.sys.modemlog.enable", "1"),
            ("persist.sys.logkit.ctrlcode", "1"),
        ]
        for prop, val in props:
            try:
                self.adb.shell(serial, ["setprop", prop, val])
            except Exception:
                pass

    def _connect_wifi(self, serial: str, ssid: str, password: str) -> None:
        """连接 WiFi"""
        try:
            self.adb.shell(serial, ["svc", "wifi", "enable"])
            time.sleep(1)
            cmd = f'cmd -w wifi connect-network "{ssid}" wpa2 "{password}"'
            self.adb.shell(serial, ["sh", "-c", cmd])
        except Exception as e:
            self._log(f"WiFi 连接失败: {e}", "WARN")

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
        except Exception as e:
            self._log(f"存储填充失败: {e}", "WARN")

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
            except Exception:
                pass

        for path in ["/data/aee_exp", "/data/vendor/aee_exp", "/data/debuglogger/mobilelog"]:
            try:
                self.adb.shell(serial, ["mkdir", "-p", path])
            except Exception:
                pass

    def _start_monkey(self, serial: str, params: Dict[str, Any]) -> Optional[str]:
        """推送资源并启动 Monkey，返回进程 PID"""
        resource_dir = params.get("resource_dir", "")
        if resource_dir and (resource_dir.startswith("/mnt/") or ":" in resource_dir or
                             (len(resource_dir) > 2 and resource_dir[0] == "/" and resource_dir[2] == "/")):
            self._log(f"检测到 Windows 路径格式: {resource_dir}, 使用环境变量", "INFO")
            resource_dir = ""

        if not resource_dir:
            resource_dir = os.environ.get("AIMONKEY_RESOURCE_DIR", "")

        if not resource_dir:
            try:
                resource_dir = str(Path(__file__).resolve().parents[3] / "Monkey_test" / "AIMonkeyTest_2025mtk")
            except Exception:
                resource_dir = "/opt/stability-test-agent/resources/aimonkey"

        self._log(f"资源目录: {resource_dir}", "INFO")

        files_to_push = [
            ("aim", "/data/local/tmp/aim"),
            ("aimwd", "/data/local/tmp/aimwd"),
            ("aim.jar", "/data/local/tmp/aim.jar"),
            ("blacklist.txt", "/sdcard/blacklist.txt"),
        ]

        self._log("推送基础文件...", "INFO")
        for fname, remote in files_to_push:
            local_path = os.path.join(resource_dir, fname)
            if os.path.exists(local_path):
                try:
                    self._log(f"推送 {fname}...", "INFO")
                    self._push_with_timeout(serial, local_path, remote, timeout=300)
                    self.adb.shell(serial, ["chmod", "755", remote])
                    self._log(f"推送 {fname} 成功", "INFO")
                except Exception as e:
                    self._log(f"推送 {fname} 失败: {e}", "WARN")

        for arch_dir in ["arm64-v8a", "armeabi-v7a"]:
            local_arch_path = os.path.join(resource_dir, arch_dir)
            if os.path.isdir(local_arch_path):
                try:
                    remote_arch_path = f"/data/local/tmp/{arch_dir}"
                    self._log(f"推送 {arch_dir}...", "INFO")
                    self._push_with_timeout(serial, local_arch_path, remote_arch_path, timeout=300)
                    self._log(f"推送 {arch_dir} 成功", "INFO")
                except Exception as e:
                    self._log(f"推送 {arch_dir} 失败: {e}", "WARN")

        apk_path = os.path.join(resource_dir, "aimonkey.apk")
        if os.path.exists(apk_path):
            try:
                remote_apk_path = "/data/local/tmp/monkey.apk"
                self._log("推送 aimonkey.apk...", "INFO")
                self._push_with_timeout(serial, apk_path, remote_apk_path, timeout=300)
                self._log("推送 aimonkey.apk 成功", "INFO")
            except Exception as e:
                self._log(f"推送 aimonkey.apk 失败: {e}", "WARN")

        self._log("启动 aimwd...", "INFO")
        try:
            self._run_shell(serial, ["sh", "-c", "nohup /data/local/tmp/aimwd >/dev/null 2>&1 &"], timeout=30)
            self._log("启动 aimwd 成功", "INFO")
        except Exception as e:
            self._log(f"启动 aimwd 失败: {e}", "WARN")

        return self._start_aimonkey_process(serial, params)

    def _push_with_timeout(self, serial: str, local_path: str, remote_path: str, timeout: float = 300.0) -> None:
        """推送文件到设备，带超时控制"""
        full_cmd = [self.adb.adb_path, "-s", serial, "push", local_path, remote_path]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise Exception(result.stderr.strip() or result.stdout.strip())

    def _start_aimonkey_process(self, serial: str, params: Dict[str, Any]) -> Optional[str]:
        """启动 aimonkey 进程，返回 PID"""
        throttle_ms = int(params.get("throttle_ms", 500))
        runtime_minutes = int(params.get("runtime_minutes", 10080))

        self._log(f"配置参数: throttle={throttle_ms}ms, runtime={runtime_minutes}min", "INFO")

        monkey_cmd = (
            f"nohup /data/local/tmp/aim --pkg-blacklist-file /sdcard/blacklist.txt "
            f"--smartuiautomator true --hprof --ignore-crashes --ignore-security-exceptions "
            f"--ignore-timeouts --throttle {throttle_ms} --runtime-minutes {runtime_minutes} "
            f"--switchuimode -v >/dev/null 2>&1 & echo $!"
        )

        self._log("执行启动命令...", "INFO")
        try:
            result = self._run_shell(serial, ["sh", "-c", monkey_cmd], timeout=60)
            pid = result.stdout.strip().splitlines()[-1] if result.stdout else ""
            self._log(f"命令输出: {result.stdout}", "INFO")
            if pid and pid.isdigit():
                self._log(f"获取到 PID: {pid}", "INFO")
                return pid
            else:
                self._log(f"PID 格式异常: {pid}", "WARN")
        except Exception as e:
            self._log(f"启动命令执行失败: {e}", "ERROR")

        self._log("尝试从进程列表获取 PID...", "INFO")
        process_name = params.get("process_name", "com.android.commands.monkey.transsion")
        fallback_pid = self.adb.get_pid(serial, process_name)
        if fallback_pid:
            self._log(f"从进程列表获取到 PID: {fallback_pid}", "INFO")
        else:
            self._log("未能从进程列表获取 PID", "ERROR")
        return fallback_pid

    def _monitor_monkey(
        self,
        serial: str,
        pid: str,
        runtime_minutes: int,
        throttle_ms: int,
        max_restarts: int,
        params: Dict[str, Any],
    ) -> Tuple[str, Optional[str]]:
        """监控 Monkey 运行"""
        start_time = time.time()
        end_time = start_time + runtime_minutes * 60
        last_heartbeat = start_time
        restart_count = 0
        current_pid = pid
        process_name = params.get("process_name", "com.android.commands.monkey.transsion")
        events: List[str] = []

        log_dir = self.log_dir or "logs/aimonkey"
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        logcat_path = Path(log_dir) / "logcat.txt"

        self._log(f"开始监控: PID={pid}, runtime={runtime_minutes}min", "INFO")

        while time.time() < end_time:
            time.sleep(5)
            now = time.time()

            alive = self.adb.get_pid(serial, process_name)
            if not alive:
                self._log(f"进程未存活 (restart_count={restart_count})", "WARN")
                if restart_count >= max_restarts:
                    msg = f"monkey died after {restart_count} restarts"
                    events.append(msg)
                    self._log(msg, "ERROR")
                    break
                restart_count += 1
                self._log(f"尝试重启 ({restart_count}/{max_restarts})...", "INFO")
                events.append(f"restart {restart_count}")
                new_pid = self._start_monkey(serial, params)
                if new_pid:
                    current_pid = new_pid
                    self._log(f"重启成功: new PID={new_pid}", "INFO")
                else:
                    self._log("重启失败", "ERROR")
                continue

            self._collect_logcat(serial, logcat_path)

            if now - last_heartbeat >= 10:
                elapsed_min = int((now - start_time) / 60)
                progress = min(int((now - start_time) / (end_time - start_time) * 100), 100)
                self._log(f"进度: {progress}%, 运行时间: {elapsed_min}min", "INFO")
                self.set_progress(55 + progress // 4, f"运行中 {elapsed_min}min")
                last_heartbeat = now

        if time.time() >= end_time:
            msg = "runtime completed"
            events.append(msg)
            self._log(msg, "INFO")

        return ("; ".join(events) if events else "completed", current_pid)

    def _collect_logcat(self, serial: str, log_path: Path) -> None:
        """收集 logcat"""
        try:
            result = self.adb.shell(serial, ["logcat", "-d", "-t", "100"])
            if result.stdout:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(result.stdout)
        except Exception:
            pass

    def _stop_monkey(self, serial: str, pid: str) -> None:
        """停止 Monkey 进程"""
        if not pid:
            return
        try:
            self.adb.kill_process(serial, pid)
        except Exception as e:
            self._log(f"停止 monkey 失败: {e}", "WARN")
