# -*- coding: utf-8 -*-
"""
TaskExecutor 已废弃：执行引擎已切换为 Pipeline-only。
此文件保留为占位，避免旧引用静默生效。
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ExecutionContext:
    api_url: str
    run_id: int
    host_id: int
    device_serial: str
    log_dir: str = ""


@dataclass
class TaskResult:
    status: str
    exit_code: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    log_summary: Optional[str] = None
    artifact: Optional[Dict[str, Any]] = None


class TaskExecutor:
    """兼容占位：禁止使用旧执行引擎。"""

    def __init__(self, *args, **kwargs) -> None:
        raise RuntimeError("TaskExecutor 已禁用，请使用 pipeline 执行")
