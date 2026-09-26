"""#3296：ai_assistant 对 plan_wifi / plan_run_queries 领域异常的捕获回归。

背景：#3296 把 ``plan_wifi`` / ``plan_run_queries`` 的 ``HTTPException`` 换成
``backend.services.errors`` 领域异常；``ai_assistant`` 里有 4 处调用方按异常
类型捕获并转成**可读文案 / RuntimeError**。领域异常不是 ``HTTPException``
的子类——漏改任何一处，异常会穿过 except 直接上抛。现有 API 测试打不进这一
侧：路由层的全局 handler 会把领域异常渲染成 4xx，恰好掩盖 ai_assistant 的
失效（owner 评注，main@56fcb03）。本文件在服务层直测钉住这 4 处 catch。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.ai_assistant.dispatch import execute_dispatch_plan_run
from backend.services.ai_assistant.plan_run_ops import (
    describe_manual_job_preview,
    run_manual_exit_job,
    run_manual_retry_job,
)
from backend.services.errors import BadRequest, NotFound, ServiceError


def test_manual_job_preview_still_reports_not_found():
    """catch #1（plan_run_ops describe_manual_job_preview）：未找到 → 原预览文案。"""
    with patch(
        "backend.services.plan_run_queries.load_job_in_run",
        side_effect=NotFound("job not found in this plan run"),
    ):
        text = describe_manual_job_preview(
            MagicMock(), {"run_id": 7, "job_id": 3}, action="manual retry",
        )
    assert "未找到或不属于该 run" in text
    assert "PlanRun #7" in text and "job #3" in text


@pytest.mark.parametrize("runner", [run_manual_retry_job, run_manual_exit_job])
def test_manual_runners_convert_domain_error_to_runtime(runner):
    """catch #2/#3（run_manual_retry_job / run_manual_exit_job）：
    领域异常 → RuntimeError（AI 助手可读），不得裸抛 ServiceError。"""
    with patch(
        "backend.services.plan_run_queries.load_job_in_run",
        side_effect=NotFound("job not found in this plan run"),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            runner(
                MagicMock(),
                {"run_id": 7, "job_id": 3, "reason": "r"},
                triggered_by="ai-assistant",
            )
    assert "job not found in this plan run" in str(exc_info.value)
    assert not isinstance(exc_info.value, ServiceError)


def test_dispatch_wifi_branch_extracts_message_from_dict_detail():
    """catch #4（dispatch execute_dispatch_plan_run）：dict detail 的领域异常
    → RuntimeError 且文本取 ``message`` 键（原 HTTPException 分支同语义）。"""
    message = "wifi_pool_id 3 is not an active wifi resource pool"
    with patch(
        "backend.services.plan_wifi.require_active_wifi_pool",
        side_effect=BadRequest({"code": "WIFI_POOL_INACTIVE", "message": message}),
    ):
        with pytest.raises(RuntimeError) as exc_info:
            execute_dispatch_plan_run(
                MagicMock(),
                {"plan_id": 1, "device_ids": [10], "wifi_pool_id": 3},
                triggered_by="ai-assistant",
            )
    assert message == str(exc_info.value)
    assert not isinstance(exc_info.value, ServiceError)
