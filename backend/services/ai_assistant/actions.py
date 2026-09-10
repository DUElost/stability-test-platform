# -*- coding: utf-8 -*-
"""AI 助手 action 状态机的原子转换原语（R13-F03 / #1215）。

审批（proposed → approved/rejected）与执行（approved → running）都必须以
数据库条件更新（compare-and-set）完成：任何「读 status 再赋值」的实现都会
在两个 worker / 两个管理员并发时双通过，造成重复执行副作用。

本模块只做一件事：在给定 ``expected`` 状态下把 action 抢占到 ``new_status``，
返回是否抢占成功（rowcount == 1）。调用方负责 commit 与后续业务。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from backend.models.ai_assistant import AiAssistantAction


def try_transition_action(
    db: Session,
    action_id: int,
    *,
    expected: str,
    new_status: str,
    **values: Any,
) -> bool:
    """Atomically transition ``action_id`` from ``expected`` to ``new_status``.

    Returns ``True`` iff this caller won the transition. The UPDATE is scoped by
    the current status so concurrent callers cannot both succeed.
    """
    result = db.execute(
        update(AiAssistantAction)
        .where(
            AiAssistantAction.id == action_id,
            AiAssistantAction.status == expected,
        )
        .values(status=new_status, **values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
