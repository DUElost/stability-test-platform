# -*- coding: utf-8 -*-
"""
测试框架基类
所有测试用例应继承此类，享受平台提供的通用能力

使用方式：
    class MyTest(BaseTestCase):
        TEST_TYPE = "MY_TEST"

        def execute(self, serial: str, params: Dict) -> TestResult:
            ...

    if __name__ == "__main__":
        BaseTestCase.main(MyTest)
"""

import os
import sys
import time
import json
import logging
import subprocess
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, FrozenSet, Optional, List
from pathlib import Path
from datetime import datetime

from .test_stages import TestStage, stage_progress, STANDARD_STAGES


# ==================== 基础数据类 ====================

@dataclass
class TestResult:
    """测试执行结果"""
    status: str = "FINISHED"           # FINISHED / FAILED / RUNNING
    exit_code: int = 0                 # 0=成功
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    log_summary: Optional[str] = None
    artifact: Optional[Dict[str, Any]] = None  # 产物信息
    metrics: Dict[str, Any] = field(default_factory=dict)  # 指标数据


@dataclass
class RiskEvent:
    """风险事件"""
    event_type: str      # FATAL / ANR / CRASH / WARN
    timestamp: str
    device: str
    description: str
    log_path: Optional[str] = None
    raw_data: Optional[Dict] = None


# ==================== 基础框架类 ====================

class BaseTestCase(ABC):
    """
    测试用例基类

    平台提供的能力：
    - 设备 ADB 操作 (adb.shell, adb.push, adb.pull)
    - 实时日志记录与上报
    - 心跳维持
    - 风险扫描
    - 日志收集
    - 异常处理
    """

    TEST_TYPE: str = "BASE"  # 子类必须定义

    # Stages this test type uses. Subclasses override to skip stages.
    STAGES: FrozenSet[TestStage] = STANDARD_STAGES

    # 运行时上下文（由 Agent 注入）
    api_url: str = ""
    run_id: int = 0
    host_id: int = 0
    device_serial: str = ""
    log_dir: str = ""

    # ADB 包装器（由 Agent 注入）
    adb: Any = None

    # 日志缓冲区
    _log_buffer: List[str] = []
    _log_level: str = "INFO"

    # 心跳控制
    _last_heartbeat: float = 0
    HEARTBEAT_INTERVAL: int = 10  # 秒
    _should_stop: bool = False

    def __init__(self, adb_wrapper=None, **context):
        """
        初始化测试用例

        Args:
            adb_wrapper: ADB 包装器实例
            **context: 运行时上下文 (api_url, run_id, host_id, device_serial, log_dir)
        """
        self.adb = adb_wrapper
        for key, value in context.items():
            setattr(self, key, value)

        # 确保日志目录存在
        if self.log_dir:
            os.makedirs(self.log_dir, exist_ok=True)

        # Stage tracking
        self._current_stage: Optional[TestStage] = None

    def enter_stage(self, stage: TestStage, message: str = "") -> bool:
        """
        Transition to a new stage.
        Returns False (and skips) if the stage is not in this test's STAGES set.
        """
        if stage not in self.STAGES:
            return False
        self._current_stage = stage
        progress = stage_progress(stage)
        display_msg = message or stage.value
        self.set_progress(progress, f"{stage.value}: {display_msg}")
        self._log(f"[{stage.value}] {display_msg}")
        return True

    # ==================== 抽象方法（子类必须实现） ====================

    @abstractmethod
    def execute(self, serial: str, params: Dict[str, Any]) -> TestResult:
        """
        执行核心测试逻辑

        Args:
            serial: 设备序列号
            params: 测试参数

        Returns:
            TestResult: 执行结果
        """
        pass

    # ==================== 可选方法（子类可重写） ====================

    def setup(self, serial: str, params: Dict[str, Any]) -> None:
        """
        设备前置配置
        可重写自定义设备配置逻辑
        默认实现：获取 root、启用开发者选项、启动日志服务
        """
        self._log("执行默认前置配置...")

        # 获取 root 权限
        if self._ensure_root(serial):
            self._log("获取 root 权限成功")
        else:
            self._log("无法获取 root 权限，某些功能可能受限", "WARN")

        # 启用开发者选项
        self._run_shell(serial, ["settings", "put", "global", "development_settings_enabled", "1"])

        # 启动日志服务
        self._start_mobile_logger(serial)

        self._log("前置配置完成")

    def teardown(self, serial: str, params: Dict[str, Any]) -> None:
        """
        测试后置处理
        可重写自定义清理逻辑
        """
        pass

    def collect(self, serial: str, log_dir: str) -> Dict[str, Any]:
        """
        自定义结果收集
        可重写实现特定测试的日志收集
        默认实现：收集 AEE 日志
        """
        collected = {}

        # 收集 AEE 日志
        aee_logs = self._collect_aee_logs(serial, log_dir)
        if aee_logs:
            collected["aee_logs"] = aee_logs
            self._log(f"收集到 {len(aee_logs)} 个 AEE 日志")

        return collected

    def scan_risks(self, serial: str) -> List[RiskEvent]:
        """
        风险扫描
        可重写实现特定的风险检测逻辑
        默认实现：扫描 fatal/ANR 关键词
        """
        return self._scan_risk_keywords(serial, "fatal")

    def get_default_params(self) -> Dict[str, Any]:
        """
        获取默认参数
        用于前端表单默认值
        """
        return {}

    # ==================== 通用能力（平台提供） ====================

    # 进度信息
    _progress: int = 0
    _progress_message: str = ""

    def set_progress(self, progress: int, message: str = "") -> None:
        """设置进度并触发心跳上报"""
        self._progress = progress
        self._progress_message = message
        self._maybe_send_heartbeat()

    def _log(self, message: str, level: str = "INFO") -> None:
        """记录日志并缓存"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level}] {message}"
        self._log_buffer.append(log_line)

        # 打印到标准输出
        print(log_line)

        # 尝试通过心跳上报日志
        self._maybe_send_heartbeat()

    def _maybe_send_heartbeat(self, force: bool = False) -> None:
        """发送心跳（带节流）"""
        if not self.api_url or not self.run_id:
            return

        now = time.time()

        if force or (now - self._last_heartbeat >= self.HEARTBEAT_INTERVAL):
            try:
                import requests
                payload: Dict[str, Any] = {
                    "status": "RUNNING",
                }
                # 添加日志行
                log_lines = self._log_buffer[-20:] if self._log_buffer else []
                if log_lines:
                    payload["log_lines"] = log_lines
                    self._log_buffer = self._log_buffer[-40:]  # 保留部分日志
                # 添加进度信息
                if self._progress > 0 or self._progress_message:
                    payload["progress"] = self._progress
                    payload["progress_message"] = self._progress_message

                requests.post(
                    f"{self.api_url}/api/v1/agent/runs/{self.run_id}/heartbeat",
                    json=payload,
                    timeout=5
                )
                self._last_heartbeat = now
            except Exception:
                pass

    def _ensure_root(self, serial: str) -> bool:
        """确保设备有 root 权限"""
        try:
            result = self.adb.shell(serial, ["id", "-u"])
            if result.returncode == 0 and "0" in result.stdout:
                return True

            # 尝试获取 root
            self.adb.root(serial)
            time.sleep(3)

            # 重新检查
            result = self.adb.shell(serial, ["id", "-u"])
            return result.returncode == 0 and "0" in result.stdout
        except Exception as e:
            self._log(f"获取 root 权限失败: {e}", "WARN")
            return False

    def _run_shell(self, serial: str, cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """执行 shell 命令"""
        return self.adb.shell(serial, cmd, timeout=timeout)

    def _start_mobile_logger(self, serial: str) -> bool:
        """启动移动日志服务"""
        try:
            # MTK 平台
            self.adb.shell(serial, [
                "am", "broadcast", "-a", "com.debug.loggerui.ADB_CMD",
                "-e", "cmd_name", "start", "--ei", "cmd_target", "1",
                "-n", "com.debug.loggerui/.framework.LogReceiver"
            ])
            self._log("移动日志服务已启动")
            return True
        except Exception as e:
            self._log(f"启动日志服务失败: {e}", "WARN")
            return False

    def _push_file(self, serial: str, local: str, remote: str) -> bool:
        """推送文件到设备"""
        try:
            self.adb.push(serial, local, remote)
            return True
        except Exception as e:
            self._log(f"推送文件失败: {e}", "ERROR")
            return False

    def _pull_file(self, serial: str, remote: str, local: str) -> bool:
        """从设备拉取文件"""
        try:
            self.adb.pull(serial, remote, local)
            return True
        except Exception as e:
            self._log(f"拉取文件失败: {e}", "ERROR")
            return False

    def _collect_aee_logs(self, serial: str, output_dir: str) -> List[str]:
        """收集 AEE 日志"""
        aee_paths = ["/data/aee_exp/", "/data/vendor/aee_exp/"]
        collected = []

        for aee_path in aee_paths:
            try:
                result = self.adb.shell(serial, ["ls", "-la", aee_path])
                if result.returncode == 0 and result.stdout:
                    # 查找新创建的目录（带时间戳的）
                    lines = result.stdout.strip().split("\n")
                    for line in lines:
                        if "d" in line and "aee_" in line:
                            parts = line.split()
                            if len(parts) >= 8:
                                folder_name = parts[-1]
                                remote_path = os.path.join(aee_path, folder_name)
                                local_path = os.path.join(output_dir, folder_name)

                                # 拉取到本地
                                if self._pull_file(serial, remote_path, local_path):
                                    collected.append(local_path)
            except Exception as e:
                self._log(f"收集 AEE 日志失败: {e}", "WARN")

        return collected

    def _scan_risk_keywords(self, serial: str, log_pattern: str = "fatal") -> List[RiskEvent]:
        """扫描风险关键词"""
        events = []

        # 扫描 AEE 目录
        aee_paths = ["/data/aee_exp/", "/data/vendor/aee_exp/"]

        for aee_path in aee_paths:
            try:
                result = self.adb.shell(serial, ["find", aee_path, "-name", f"*{log_pattern}*", "-type", "d"])
                if result.returncode == 0 and result.stdout:
                    for line in result.stdout.strip().split("\n"):
                        if line:
                            events.append(RiskEvent(
                                event_type="FATAL",
                                timestamp=datetime.now().isoformat(),
                                device=serial,
                                description=f"Found: {line}",
                                log_path=line
                            ))
            except Exception:
                pass

        return events

    # ==================== 主入口 ====================

    @classmethod
    def main(cls, test_class):
        """主入口，供独立调试使用"""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--serial", required=True)
        parser.add_argument("--params", default="{}")
        parser.add_argument("--log-dir", default="logs/test")
        parser.add_argument("--api-url", default="")
        parser.add_argument("--run-id", type=int, default=0)
        args = parser.parse_args()

        params = json.loads(args.params)

        # 创建测试实例（需要注入 ADB 包装器）
        # 注意：独立运行时需要自己提供 ADB 包装器
        test = test_class(
            serial=args.serial,
            log_dir=args.log_dir,
            params=params,
            api_url=args.api_url,
            run_id=args.run_id,
        )

        # 执行测试
        result = test.run(args.serial, params)

        # 输出结果
        print(json.dumps({
            "status": result.status,
            "exit_code": result.exit_code,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "log_summary": result.log_summary,
        }))

        sys.exit(result.exit_code)

    def run(self, serial: str, params: Dict[str, Any]) -> TestResult:
        """
        运行测试的完整流程（stage-aware）

        1. PRECHECK   - 设备连接检测
        2. PREPARE    - 前置配置 (setup)
        3. RUN        - 执行测试 (execute)
        4. RISK_SCAN  - 风险扫描 (scan_risks)
        5. EXPORT     - 结果收集 (collect)
        6. TEARDOWN   - 后置处理 (teardown)
        7. POST_TEST  - 测试后置清理
        """
        try:
            self._log(f"[{self.TEST_TYPE}] 测试开始", "INFO")

            # 1. PRECHECK
            self.enter_stage(TestStage.PRECHECK, "设备连接检测")

            # 2. PREPARE + setup (always called)
            self.enter_stage(TestStage.PREPARE, "执行前置配置")
            self.setup(serial, params)

            # 3. RUN + execute (with heartbeat thread)
            self.enter_stage(TestStage.RUN, "开始执行测试")
            heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            heartbeat_thread.start()

            result = self.execute(serial, params)

            # 4. RISK_SCAN (conditional)
            if self.enter_stage(TestStage.RISK_SCAN, "执行风险扫描"):
                risks = self.scan_risks(serial)
                if risks:
                    self._log(f"发现 {len(risks)} 个风险事件", "WARN")
                    result.metrics["risk_count"] = len(risks)
                    result.metrics["risks"] = [
                        {"type": r.event_type, "desc": r.description}
                        for r in risks
                    ]

            # 5. EXPORT (conditional)
            if self.enter_stage(TestStage.EXPORT, "收集测试结果"):
                if self.log_dir:
                    result.metrics.update(self.collect(serial, self.log_dir))

            # 6. TEARDOWN (always called)
            self.enter_stage(TestStage.TEARDOWN, "执行后置处理")
            self.teardown(serial, params)

            # 7. POST_TEST
            self.enter_stage(TestStage.POST_TEST, "测试后置清理")

            self._log(f"[{self.TEST_TYPE}] 测试完成: {result.status}", "INFO")

            # 设置日志摘要
            if not result.log_summary:
                result.log_summary = "\n".join(self._log_buffer[-50:])

            # 强制发送最后一次心跳
            self._maybe_send_heartbeat(force=True)

            # 停止心跳线程
            self._should_stop = True

            return result

        except Exception as e:
            self._log(f"测试执行异常: {str(e)}", "ERROR")
            import traceback
            traceback.print_exc()
            return TestResult(
                status="FAILED",
                exit_code=1,
                error_code="TEST_EXCEPTION",
                error_message=str(e),
                log_summary="\n".join(self._log_buffer[-50:])
            )

    def _heartbeat_loop(self) -> None:
        """心跳循环"""
        while not self._should_stop:
            time.sleep(self.HEARTBEAT_INTERVAL)
            self._maybe_send_heartbeat(force=True)

    def stop(self) -> None:
        """停止测试"""
        self._should_stop = True
