# -*- coding: utf-8 -*-
"""审计词表与归并语义（纯函数部分）。

#3297：审计记录**写入**（record_audit / record_audit_async）已按 C1 的 S4 出口
下沉到 `backend/services/audit_writer.py`——写入要 import models，core 是最底层
不得上引。本模块只剩零依赖的登记词表与别名归并，供写侧守卫与读侧过滤共用。
"""

#: #2778：审计 `resource_type` 的**唯一权威 = 实体对应的表名**。无独立表的实体
#: （会话、资源池类型、死信队列、任务/定时等派生面）在此显式登记，登记即视为规范值。
#: 写侧只允许使用本集合内的值；新增资源类型必须先登记（机械守卫见
#: `backend/tests/test_audit_resource_type_guard.py`）。
AUDIT_RESOURCE_TYPES: frozenset[str] = frozenset({
    "agent_dead_letter",
    "ai_assistant_action",
    "ai_assistant_config",
    "ai_chat_session",
    "audit_log",
    "device",
    "host",
    "jira_run",
    "job_instance",
    "notification_channel",
    "notification_rule",
    "plan",
    "plan_run",
    "resource_pool",
    "schedule",
    "script",
    "session",
    "task",
    "test_case",
    "test_project",
    "test_suite",
    "user",
    "wifi",
})

#: #2778：历史别名 → 规范值。审计行 append-only、不可追溯改写（ADR-0015），
#: 历史字面量只在**读取侧**归并（列表过滤展开 + facets 合并计数）；写侧禁止再产出别名。
AUDIT_RESOURCE_TYPE_ALIASES: dict[str, str] = {
    # job_terminalized 曾按收尾路径分裂：Agent 完成侧写 job、回收器侧写 job_instance（#2778）
    "job": "job_instance",
    # 脚本目录扫描曾写 script_catalog，规范实体是 script 表（#2778）
    "script_catalog": "script",
}


def canonical_resource_type(value: str) -> str:
    """把历史别名归一到规范 `resource_type`；未登记值原样返回（不吞错）。"""
    return AUDIT_RESOURCE_TYPE_ALIASES.get(value, value)


def expand_resource_type_filter(value: str) -> list[str]:
    """筛选值 → 需匹配的全部字面量（规范值 + 其历史别名），保证历史行仍筛得到。

    例：``job_instance`` → ``["job", "job_instance"]``；未登记值 → ``[value]``。
    """
    canonical = canonical_resource_type(value)
    aliases = [
        alias for alias, target in AUDIT_RESOURCE_TYPE_ALIASES.items()
        if target == canonical
    ]
    return sorted({canonical, *aliases})
