# -*- coding: utf-8 -*-
"""AIMonkey 进程守护 Pipeline Action.

此 Action 仅负责核心监控循环（进程存活检测 + 自动重启 + logcat 收集），
其余阶段由 builtin: actions 在 pipeline_def 中编排完成：

  prepare:
    - builtin:check_device
    - builtin:ensure_root
    - builtin:setup_device_commands   (开发者选项 / mobile logger / WiFi)
    - builtin:fill_storage            (target_percentage: 60)
    - builtin:push_resources          (aim / aimwd / aim.jar / monkey.apk)
    - builtin:start_process           (nohup /data/local/tmp/aim ... & echo $!)
  execute:
    - tool:aimonkey_monitor           (本 Action — 监控 + 自动重启)
  post_process:
    - builtin:scan_aee
    - builtin:aee_extract
    - builtin:log_scan
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..pipeline_engine import PipelineAction, StepContext, StepResult


class AIMonkeyMonitorAction(PipelineAction):
    """监控 AIMonkey 进程，按策略自动重启，并定期收集 logcat。

    从 ``ctx.shared`` 取上游 ``start_process`` 步骤写入的 ``pid``；
    若未找到则从 ``ctx.params["pid"]`` 读取。
    """

    TOOL_CATEGORY = "AIMONKEY"
    TOOL_DESCRIPTION = "AIMonkey 进程守护：存活检测、自动重启、logcat 收集。"

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        return {
            "pid_from_step": "start_monkey",          # shared key from start_process step
            "process_name": "com.android.commands.monkey.transsion",
            "runtime_minutes": 10080,
            "max_restarts": 1,
            "check_interval": 5,
            "log_interval": 60,
            "restart_command": (
                "nohup /data/local/tmp/aim --pkg-blacklist-file /sdcard/blacklist.txt "
                "--smartuiautomator true --hprof --ignore-crashes --ignore-security-exceptions "
                "--ignore-timeouts --throttle 500 --runtime-minutes 10080 "
                "--switchuimode -v >/dev/null 2>&1 & echo $!"
            ),
            "logcat_lines": 100,
            "log_dir": "",
        }

    def run(self, ctx: StepContext) -> StepResult:
        # Resolve initial PID from shared store (set by builtin:start_process)
        pid_from_step = ctx.params.get("pid_from_step", "start_monkey")
        pid: Optional[str] = (
            ctx.shared.get(pid_from_step, {}).get("pid")
            or ctx.params.get("pid")
        )
        if not pid:
            return StepResult(
                success=False,
                exit_code=1,
                error_message=f"No PID found (pid_from_step={pid_from_step!r}). "
                              "Ensure builtin:start_process runs first.",
            )

        process_name = ctx.params.get("process_name", "com.android.commands.monkey.transsion")
        runtime_minutes = int(ctx.params.get("runtime_minutes", 10080))
        max_restarts = int(ctx.params.get("max_restarts", 1))
        check_interval = int(ctx.params.get("check_interval", 5))
        log_interval = int(ctx.params.get("log_interval", 60))
        restart_command = ctx.params.get("restart_command", "")
        logcat_lines = int(ctx.params.get("logcat_lines", 100))
        log_dir = ctx.params.get("log_dir") or (
            f"logs/runs/{ctx.run_id}" if ctx.run_id else "logs/runs/local"
        )
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        logcat_path = Path(log_dir) / "logcat.txt"

        ctx.logger.info(
            f"开始监控: PID={pid}, process={process_name}, "
            f"runtime={runtime_minutes}min, max_restarts={max_restarts}"
        )

        start_time = time.time()
        end_time = start_time + runtime_minutes * 60
        last_log_time = start_time
        restart_count = 0
        current_pid = pid
        events: List[str] = []

        while time.time() < end_time:
            time.sleep(check_interval)
            now = time.time()

            # 进程存活检测
            alive_pid = self._get_pid_by_name(ctx, process_name)
            if not alive_pid:
                ctx.logger.warn(f"进程未存活 (restart_count={restart_count}/{max_restarts})")
                if restart_count >= max_restarts:
                    msg = f"monkey died after {restart_count} restarts — stopping"
                    events.append(msg)
                    ctx.logger.error(msg)
                    break
                restart_count += 1
                new_pid = self._restart_process(ctx, restart_command, process_name)
                if new_pid:
                    current_pid = new_pid
                    events.append(f"restart {restart_count}: new PID={new_pid}")
                    ctx.logger.info(f"重启成功: new PID={new_pid}")
                else:
                    events.append(f"restart {restart_count}: failed")
                    ctx.logger.error("重启失败")
                continue

            # 定期 logcat 收集
            if now - last_log_time >= log_interval:
                self._collect_logcat(ctx, logcat_path, logcat_lines)
                elapsed_min = int((now - start_time) / 60)
                progress_pct = min(int((now - start_time) / (end_time - start_time) * 100), 99)
                ctx.logger.info(f"运行中: {elapsed_min}min elapsed ({progress_pct}%)")
                last_log_time = now

        elapsed_total = int(time.time() - start_time)
        if time.time() >= end_time:
            events.append(f"runtime completed after {elapsed_total}s")
            ctx.logger.info(f"目标时长已达到: {elapsed_total}s")

        # 写入 shared 供下游步骤使用
        ctx.shared["aimonkey_monitor"] = {
            "final_pid": current_pid,
            "restart_count": restart_count,
            "elapsed_sec": elapsed_total,
            "events": events,
        }

        return StepResult(
            success=True,
            exit_code=0,
            metrics={
                "restart_count": restart_count,
                "elapsed_sec": elapsed_total,
                "events": "; ".join(events),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_pid_by_name(self, ctx: StepContext, process_name: str) -> Optional[str]:
        try:
            result = ctx.adb.shell(ctx.serial, ["pgrep", "-f", process_name])
            stdout = (getattr(result, "stdout", "") or "").strip()
            return stdout.splitlines()[0].strip() if stdout else None
        except Exception:
            return None

    def _restart_process(
        self, ctx: StepContext, command: str, process_name: str
    ) -> Optional[str]:
        if not command:
            return None
        try:
            result = ctx.adb.shell(ctx.serial, ["sh", "-c", command], timeout=60)
            stdout = (getattr(result, "stdout", "") or "").strip()
            pid = stdout.splitlines()[-1].strip() if stdout else ""
            if pid and pid.isdigit():
                return pid
            # Fallback: find by process name
            return self._get_pid_by_name(ctx, process_name)
        except Exception as exc:
            ctx.logger.warn(f"重启命令执行失败: {exc}")
            return None

    @staticmethod
    def _collect_logcat(ctx: StepContext, log_path: Path, lines: int) -> None:
        try:
            result = ctx.adb.shell(ctx.serial, ["logcat", "-d", "-t", str(lines)])
            stdout = getattr(result, "stdout", "") or ""
            if stdout:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(stdout)
        except Exception:
            pass
