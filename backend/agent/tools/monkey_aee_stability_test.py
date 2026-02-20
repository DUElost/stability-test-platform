# -*- coding: utf-8 -*-
"""
MONKEY_AEE 平台化工具类。
"""

from __future__ import annotations

import hashlib
import tarfile
from pathlib import Path
from typing import Any, Dict, Optional

from ..test_framework import BaseTestCase, TestResult
from ..test_stages import MINIMAL_STAGES, TestStage, stage_progress
from .adapters.legacy_monkey_aee_adapter import (
    LegacyMonkeyAEEAdapter,
    LegacyMonkeyAEEConfig,
    LegacyRunResult,
)
from .config.monkey_aee_defaults import build_monkey_aee_defaults


class MonkeyAEEStabilityTest(BaseTestCase):
    """兼容旧脚本的 MONKEY_AEE 稳定性测试。"""

    TEST_TYPE = "MONKEY_AEE"
    STAGES = MINIMAL_STAGES

    def __init__(self, adb_wrapper=None, **context):
        super().__init__(adb_wrapper=adb_wrapper, **context)
        self._last_run_log_path: str = ""
        self._last_run_tail: str = ""

    def get_default_params(self) -> Dict[str, Any]:
        return build_monkey_aee_defaults()

    def setup(self, serial: str, params: Dict[str, Any]) -> None:
        """仅做轻量预检，避免默认 setup 带来额外负担。"""
        self.set_progress(stage_progress(TestStage.PRECHECK), "MONKEY_AEE 预检中")
        if not self.log_dir:
            self.log_dir = f"logs/runs/{self.run_id or 'local'}"
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        try:
            self.adb.shell(serial, ["id"])
        except Exception as exc:
            raise RuntimeError(f"ADB 设备不可用: {exc}") from exc

    def execute(self, serial: str, params: Dict[str, Any]) -> TestResult:
        merged = self._merge_params(params)
        script_path = str(merged.get("legacy_script_path", "")).strip()
        if not script_path:
            return TestResult(
                status="FAILED",
                exit_code=1,
                error_code="SCRIPT_PATH_EMPTY",
                error_message="legacy_script_path is required",
            )
        if not Path(script_path).exists():
            return TestResult(
                status="FAILED",
                exit_code=1,
                error_code="SCRIPT_NOT_FOUND",
                error_message=f"legacy script not found: {script_path}",
            )

        self.set_progress(stage_progress(TestStage.PREPARE), "构建外部脚本执行上下文")
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
        self.set_progress(stage_progress(TestStage.RUN), "启动旧版 MonkeyAEE 脚本")
        run_result = adapter.run(
            serial=serial,
            log_dir=self.log_dir,
            on_log=lambda line: self._log(line, "INFO"),
            on_tick=self._on_tick,
        )
        self._last_run_log_path = run_result.log_path
        self._last_run_tail = "\n".join(run_result.tail_lines[-30:])

        if run_result.timed_out:
            return TestResult(
                status="FAILED",
                exit_code=124,
                error_code="LEGACY_TIMEOUT",
                error_message=f"legacy script timed out after {config.run_timeout_sec}s",
                log_summary=self._build_summary(run_result, aee_count=0),
            )

        aee_count = 0
        if bool(merged.get("collect_aee_logs", True)):
            self.set_progress(stage_progress(TestStage.RISK_SCAN), "执行 AEE 日志收集")
            aee_entries = self._collect_aee_logs(serial, self.log_dir)
            aee_count = len(aee_entries)
            self._log(f"收集到 AEE 目录: {aee_count}", "INFO")

        artifact = None
        if bool(merged.get("pack_artifact", True)):
            self.set_progress(stage_progress(TestStage.EXPORT), "打包运行日志产物")
            artifact = self._build_log_artifact(
                self.log_dir,
                artifact_name=str(merged.get("artifact_name", "monkey_aee")),
            )

        if run_result.return_code != 0:
            return TestResult(
                status="FAILED",
                exit_code=run_result.return_code,
                error_code="LEGACY_SCRIPT_FAILED",
                error_message=f"legacy script exit code: {run_result.return_code}",
                log_summary=self._build_summary(run_result, aee_count=aee_count),
                artifact=artifact,
            )

        return TestResult(
            status="FINISHED",
            exit_code=0,
            log_summary=self._build_summary(run_result, aee_count=aee_count),
            artifact=artifact,
        )

    def teardown(self, serial: str, params: Dict[str, Any]) -> None:
        self.set_progress(stage_progress(TestStage.TEARDOWN), "MONKEY_AEE 执行结束")

    def _on_tick(self, elapsed_sec: int) -> None:
        self.set_progress(stage_progress(TestStage.MONITOR), f"外部脚本运行中 {elapsed_sec}s")

    @staticmethod
    def _resolve_working_dir(script_path: str, working_dir: Optional[str]) -> str:
        if working_dir:
            return str(Path(working_dir))
        return str(Path(script_path).resolve().parent)

    def _merge_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        merged = self.get_default_params()
        merged.update(params or {})
        return merged

    def _build_log_artifact(self, log_dir: str, artifact_name: str) -> Optional[Dict[str, Any]]:
        base_dir = Path(log_dir)
        if not base_dir.exists() or not base_dir.is_dir():
            return None

        safe_artifact_name = artifact_name.strip() or "monkey_aee"
        run_id = str(self.run_id) if self.run_id else "local"
        archive_path = base_dir.parent / f"{safe_artifact_name}_{run_id}.tar.gz"
        try:
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(base_dir, arcname=base_dir.name)
            checksum = self._sha256(archive_path)
            return {
                "storage_uri": f"file://{archive_path.resolve()}",
                "size_bytes": archive_path.stat().st_size,
                "checksum": checksum,
            }
        except Exception as exc:
            self._log(f"日志产物打包失败: {exc}", "WARN")
            return None

    @staticmethod
    def _sha256(file_path: Path) -> str:
        hasher = hashlib.sha256()
        with file_path.open("rb") as fp:
            while True:
                chunk = fp.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    def _build_summary(self, run_result: LegacyRunResult, aee_count: int) -> str:
        summary_parts = [
            f"duration={run_result.duration_sec:.1f}s",
            f"exit_code={run_result.return_code}",
            f"timed_out={run_result.timed_out}",
            f"aee_count={aee_count}",
            f"log_path={run_result.log_path}",
        ]
        if self._last_run_tail:
            summary_parts.append("tail=" + self._last_run_tail[-800:])
        return "; ".join(summary_parts)[-2000:]
