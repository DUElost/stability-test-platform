# -*- coding: utf-8 -*-
"""AI 助手工具授权（ADR-0031 D8）。

有效权限 = 账号在 REST API 上具备的同操作权限；助手不得成为越权后门。
当前以 ``ToolSpec.admin_only`` 镜像 ``require_admin`` 端点；细粒度 RBAC
落地时在此扩展，执行面与 LLM 载荷裁剪共用同一函数。
"""

from __future__ import annotations

from typing import Any

from backend.services.ai_assistant.tools import ToolSpec


class ToolAuthorizationError(PermissionError):
    """发起人无权调用该工具（与 API 403 语义对齐）。"""


def user_may_invoke_tool(user: Any, spec: ToolSpec | None) -> bool:
    if spec is None:
        return False
    if spec.admin_only and getattr(user, "role", None) != "admin":
        return False
    return True


def assert_user_may_invoke_tool(user: Any, spec: ToolSpec) -> None:
    if not user_may_invoke_tool(user, spec):
        raise ToolAuthorizationError(
            f"用户无权执行工具 {spec.name}"
            + ("（需要 admin）" if spec.admin_only else "")
        )
