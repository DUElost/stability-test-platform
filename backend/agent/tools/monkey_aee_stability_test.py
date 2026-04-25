# -*- coding: utf-8 -*-
"""MONKEY_AEE 稳定性测试 — Pipeline Action.

通过 LegacyMonkeyAEEAdapter 执行旧版 MonkeyAEE 脚本，
并将日志、AEE 收集、产物打包整合到 Pipeline Action 接口。
"""

from __future__ import annotations

import hashlib
import os
import tarfile
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..pipeline_engine import PipelineAction, StepContext, StepResult
from .adapters.legacy_monkey_aee_adapter import (
    LegacyMonkeyAEEAdapter,
    LegacyMonkeyAEEConfig,
)
from .config.monkey_aee_defaults import build_monkey_aee_defaults


class MonkeyAEEAction(PipelineAction):
    """执行旧版 MonkeyAEE 脚本，并在 Pipeline 体系内完成日志收集和产物打包。"""

    TOOL_CATEGORY = "MONKEY_AEE"
    TOOL_DESCRIPTION = "兼容旧版 MonkeyAEE 脚本的稳定性测试 Pipeline Action。"

    @classmethod
    def get_default_params(cls) -> Dict[str, Any]:
        return build_monkey_aee_defaults()

    def run(self, ctx: StepContext) -> StepResult:
        merged = {**self.get_default_params(), **(ctx.params or {})}

        log_dir = merged.get("log_dir") or (
            f"logs/runs/{ctx.run_id}" if ctx.run_id else "logs/runs/local"
        )
        Path(log_dir).mkdir(parents=True, exist_ok=True)

        # 设备预检
        try:
            ctx.adb.shell(ctx.serial, ["id"])
        except Exception as exc:
            return StepResult(success=False, exit_code=1, error_message=f"ADB 设备不可用: {exc}")

        script_path = str(merged.get("legacy_script_path", "")).strip()
        if not script_path:
            return StepResult(success=False, exit_code=1, error_message="legacy_script_path is required")
        if not Path(script_path).exists():
            return StepResult(success=False, exit_code=1, error_message=f"legacy script not found: {script_path}")

        ctx.logger.info(f"构建 LegacyMonkeyAEEAdapter: script={script_path}")
        config = LegacyMonkeyAEEConfig(
            python_executable=str(merged["python_executable"]),
            script_path=script_path,
            working_dir=self._resolve_working_dir(script_path, merged.get("legacy_working_dir")),
            script_args=list(merged.get("script_args") or []),
            legacy_params=dict(merged.get("legacy_params") or {}),
            pass_serial_arg=bool(merged.get("pass_serial_arg", True)),
            serial_arg_name=str(merged.get("serial_arg_name", "--serial")),
            run_timeout_sec=int(merged.get("run_timeout_sec", 21600)),
            poll_interval_sec=float(merged.get("poll_interval_sec", 1.0)),
            progress_interval_sec=int(merged.get("progress_interval_sec", 15)),
            max_log_lines=int(merged.get("max_log_lines", 2000)),
        )

        adapter = LegacyMonkeyAEEAdapter(config)
        ctx.logger.info("启动旧版 MonkeyAEE 脚本")

        run_result = adapter.run(
            serial=ctx.serial,
            log_dir=log_dir,
            on_log=lambda line: ctx.logger.info(line),
            on_tick=lambda elapsed: ctx.logger.info(f"外部脚本运行中 {elapsed}s"),
        )

        if run_result.timed_out:
            return StepResult(
                success=False,
                exit_code=124,
                error_message=f"legacy script timed out after {config.run_timeout_sec}s",
                metrics=self._build_metrics(run_result, aee_count=0),
            )

        aee_count = 0
        if bool(merged.get("collect_aee_logs", True)):
            ctx.logger.info("执行 AEE 日志收集")
            try:
                aee_dirs = []
                for path in ["/data/aee_exp", "/data/vendor/aee_exp"]:
                    result = ctx.adb.shell(ctx.serial, ["ls", path])
                    stdout = getattr(result, "stdout", "") or ""
                    entries = [e for e in stdout.splitlines() if e.strip()]
                    aee_count += len(entries)
                    aee_dirs.extend(entries)
                ctx.logger.info(f"发现 AEE 目录: {aee_count} 个")
            except Exception as exc:
                ctx.logger.warn(f"AEE 收集失败: {exc}")

        artifact = None
        if bool(merged.get("pack_artifact", True)):
            ctx.logger.info("打包运行日志产物")
            artifact = self._build_log_artifact(
                log_dir,
                artifact_name=str(merged.get("artifact_name", "monkey_aee")),
                run_id=ctx.run_id,
            )

        metrics = self._build_metrics(run_result, aee_count=aee_count)
        if artifact:
            metrics["artifact"] = artifact

        if run_result.return_code != 0:
            return StepResult(
                success=False,
                exit_code=run_result.return_code,
                error_message=f"legacy script exit code: {run_result.return_code}",
                metrics=metrics,
                artifact=artifact,
            )

        return StepResult(success=True, exit_code=0, metrics=metrics, artifact=artifact)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_working_dir(script_path: str, working_dir: Optional[str]) -> str:
        if working_dir:
            return str(Path(working_dir))
        return str(Path(script_path).resolve().parent)

    @staticmethod
    def _build_metrics(run_result, aee_count: int) -> dict:
        return {
            "duration_sec": round(getattr(run_result, "duration_sec", 0), 1),
            "exit_code": getattr(run_result, "return_code", 0),
            "timed_out": getattr(run_result, "timed_out", False),
            "aee_count": aee_count,
            "log_path": getattr(run_result, "log_path", ""),
        }

    @staticmethod
    def _build_log_artifact(log_dir: str, artifact_name: str, run_id: Any) -> Optional[dict]:
        base_dir = Path(log_dir)
        if not base_dir.exists() or not base_dir.is_dir():
            return None
        safe_name = (artifact_name.strip() or "monkey_aee")
        run_label = str(run_id) if run_id else "local"
        archive_path = base_dir.parent / f"{safe_name}_{run_label}.tar.gz"
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(base_dir, arcname=base_dir.name)
            hasher = hashlib.sha256()
            with archive_path.open("rb") as fp:
                while chunk := fp.read(1024 * 1024):
                    hasher.update(chunk)
            return {
                "storage_uri": f"file://{archive_path.resolve()}",
                "size_bytes": archive_path.stat().st_size,
                "checksum": hasher.hexdigest(),
            }
        except Exception:
            return None


@dataclass
class LegacyMonkeyAEEResult:
    status: str
    exit_code: int
    error_message: str = ""
    artifact: Optional[dict] = None
    metrics: Optional[dict] = None


class _LegacyStepLogger:
    def info(self, message: str) -> None:
        pass

    def warn(self, message: str) -> None:
        pass

    warning = warn


class MonkeyAEEStabilityTest:
    """Backward-compatible wrapper for the pre-Pipeline MonkeyAEE test API."""

    def __init__(self, adb_wrapper: Any, run_id: int, log_dir: str):
        self.adb_wrapper = adb_wrapper
        self.run_id = run_id
        self.log_dir = log_dir
        self._action = MonkeyAEEAction()

    def run(self, serial: str, params: Optional[Dict[str, Any]] = None) -> LegacyMonkeyAEEResult:
        merged_params = dict(params or {})
        merged_params.setdefault("log_dir", self.log_dir)
        ctx = StepContext(
            adb=self.adb_wrapper,
            serial=serial,
            params=merged_params,
            run_id=self.run_id,
            step_id=0,
            logger=_LegacyStepLogger(),
        )
        result = self._action.run(ctx)
        return LegacyMonkeyAEEResult(
            status="FINISHED" if result.success else "FAILED",
            exit_code=result.exit_code,
            error_message=result.error_message,
            artifact=result.artifact,
            metrics=result.metrics,
        )
