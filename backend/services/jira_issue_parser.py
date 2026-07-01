"""Jira issue key 解析器 — 从厂商脚本 stdout 日志提取已创建的 issue key。

厂商脚本（create_transsion/tinno_jira_batch_from_excel.py）建单成功时会在 stdout
输出形如 `STABILITY-123`、`STB-45` 的 issue key。本解析器扫描日志文本，按
Jira issue key 通用正则（`[A-Z][A-Z0-9_]+-\d+`，全大写、至少 2 字母项目前缀）
提取去重后的有序列表。

正则宽松 + 大小写约束 + 数字结尾，能覆盖绝大多数 Jira Cloud/Server 实例的
project key 命名。若厂商脚本输出格式变化，扩展此处即可，不影响上下游。
"""
from __future__ import annotations

import re
from typing import Iterable, List

# Jira issue key：大写字母/数字/下划线组成的项目前缀（≥2 字符）+ '-' + 数字
_ISSUE_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9_]{1,}-\d+)\b")


def parse_issue_keys(lines: Iterable[str]) -> List[str]:
    """从日志行序列提取 issue key，去重保序。

    输入是行级迭代器（如 open(log_path) 或已 split 的列表）。
    返回按首次出现顺序去重的 key 列表。
    """
    seen: set[str] = set()
    result: List[str] = []
    for line in lines:
        if not line:
            continue
        for m in _ISSUE_KEY_RE.finditer(line):
            key = m.group(1)
            if key not in seen:
                seen.add(key)
                result.append(key)
    return result


def parse_issue_keys_from_text(text: str) -> List[str]:
    """从整段日志文本提取 issue key（便利方法）。"""
    return parse_issue_keys(text.splitlines())