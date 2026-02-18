import logging
import os
import re
import subprocess
import time
import tarfile
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from .adb_wrapper import AdbError, AdbWrapper
from .aimonkey_aee import AEEEntry, scan_aee_entries, scan_and_pull_aee_entries
from .aimonkey_risk import build_risk_summary, write_risk_summary
from .aimonkey_stages import AIMonkeyStage, stage_progress


@dataclass
class ExecutionContext:
    """Agent 执行上下文，包含执行所需的运行时信息"""
    api_url: str
    run_id: int
    host_id: int
    device_serial: str
    log_dir: str = ""

    def __post_init__(self):
        if not self.log_dir:
            self.log_dir = f"logs/runs/{self.run_id}"


@dataclass
class TaskResult:
    status: str
    exit_code: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    log_summary: Optional[str] = None
    artifact: Optional[Dict[str, Any]] = None


class TaskExecutor:
    # Registry: task_type → (module_path, class_name)
    # 使用动态路径解析，兼容两种运行模式：
    # 1. python -m agent.main (部署模式，包名为 agent)
    # 2. 直接运行或调试模式 (包名为 backend.agent)
    @staticmethod
    def _get_tools_module_path() -> str:
        """动态解析 tools 模块路径，兼容不同运行形态"""
        import sys
        # 检查是否以 agent 包模式运行
        if "agent.main" in sys.modules or any("agent." in m for m in sys.modules if m.startswith("agent")):
            return "agent.tools"
        return "backend.agent.tools"

    _TEST_CLASS_REGISTRY: Dict[str, Tuple[str, str]] = {
        "MONKEY": ("backend.agent.tools.monkey_test", "MonkeyTest"),
        "MTBF": ("backend.agent.tools.mtbf_test", "MtbfTest"),
        "DDR": ("backend.agent.tools.ddr_test", "DdrTest"),
        "GPU": ("backend.agent.tools.gpu_stress_test", "GpuStressTest"),
        "STANDBY": ("backend.agent.tools.standby_test", "StandbyTest"),
        "AIMONKEY": ("backend.agent.tools.aimonkey_test", "AIMonkeyTest"),
    }

    def __init__(self, adb: AdbWrapper) -> None:
        self.adb = adb
        self._safe_pattern = re.compile(r"^[\w@%:/._+\-]+$")
        self._logger = logging.getLogger(__name__)
        self._heartbeat_interval = 60
        self._context: Optional[ExecutionContext] = None
        self._log_buffer: List[str] = []
        self._progress: int = 0
        self._progress_message: str = ""

    @property
    def _api_url(self) -> str:
        return self._context.api_url if self._context else ""

    @property
    def _run_id(self) -> Optional[int]:
        return self._context.run_id if self._context else None

    def set_progress(self, progress: int, message: str = "") -> None:
        """设置进度并发送心跳"""
        self._progress = progress
        self._progress_message = message
        self._flush_logs()

    def _log(self, message: str, level: str = "INFO") -> None:
        """记录日志并发送到后端"""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level}] {message}"
        self._logger.info(message)
        self._log_buffer.append(log_line)
        # 保持缓冲区大小
        if len(self._log_buffer) > 200:
            self._log_buffer = self._log_buffer[-100:]
        # 立即发送日志
        self._flush_logs()

    def _flush_logs(self) -> None:
        """将缓冲区日志发送到后端"""
        if not self._api_url or not self._run_id:
            return
        try:
            payload: Dict[str, Any] = {
                "status": "RUNNING",
            }
            # 添加日志行
            if self._log_buffer:
                payload["log_lines"] = self._log_buffer[-50:]
                self._log_buffer = []
            # 添加进度信息
            if self._progress > 0 or self._progress_message:
                payload["progress"] = self._progress
                payload["progress_message"] = self._progress_message
            # 发送请求
            if len(payload) > 1:  # 至少有 status 和其他字段
                requests.post(
                    f"{self._api_url}/api/v1/agent/runs/{self._run_id}/heartbeat",
                    json=payload,
                    timeout=5,
                )
        except Exception:
            pass  # 发送失败不影响主流程

    def execute_task(self, task_type: str, params: Dict[str, Any], context: ExecutionContext) -> TaskResult:
        self._context = context
        try:
            # Priority 1: tool_id → dynamic tool loading
            tool_id = params.get("tool_id")
            if tool_id:
                return self._execute_tool(tool_id, params)

            # Priority 2: class registry → BaseTestCase subclasses
            task_name = task_type.upper()
            if task_name in self._TEST_CLASS_REGISTRY:
                return self._execute_registered_test(task_name, params)

            # Priority 3: legacy fallback → local script
            return self._run_local_script(params)
        except ValueError as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="INVALID_PARAM", error_message=str(exc))
        except AdbError as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="ADB_ERROR", error_message=str(exc))
        except subprocess.TimeoutExpired:
            return TaskResult(status="FAILED", exit_code=1, error_code="TIMEOUT", error_message="command timeout")
        except Exception as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="UNKNOWN", error_message=str(exc))
        finally:
            self._context = None

    def _execute_registered_test(self, task_name: str, params: Dict[str, Any]) -> TaskResult:
        """Instantiate a BaseTestCase subclass from the registry and run it."""
        import importlib

        module_path, class_name = self._TEST_CLASS_REGISTRY[task_name]
        # 动态调整模块路径，兼容 agent/main 和 backend.agent 两种运行形态
        tools_base = self._get_tools_module_path()
        module_path = module_path.replace("backend.agent.tools", tools_base)

        try:
            module = importlib.import_module(module_path)
        except ModuleNotFoundError as e:
            self._logger.error(f"Failed to import test module {module_path}: {e}")
            return TaskResult(
                status="FAILED",
                exit_code=1,
                error_code="MODULE_NOT_FOUND",
                error_message=f"Test module not found: {module_path}",
            )

        try:
            test_class = getattr(module, class_name)
        except AttributeError as e:
            self._logger.error(f"Failed to get test class {class_name} from {module_path}: {e}")
            return TaskResult(
                status="FAILED",
                exit_code=1,
                error_code="CLASS_NOT_FOUND",
                error_message=f"Test class not found: {class_name}",
            )

        os.makedirs(self._context.log_dir, exist_ok=True)

        test_case = test_class(
            adb_wrapper=self.adb,
            api_url=self._context.api_url,
            run_id=self._context.run_id,
            host_id=self._context.host_id,
            device_serial=self._context.device_serial,
            log_dir=self._context.log_dir,
        )

        default_params = test_case.get_default_params()
        exec_params = {**default_params, **params, **self._context_to_dict()}

        result = test_case.run(self._context.device_serial, exec_params)

        return TaskResult(
            status=result.status,
            exit_code=result.exit_code,
            error_code=result.error_code,
            error_message=result.error_message,
            log_summary=result.log_summary,
            artifact=result.artifact,
        )

    def _execute_tool(self, tool_id: int, params: Dict[str, Any]) -> TaskResult:
        """通过工具 ID 执行测试"""
        import importlib.util
        from backend.agent.test_framework import BaseTestCase, TestResult

        script_path = params.get("script_path")
        script_class = params.get("script_class")

        if not script_path or not script_class:
            return TaskResult(
                status="FAILED",
                exit_code=1,
                error_code="TOOL_NOT_FOUND",
                error_message="tool_id requires script_path and script_class"
            )

        tool_class = self._load_tool_class(script_path, script_class)
        if not tool_class:
            return TaskResult(
                status="FAILED",
                exit_code=1,
                error_code="TOOL_LOAD_FAILED",
                error_message=f"Failed to load {script_class} from {script_path}"
            )

        default_params = params.get("default_params", {})
        exec_params = {**default_params, **params}
        exec_params = {**exec_params, **self._context_to_dict()}

        os.makedirs(self._context.log_dir, exist_ok=True)

        test_case = tool_class(
            adb_wrapper=self.adb,
            api_url=self._context.api_url,
            run_id=self._context.run_id,
            host_id=self._context.host_id,
            device_serial=self._context.device_serial,
            log_dir=self._context.log_dir,
        )

        result = test_case.run(self._context.device_serial, exec_params)

        return TaskResult(
            status=result.status,
            exit_code=result.exit_code,
            error_code=result.error_code,
            error_message=result.error_message,
            log_summary=result.log_summary,
            artifact=result.artifact,
        )

    def _context_to_dict(self) -> Dict[str, Any]:
        """将执行上下文转换为字典，供工具使用"""
        if not self._context:
            return {}
        return {
            "api_url": self._context.api_url,
            "run_id": self._context.run_id,
            "host_id": self._context.host_id,
            "device_serial": self._context.device_serial,
            "log_dir": self._context.log_dir,
        }

    def _load_tool_class(self, script_path: str, class_name: str):
        """动态加载工具类"""
        import importlib.util

        # 简单的缓存
        cache_key = f"{script_path}:{class_name}"
        if hasattr(self, '_tool_class_cache') and cache_key in self._tool_class_cache:
            return self._tool_class_cache[cache_key]

        if not hasattr(self, '_tool_class_cache'):
            self._tool_class_cache = {}

        try:
            # 从文件加载模块
            spec = importlib.util.spec_from_file_location("tool_module", script_path)
            if spec is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # 获取类
            tool_class = getattr(module, class_name, None)

            # 检查是否继承 BaseTestCase
            from backend.agent.test_framework import BaseTestCase
            if tool_class and issubclass(tool_class, BaseTestCase):
                self._tool_class_cache[cache_key] = tool_class
                return tool_class

            return None
        except Exception as e:
            print(f"加载工具类失败: {e}")
            import traceback
            traceback.print_exc()
            return None

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

    def _to_bool(self, value: Any, default: bool) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"1", "true", "yes", "on"}:
                return True
            if lowered in {"0", "false", "no", "off"}:
                return False
        return default

    def _validate_aimonkey_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        runtime_minutes = int(params.get("runtime_minutes", 10080))
        if runtime_minutes <= 0:
            raise ValueError("runtime_minutes must be > 0")

        throttle_ms = int(params.get("throttle_ms", 500))
        if throttle_ms <= 0:
            raise ValueError("throttle_ms must be > 0")

        max_restarts = int(params.get("max_restarts", 1))
        if max_restarts < 0:
            raise ValueError("max_restarts must be >= 0")

        target_fill_percentage = int(params.get("target_fill_percentage", 60))
        if target_fill_percentage < 1 or target_fill_percentage > 95:
            raise ValueError("target_fill_percentage must be between 1 and 95")

        raw_run_id = params.get("run_id")
        run_id: Optional[int] = None
        if raw_run_id not in (None, ""):
            run_id = int(raw_run_id)
            if run_id <= 0:
                raise ValueError("run_id must be > 0 when provided")

        validated = dict(params)
        validated["runtime_minutes"] = runtime_minutes
        validated["throttle_ms"] = throttle_ms
        validated["max_restarts"] = max_restarts
        validated["target_fill_percentage"] = target_fill_percentage
        validated["run_id"] = run_id
        validated["api_url"] = str(params.get("api_url", "")).strip()
        validated["process_name"] = self._safe(
            params.get("process_name", "com.android.commands.monkey.transsion"),
            "process_name",
        )
        validated["wifi_ssid"] = self._safe_optional(params.get("wifi_ssid"), "wifi_ssid") or ""
        validated["wifi_password"] = self._safe_optional(params.get("wifi_password"), "wifi_password") or ""
        validated["enable_fill_storage"] = self._to_bool(params.get("enable_fill_storage"), True)
        validated["enable_clear_logs"] = self._to_bool(params.get("enable_clear_logs"), True)
        return validated

    def _aimonkey_stage_update(
        self,
        stage: AIMonkeyStage,
        api_url: str,
        run_id: Optional[int],
        message: str,
    ) -> None:
        progress = stage_progress(stage)
        self._log(f"[{stage.value}] {message}", "INFO")
        self._send_heartbeat(
            api_url,
            run_id,
            progress,
            f"{stage.value}: {message}",
        )

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
        try:
            validated_params = self._validate_aimonkey_params(params)
        except ValueError as exc:
            return TaskResult(status="FAILED", exit_code=1, error_code="INVALID_PARAM", error_message=str(exc))

        run_id = validated_params.get("run_id")
        api_url = validated_params.get("api_url", "")
        log_dir = validated_params.get("log_dir") or self._aimonkey_log_dir(run_id)

        # 设置日志上下文
        self._api_url = api_url
        self._run_id = run_id
        self._log_buffer = []

        runtime_minutes = validated_params["runtime_minutes"]
        throttle_ms = validated_params["throttle_ms"]
        max_restarts = validated_params["max_restarts"]

        self._aimonkey_stage_update(
            AIMonkeyStage.PRECHECK,
            api_url,
            run_id,
            f"AIMONKEY任务开始: device={serial}, runtime={runtime_minutes}min",
        )

        try:
            self._aimonkey_stage_update(AIMonkeyStage.PREPARE, api_url, run_id, "开始设备前置准备")
            if validated_params["enable_fill_storage"]:
                self._aimonkey_stage_update(
                    AIMonkeyStage.FILL_STORAGE,
                    api_url,
                    run_id,
                    f"资源填充已启用，目标占用={validated_params['target_fill_percentage']}%",
                )
            self._aimonkey_setup(serial, validated_params)
            self._aimonkey_stage_update(AIMonkeyStage.PREPARE, api_url, run_id, "设备前置准备完成")

            self._aimonkey_stage_update(AIMonkeyStage.RUN, api_url, run_id, "开始推送资源并启动Monkey")
            pid = self._aimonkey_start_monkey(serial, validated_params)
            if not pid:
                error_msg = "Failed to start monkey or capture PID"
                self._log(f"[{AIMonkeyStage.RUN.value}] 启动失败: {error_msg}", "ERROR")
                return TaskResult(
                    status="FAILED",
                    exit_code=1,
                    error_code="MONKEY_START_FAILED",
                    error_message=error_msg,
                )
            self._aimonkey_stage_update(AIMonkeyStage.RUN, api_url, run_id, f"Monkey进程已启动: PID={pid}")

            self._aimonkey_stage_update(AIMonkeyStage.MONITOR, api_url, run_id, "开始监控Monkey运行")
            summary, final_pid = self._aimonkey_monitor(
                serial, pid, runtime_minutes, throttle_ms, max_restarts, log_dir, validated_params, api_url, run_id
            )
            self._aimonkey_stage_update(AIMonkeyStage.MONITOR, api_url, run_id, f"监控结束: {summary}")

            self._aimonkey_stage_update(AIMonkeyStage.RISK_SCAN, api_url, run_id, "执行风险问题检查")
            aee_entries = scan_aee_entries(self.adb, serial)
            risk_summary = build_risk_summary(
                monitor_summary=summary,
                logcat_path=Path(log_dir) / "logcat.txt",
                aee_entries=aee_entries,
                restart_warn_threshold=int(validated_params.get("restart_warn_threshold", 1)),
            )
            risk_summary_path = Path(log_dir) / "risk_summary.json"
            write_risk_summary(risk_summary, risk_summary_path)
            self._log(
                f"风险检查完成: level={risk_summary['risk_level']}, events={risk_summary['counts']['events_total']}",
                "INFO",
            )
            self._aimonkey_stage_update(
                AIMonkeyStage.TEARDOWN,
                api_url,
                run_id,
                "结束测试，停止Monkey进程",
            )
            self._aimonkey_stop_monkey(serial, final_pid or pid)

            self._aimonkey_stage_update(AIMonkeyStage.EXPORT, api_url, run_id, "日志回传导出中")
            export_meta = self._aimonkey_collect_logs(serial, log_dir, aee_entries=aee_entries)
            self._log(
                f"日志导出完成: aee_scanned={export_meta['aee_scanned']}, "
                f"aee_pulled={export_meta['aee_pulled']}, bugreport={export_meta['bugreport_exported']}",
                "INFO",
            )
            artifact = self._build_log_artifact(log_dir, run_id)
            if artifact:
                self._log(
                    f"日志产物打包完成: {artifact['storage_uri']} ({artifact.get('size_bytes')} bytes)",
                    "INFO",
                )
            self._aimonkey_stage_update(AIMonkeyStage.TEARDOWN, api_url, run_id, "测试后置完成")

            self._log("任务完成", "INFO")
            final_summary = self._aimonkey_result_summary(summary, risk_summary)
            return TaskResult(
                status="FINISHED",
                exit_code=0,
                log_summary=self._tail(final_summary),
                artifact=artifact,
            )
        except AdbError as exc:
            error_msg = str(exc)
            self._log(f"ADB错误: {error_msg}", "ERROR")
            return TaskResult(status="FAILED", exit_code=1, error_code="ADB_ERROR", error_message=error_msg)
        except Exception as exc:
            self._logger.exception("aimonkey_failed")
            self._log(f"未知错误: {str(exc)}", "ERROR")
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
        self._log("检查root权限...", "INFO")
        self._ensure_root_access(serial)
        self._log("Root权限已获取", "INFO")

        self._log("启用开发者选项...", "INFO")
        self._enable_dev_settings(serial)

        self._log("启动mobile logger...", "INFO")
        self._start_mobile_logger(serial)

        wifi_ssid = params.get("wifi_ssid", "")
        if wifi_ssid:
            self._log(f"连接WiFi: {wifi_ssid}...", "INFO")
            self._connect_wifi(serial, wifi_ssid, params.get("wifi_password", ""))

        if bool(params.get("enable_fill_storage", True)):
            target_pct = int(params.get("target_fill_percentage", 60))
            self._log(f"填充存储到{target_pct}%...", "INFO")
            self._fill_storage(serial, target_pct)
            self._log("存储填充完成", "INFO")

        if bool(params.get("enable_clear_logs", True)):
            self._log("清理设备日志...", "INFO")
            self._clear_device_logs(serial)

    def _ensure_root_access(self, serial: str, max_attempts: int = 3) -> None:
        """确保设备具有 root 权限，失败则抛出 AdbError"""
        import subprocess

        for attempt in range(max_attempts):
            try:
                # 检查当前是否已有 root 权限
                result = self.adb.shell(serial, ["id", "-u"])
                if result.stdout.strip() == "0":
                    self._logger.debug("already_root: %s", serial)
                    return

                # 尝试通过 adb root 命令获取 root（这是客户端命令，不是 shell 命令）
                try:
                    subprocess.run(
                        [self.adb.adb_path, "-s", serial, "root"],
                        capture_output=True,
                        text=True,
                        timeout=10
                    )
                    time.sleep(3)
                except (subprocess.TimeoutExpired, Exception) as e:
                    self._logger.debug("adb_root_command_failed: %s", e)
                    # 继续尝试，某些设备可能不支持 adb root 命令

                # 再次检查权限
                result = self.adb.shell(serial, ["id", "-u"])
                if result.stdout.strip() == "0":
                    self._logger.info("root_access_granted: %s", serial)
                    return
            except AdbError as exc:
                self._logger.debug("root_check_failed: %s - %s", serial, exc)
                if attempt == max_attempts - 1:
                    raise
                time.sleep(2)
        raise AdbError(f"Failed to obtain root access for {serial}")

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
            safe_pwd = self._safe_optional(password, "wifi_password") or ""
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
        # 如果 resource_dir 是 Windows 格式路径（如 /d/... 或 D:\...），在 Linux 上使用环境变量
        if resource_dir and (resource_dir.startswith("/mnt/") or ":" in resource_dir or
                             (len(resource_dir) > 2 and resource_dir[0] == "/" and resource_dir[2] == "/")):
            self._log(f"检测到Windows路径格式: {resource_dir}, 使用环境变量", "INFO")
            resource_dir = ""
        if not resource_dir:
            # 优先从环境变量读取，支持 Linux/Windows 不同部署路径
            resource_dir = os.environ.get("AIMONKEY_RESOURCE_DIR", "")
        if not resource_dir:
            # 默认路径：尝试基于当前文件位置推导
            try:
                resource_dir = str(Path(__file__).resolve().parents[3] / "Monkey_test" / "AIMonkeyTest_2025mtk")
            except Exception:
                resource_dir = "/opt/stability-test-agent/resources/aimonkey"

        self._log(f"资源目录: {resource_dir}", "INFO")

        # 基础文件推送
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
                    self._adb_push_with_timeout(serial, local_path, remote, timeout=300)
                    self.adb.shell(serial, ["chmod", "755", remote])
                    self._log(f"推送 {fname} 成功", "INFO")
                except AdbError as exc:
                    self._log(f"推送 {fname} 失败: {exc}", "WARN")
            else:
                self._log(f"文件不存在: {local_path}", "WARN")

        # 推送架构库 (arm64-v8a, armeabi-v7a)
        self._log("推送架构库...", "INFO")
        for arch_dir in ["arm64-v8a", "armeabi-v7a"]:
            local_arch_path = os.path.join(resource_dir, arch_dir)
            if os.path.isdir(local_arch_path):
                try:
                    remote_arch_path = f"/data/local/tmp/{arch_dir}"
                    self._log(f"推送 {arch_dir}...", "INFO")
                    self._adb_push_with_timeout(serial, local_arch_path, remote_arch_path, timeout=300)
                    self._log(f"推送 {arch_dir} 成功", "INFO")
                except AdbError as exc:
                    self._log(f"推送 {arch_dir} 失败: {exc}", "WARN")

        # 推送 aimonkey.apk
        apk_path = os.path.join(resource_dir, "aimonkey.apk")
        self._log(f"检查APK: {apk_path}", "INFO")
        if os.path.exists(apk_path):
            try:
                remote_apk_path = "/data/local/tmp/monkey.apk"
                self._log("推送 aimonkey.apk...", "INFO")
                self._adb_push_with_timeout(serial, apk_path, remote_apk_path, timeout=300)
                self._log("推送 aimonkey.apk 成功", "INFO")
            except AdbError as exc:
                self._log(f"推送 aimonkey.apk 失败: {exc}", "WARN")
        else:
            self._log(f"APK不存在: {apk_path}", "WARN")

        # 启动 aimwd
        self._log("启动 aimwd...", "INFO")
        try:
            self._adb_shell_with_timeout(serial, ["sh", "-c", "nohup /data/local/tmp/aimwd >/dev/null 2>&1 &"], timeout=30)
            self._log("启动 aimwd 成功", "INFO")
        except AdbError as exc:
            self._log(f"启动 aimwd 失败: {exc}", "WARN")

        # 启动 aimonkey 并返回 PID
        self._log("启动 aimonkey 进程...", "INFO")
        return self._start_aimonkey_process(serial, params)

    def _adb_shell_with_timeout(
        self, serial: str, cmd: List[str], timeout: float = 60.0
    ) -> subprocess.CompletedProcess:
        """执行 ADB shell 命令，带超时控制"""
        import subprocess

        full_cmd = [self.adb.adb_path, "-s", serial, "shell"] + cmd
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise AdbError(result.stderr.strip() or result.stdout.strip())
        return result

    def _adb_push_with_timeout(
        self, serial: str, local_path: str, remote_path: str, timeout: float = 300.0
    ) -> subprocess.CompletedProcess:
        """推送文件到设备，带超时控制"""
        import subprocess

        full_cmd = [self.adb.adb_path, "-s", serial, "push", local_path, remote_path]
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise AdbError(result.stderr.strip() or result.stdout.strip())
        return result

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
            result = self._adb_shell_with_timeout(serial, ["sh", "-c", monkey_cmd], timeout=60)
            pid = result.stdout.strip().splitlines()[-1] if result.stdout else ""
            self._log(f"命令输出: {result.stdout}", "INFO")
            if pid and pid.isdigit():
                self._log(f"获取到PID: {pid}", "INFO")
                return pid
            else:
                self._log(f"PID格式异常: {pid}", "WARN")
        except AdbError as exc:
            self._log(f"启动命令执行失败: {exc}", "ERROR")

        self._log("尝试从进程列表获取PID...", "INFO")
        fallback_pid = self.adb.get_pid(serial, "com.android.commands.monkey.transsion")
        if fallback_pid:
            self._log(f"从进程列表获取到PID: {fallback_pid}", "INFO")
        else:
            self._log("未能从进程列表获取PID", "ERROR")
        return fallback_pid

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
        """监控 Monkey 运行：5秒存活检测、10秒心跳、自动重启，返回(摘要, 最终PID)"""
        start_time = time.time()
        end_time = start_time + runtime_minutes * 60
        last_heartbeat = start_time
        restart_count = 0
        current_pid = pid
        process_name = params.get("process_name", "com.android.commands.monkey.transsion")
        events: list[str] = []
        recent_log_lines: list[str] = []  # 存储最近的日志行
        last_log_send = start_time  # 上次发送日志时间

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
                new_pid = self._aimonkey_start_monkey(serial, params)
                if new_pid:
                    current_pid = new_pid
                    self._log(f"重启成功: new PID={new_pid}", "INFO")
                else:
                    self._log("重启失败", "ERROR")
                continue

            # 收集日志并获取新行
            new_lines = self._collect_realtime_logcat(serial, logcat_path)
            if new_lines:
                recent_log_lines.extend(new_lines)
                # 保持最多100行
                if len(recent_log_lines) > 100:
                    recent_log_lines = recent_log_lines[-100:]

            # 每10秒发送一次心跳（原来是60秒）
            if now - last_heartbeat >= 10:
                elapsed_min = int((now - start_time) / 60)
                progress = min(int((now - start_time) / (end_time - start_time) * 100), 100)
                self._log(f"进度: {progress}%, 运行时间: {elapsed_min}min", "INFO")
                self._send_heartbeat(api_url, run_id, progress, f"running {elapsed_min}min", recent_log_lines)
                last_heartbeat = now
                recent_log_lines = []  # 发送后清空

        if time.time() >= end_time:
            msg = "runtime completed"
            events.append(msg)
            self._log(msg, "INFO")

        return ("; ".join(events) if events else "completed", current_pid)

    def _collect_realtime_logcat(self, serial: str, log_path: Path) -> List[str]:
        """收集实时 logcat 追加到文件，返回新的日志行"""
        try:
            result = self.adb.shell(serial, ["logcat", "-d", "-t", "100"])
            if not result.stdout:
                return []

            lines = result.stdout.strip().splitlines()
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(result.stdout)

            # 返回新的日志行（过滤掉空行）
            return [line.strip() for line in lines if line.strip()]
        except AdbError:
            return []

    def _send_heartbeat(self, api_url: str, run_id: Optional[int], progress: int, message: str, log_lines: Optional[List[str]] = None) -> None:
        """向后端发送进度心跳，附带日志行用于实时推送"""
        if not api_url or not run_id:
            return
        try:
            payload = {
                "status": "RUNNING",
                "progress": progress,
                "log_summary": message,
            }
            # 添加日志行用于实时 WebSocket 推送
            if log_lines:
                payload["log_lines"] = log_lines[-50:]  # 限制最多发送最近50行
            requests.post(
                f"{api_url}/api/v1/agent/runs/{run_id}/heartbeat",
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            self._logger.debug("heartbeat_failed: %s", exc)

    def _aimonkey_collect_logs(
        self,
        serial: str,
        log_dir: str,
        aee_entries: Optional[Sequence[AEEEntry]] = None,
    ) -> Dict[str, Any]:
        """收集 AEE、mobilelog、bugreport 等日志"""
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        export_meta: Dict[str, Any] = {
            "aee_scanned": 0,
            "aee_pulled": 0,
            "bugreport_exported": False,
        }

        scanned_entries, pulled_records = scan_and_pull_aee_entries(
            self.adb,
            serial,
            log_dir,
            entries=aee_entries,
        )
        export_meta["aee_scanned"] = len(scanned_entries)
        export_meta["aee_pulled"] = sum(1 for item in pulled_records if item.get("pulled"))

        try:
            debuglogger_path = Path(log_dir) / "debuglogger"
            debuglogger_path.mkdir(exist_ok=True)
            self.adb.pull(serial, "/data/debuglogger", str(debuglogger_path))
        except AdbError as exc:
            self._logger.debug("pull_failed: /data/debuglogger - %s", exc)

        try:
            bugreport_path = "/sdcard/bugreport-aimonkey.txt"
            self.adb.shell(serial, ["bugreport", bugreport_path])
            self.adb.pull(serial, bugreport_path, str(Path(log_dir) / "bugreport.txt"))
            export_meta["bugreport_exported"] = True
        except AdbError as exc:
            self._logger.debug("bugreport_failed: %s", exc)
        return export_meta

    def _build_log_artifact(self, log_dir: str, run_id: Optional[int]) -> Optional[Dict[str, Any]]:
        base_dir = Path(log_dir)
        if not base_dir.exists() or not base_dir.is_dir():
            return None
        archive_name = str(run_id) if run_id else base_dir.name
        archive_path = base_dir.parent / f"{archive_name}.tar.gz"
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(base_dir, arcname=base_dir.name)

            checksum = self._sha256_file(archive_path)
            return {
                "storage_uri": f"file://{archive_path.resolve()}",
                "size_bytes": archive_path.stat().st_size,
                "checksum": checksum,
            }
        except Exception as exc:
            self._logger.warning("build_log_artifact_failed: %s", exc)
            return None

    def _aimonkey_result_summary(self, monitor_summary: str, risk_summary: Dict[str, Any]) -> str:
        counts = risk_summary.get("counts") if isinstance(risk_summary, dict) else {}
        if not isinstance(counts, dict):
            counts = {}
        risk_level = str(risk_summary.get("risk_level", "UNKNOWN")) if isinstance(risk_summary, dict) else "UNKNOWN"
        events_total = counts.get("events_total", 0)
        restart_count = counts.get("restart_count", 0)
        aee_entries = counts.get("aee_entries", 0)
        return (
            f"monitor={monitor_summary}; "
            f"risk={risk_level}; "
            f"events={events_total}; "
            f"restarts={restart_count}; "
            f"aee_entries={aee_entries}"
        )

    def _sha256_file(self, file_path: Path) -> str:
        hasher = hashlib.sha256()
        with file_path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    def _run_aimonkey_test(self, device_serial: str, params: Dict[str, Any]) -> TaskResult:
        """使用 AIMonkeyTest 类执行 AIMONKEY 任务"""
        try:
            from .tools.aimonkey_test import AIMonkeyTest

            log_dir = f"logs/runs/{self._context.run_id}"
            os.makedirs(log_dir, exist_ok=True)

            test_case = AIMonkeyTest(
                adb_wrapper=self.adb,
                api_url=self._api_url,
                run_id=self._context.run_id,
                host_id=self._context.host_id,
                device_serial=device_serial,
                log_dir=log_dir,
            )

            result = test_case.run(device_serial, params)

            return TaskResult(
                status=result.status,
                exit_code=result.exit_code,
                error_code=result.error_code,
                error_message=result.error_message,
                log_summary=result.log_summary,
                artifact=result.artifact,
            )
        except Exception as e:
            self._logger.exception("aimonkey_test_failed")
            return TaskResult(
                status="FAILED",
                exit_code=1,
                error_code="AIMONKEY_TEST_ERROR",
                error_message=str(e),
            )
