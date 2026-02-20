# -*- coding: utf-8 -*-
"""
MONKEY_AEE 默认参数与参数表单定义。
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict


DEFAULT_MONKEY_AEE_PARAMS: Dict[str, Any] = {
    "python_executable": "python",
    "legacy_script_path": r"F:\AEE\MonkeyAEEinfo_Stability_20250901.py",
    "legacy_working_dir": r"F:\AEE",
    "script_args": [],
    "legacy_params": {},
    "pass_serial_arg": True,
    "serial_arg_name": "--serial",
    "run_timeout_sec": 21600,
    "poll_interval_sec": 1.0,
    "progress_interval_sec": 15,
    "max_log_lines": 2000,
    "collect_aee_logs": True,
    "pack_artifact": True,
    "artifact_name": "monkey_aee",
}


MONKEY_AEE_PARAM_SCHEMA: Dict[str, Dict[str, Any]] = {
    "legacy_script_path": {
        "type": "string",
        "label": "脚本路径",
        "required": True,
        "placeholder": r"F:\AEE\MonkeyAEEinfo_Stability_20250901.py",
        "description": "外部 MonkeyAEE 脚本路径（Windows 绝对路径）",
    },
    "legacy_working_dir": {
        "type": "string",
        "label": "工作目录",
        "placeholder": r"F:\AEE",
        "description": "脚本执行工作目录，留空时默认使用脚本所在目录",
    },
    "run_timeout_sec": {
        "type": "number",
        "label": "超时秒数",
        "required": True,
        "min": 60,
        "default": 21600,
        "description": "超时后强制终止脚本并标记任务失败",
    },
    "progress_interval_sec": {
        "type": "number",
        "label": "进度上报间隔",
        "required": True,
        "min": 5,
        "default": 15,
        "description": "每隔 N 秒更新一次运行中进度信息",
    },
    "max_log_lines": {
        "type": "number",
        "label": "日志缓存行数",
        "required": True,
        "min": 200,
        "default": 2000,
        "description": "内存中保留的最大日志行数，避免长跑内存增长",
    },
    "collect_aee_logs": {
        "type": "boolean",
        "label": "收集 AEE 日志",
        "default": True,
        "description": "执行后通过 ADB 拉取 AEE 目录日志",
    },
    "pack_artifact": {
        "type": "boolean",
        "label": "打包日志产物",
        "default": True,
        "description": "将 run 目录打包为 tar.gz 并回传 artifact 元信息",
    },
}


def build_monkey_aee_defaults() -> Dict[str, Any]:
    """返回可安全修改的默认参数副本。"""
    return deepcopy(DEFAULT_MONKEY_AEE_PARAMS)

