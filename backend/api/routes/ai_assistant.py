# -*- coding: utf-8 -*-
"""平台 AI 助手路由（ADR-0031 D6 权限矩阵）。

- config 三端点 + 动作审批/取消：admin
- 会话/消息：登录用户（按用户隔离，仅本人可见）
- 动作详情/日志：提案人或 admin
未配置时统一 409 `ai_not_configured`。
"""

import asyncio
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.api.response import ApiResponse, ok
from backend.api.routes.auth import User, get_current_active_user, require_admin
from backend.api.schemas.ai_assistant import (
    AiActionOut,
    AiAssistantConfigOut,
    AiAssistantConfigUpdate,
    AiConnectionTestOut,
    AiMessageOut,
    AiSessionOut,
    T2bAutoDispatchAllowlistEntry,
)
from backend.core.ai_security import decrypt_api_key, encrypt_api_key, mask_api_key
from backend.core.audit import record_audit
from backend.core.database import get_db
from backend.models.ai_assistant import (
    AiAssistantAction,
    AiChatMessage,
    AiChatSession,
)
from backend.services.ai_assistant.llm_client import (
    AiAuthError,
    AiBadResponse,
    AiNotConfigured,
    AiUpstreamTimeout,
    LlmClient,
)
from backend.services.ai_assistant.orchestrator import (
    ensure_pending_placeholder,
    execute_action,
    get_or_create_config,
    load_effective_config,
)
from backend.services.ai_assistant.tools import TOOLS, describe_tool_action_preview

router = APIRouter(prefix="/api/v1/ai-assistant", tags=["ai-assistant"])
logger = logging.getLogger(__name__)


# M5：在飞后台任务强引用集（create_task 结果无人持有时可能被 GC 回收）
_BG_TASKS: set = set()


def _bg_task_done(task) -> None:
    _BG_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("ai_action_execute_failed %s", exc, exc_info=exc)


def _not_configured(detail: str = "AI 助手未配置或未启用") -> HTTPException:
    return HTTPException(status_code=409, detail={"code": "ai_not_configured", "message": detail})


# ─────────────────────────── 配置（admin） ───────────────────────────

def _config_out(db: Session) -> AiAssistantConfigOut:
    cfg = get_or_create_config(db)
    try:
        masked = mask_api_key(decrypt_api_key(cfg.api_key_encrypted or "")) or None
    except Exception:  # noqa: BLE001 - 密文不可解时只报未配置态，不抛栈
        masked = None
    from backend.services.ai_assistant.t2b_allowlist import sanitize_t2b_auto_dispatch_allowlist

    cleaned_allowlist, _ = sanitize_t2b_auto_dispatch_allowlist(
        cfg.t2b_auto_dispatch_allowlist,
        db,
    )
    return AiAssistantConfigOut(
        base_url=cfg.base_url,
        model=cfg.model,
        api_key_masked=masked,
        enabled=cfg.enabled,
        temperature=cfg.temperature,
        max_turns=cfg.max_turns,
        request_timeout_seconds=cfg.request_timeout_seconds,
        t1_require_confirm=cfg.t1_require_confirm,
        auto_approve_tools=list(cfg.auto_approve_tools or []),
        t2b_auto_dispatch_allowlist=[
            T2bAutoDispatchAllowlistEntry.model_validate(e) for e in cleaned_allowlist
        ],
        updated_at=cfg.updated_at,
    )


@router.get("/config", response_model=ApiResponse[AiAssistantConfigOut])
def get_ai_config(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    return ok(_config_out(db))


@router.put("/config", response_model=ApiResponse[AiAssistantConfigOut])
def update_ai_config(
    payload: AiAssistantConfigUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    cfg = get_or_create_config(db)
    changed: list[str] = []
    for field_name in (
        "base_url", "model", "enabled", "temperature", "max_turns",
        "request_timeout_seconds", "t1_require_confirm", "auto_approve_tools",
        "t2b_auto_dispatch_allowlist",
    ):
        value = getattr(payload, field_name)
        if value is not None:
            if field_name == "t2b_auto_dispatch_allowlist":
                setattr(
                    cfg,
                    field_name,
                    [e.model_dump() for e in value],
                )
            else:
                setattr(cfg, field_name, value)
            changed.append(field_name)
    if payload.api_key:  # 留空 = 不变更
        cfg.api_key_encrypted = encrypt_api_key(payload.api_key)
        changed.append("api_key")
    db.commit()

    # 白名单只接受 T2 且 whitelistable 的工具（D1 治理闭环）
    invalid = [
        t for t in (cfg.auto_approve_tools or [])
        if not (TOOLS.get(t) and TOOLS[t].tier == "T2" and TOOLS[t].whitelistable)
    ]
    if invalid:
        cfg.auto_approve_tools = [t for t in cfg.auto_approve_tools if t not in invalid]
        db.commit()

    from backend.services.ai_assistant.t2b_allowlist import sanitize_t2b_auto_dispatch_allowlist

    cleaned, dropped_allowlist = sanitize_t2b_auto_dispatch_allowlist(
        cfg.t2b_auto_dispatch_allowlist,
        db,
    )
    if list(cfg.t2b_auto_dispatch_allowlist or []) != cleaned:
        cfg.t2b_auto_dispatch_allowlist = cleaned
        db.commit()

    record_audit(
        db,
        action="ai_assistant_config_update",
        resource_type="ai_assistant_config",
        resource_id=1,
        details={
            "changed_fields": changed,
            "invalid_whitelist_dropped": invalid,
            "t2b_allowlist_dropped": dropped_allowlist,
        },
        user_id=user.id,
        username=user.username,
        request=request,
    )
    db.commit()
    return ok(_config_out(db))


@router.post("/config/test-connection", response_model=ApiResponse[AiConnectionTestOut])
async def test_ai_connection(
    db: Session = Depends(get_db),
    _user: User = Depends(require_admin),
):
    try:
        cfg, api_key = load_effective_config(db)
    except AiNotConfigured as exc:
        return ok(AiConnectionTestOut(ok=False, latency_ms=None, model="", error=f"ai_not_configured: {exc}"))

    started = time.monotonic()
    try:
        client = LlmClient(
            base_url=cfg.base_url,
            api_key=api_key,
            model=cfg.model,
            timeout_seconds=min(float(cfg.request_timeout_seconds), 30.0),
        )
        await client.chat(
            [{"role": "user", "content": "ping"}], temperature=0.0
        )
        return ok(
            AiConnectionTestOut(
                ok=True,
                latency_ms=int((time.monotonic() - started) * 1000),
                model=cfg.model,
            )
        )
    except (AiAuthError, AiUpstreamTimeout, AiBadResponse, AiNotConfigured) as exc:
        return ok(
            AiConnectionTestOut(
                ok=False,
                latency_ms=None,
                model=cfg.model,
                error=f"{type(exc).__name__}: {exc}",
            )
        )


# ─────────────────────────── 会话与消息（登录用户） ───────────────────────────

def _own_session(db: Session, user: User, session_id: int) -> AiChatSession:
    # M4：严格按 D6 用户隔离（含 admin——对话内容属隐私面；列表端点同口径，
    # 不留「列表看不到、按 id 直取」的半开状态）
    session = db.get(AiChatSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    return session


@router.post("/sessions", response_model=ApiResponse[AiSessionOut])
def create_session(
    payload: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    title = ""
    if isinstance(payload, dict):
        raw = str(payload.get("title") or "").strip()
        title = raw[:200]
    session = AiChatSession(user_id=user.id, title=title)
    db.add(session)
    db.commit()
    db.refresh(session)
    return ok(AiSessionOut.model_validate(session))


@router.get("/sessions", response_model=ApiResponse[list[AiSessionOut]])
def list_sessions(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    rows = (
        db.query(AiChatSession)
        .filter(AiChatSession.user_id == user.id)
        .order_by(AiChatSession.updated_at.desc(), AiChatSession.id.desc())
        .all()
    )
    return ok([AiSessionOut.model_validate(r) for r in rows])


@router.delete("/sessions/{session_id}", response_model=ApiResponse[dict])
def delete_session(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    session = _own_session(db, user, session_id)
    db.query(AiChatMessage).filter(AiChatMessage.session_id == session.id).delete()
    db.query(AiAssistantAction).filter(AiAssistantAction.session_id == session.id).delete()
    db.delete(session)
    record_audit(
        db,
        action="ai_assistant_session_delete",
        resource_type="ai_chat_session",
        resource_id=session.id,
        user_id=user.id,
        username=user.username,
        request=request,
    )
    db.commit()
    return ok({"deleted": session_id})


@router.get("/sessions/{session_id}/messages", response_model=ApiResponse[list[AiMessageOut]])
def list_messages(
    session_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _own_session(db, user, session_id)
    rows = (
        db.query(AiChatMessage)
        .filter(AiChatMessage.session_id == session_id)
        .order_by(AiChatMessage.id.asc())
        .all()
    )
    return ok([AiMessageOut.model_validate(r) for r in rows])


@router.post("/sessions/{session_id}/messages", response_model=ApiResponse[AiMessageOut])
def send_message(
    session_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    from datetime import datetime, timezone

    session = _own_session(db, user, session_id)
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is required")
    # 轮次互斥：同会话已有 pending/running 轮时拒绝再发——否则第二占位会因
    # SAQ 同 key 去重而孤儿化（线上实测：占位永挂 pending）。#547 的续轮
    # 占位机制让该互斥更必要（审批也会建占位）。
    in_flight = (
        db.query(AiChatMessage)
        .filter(
            AiChatMessage.session_id == session.id,
            AiChatMessage.role == "assistant",
            AiChatMessage.status.in_(["pending", "running"]),
        )
        .count()
    )
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail={"code": "ai_turn_in_progress", "message": "上一轮仍在进行中，请等回复完成后再发送"},
        )
    try:
        load_effective_config(db)
    except AiNotConfigured as exc:
        raise _not_configured(str(exc)) from exc

    db.add(AiChatMessage(session_id=session.id, role="user", content=content))
    placeholder = AiChatMessage(
        session_id=session.id, role="assistant", content="", status="pending"
    )
    db.add(placeholder)
    session.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(placeholder)

    from backend.tasks.saq_worker import enqueue_sync

    # M2：max_turns 次串行 LLM 调用的最坏超时；M3：轮次有副作用，retries=0
    cfg = get_or_create_config(db)
    timeout = cfg.request_timeout_seconds * max(int(cfg.max_turns), 1) + 120
    enqueued = enqueue_sync(
        "ai_assistant_turn_task",
        key=f"ai-turn:{session.id}",
        timeout=timeout,
        retries=0,
        session_id=session.id,
    )
    if not enqueued:
        placeholder.status = "failed"
        placeholder.meta = {"error": "任务入队失败，请重试"}
        db.commit()
        raise HTTPException(status_code=503, detail="ai turn enqueue failed")
    return ok(AiMessageOut.model_validate(placeholder))


# ─────────────────────────── 动作（审批流） ───────────────────────────

def _action_out(db: Session, action: AiAssistantAction) -> AiActionOut:
    requester = db.get(AiChatSession, action.session_id)
    requested_by = None
    decided_by = None
    if requester is not None:
        from backend.models.user import User as UserModel

        req_user = db.get(UserModel, requester.user_id)
        requested_by = getattr(req_user, "username", None)
        if action.decided_by_user_id is not None:
            dec_user = db.get(UserModel, action.decided_by_user_id)
            decided_by = getattr(dec_user, "username", None)
    return AiActionOut(
        id=action.id,
        session_id=action.session_id,
        tool_name=action.tool_name,
        params=dict(action.params or {}),
        status=action.status,
        console_run_id=action.console_run_id,
        result_summary=action.result_summary,
        preview_text=describe_tool_action_preview(
            db, action.tool_name, dict(action.params or {})
        ),
        requested_by=requested_by,
        decided_by=decided_by,
        created_at=action.created_at,
        decided_at=action.decided_at,
    )


def _visible_action(db: Session, user: User, action_id: int) -> AiAssistantAction:
    action = db.get(AiAssistantAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    session = db.get(AiChatSession, action.session_id)
    owner_id = getattr(session, "user_id", None)
    # 动作可见性保留 admin：审批职责要求 admin 能触达任意会话的操作卡
    # （D6 审批限 admin；会话消息流仍严格隔离——见 _own_session）
    if owner_id != user.id and user.role != "admin":
        raise HTTPException(status_code=404, detail="action not found")
    return action


@router.get("/actions/{action_id}", response_model=ApiResponse[AiActionOut])
def get_action(
    action_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    action = _visible_action(db, user, action_id)
    return ok(_action_out(db, action))


async def _decide_action(
    db: Session,
    request: Request,
    user: User,
    action_id: int,
    verb: str,
):
    action = db.get(AiAssistantAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    if action.status != "proposed":
        raise HTTPException(status_code=409, detail=f"action is {action.status}, not proposed")
    from datetime import datetime, timezone

    action.status = "approved" if verb == "approve" else "rejected"
    action.decided_by_user_id = user.id
    action.decided_at = datetime.now(timezone.utc)
    record_audit(
        db,
        action=f"ai_assistant_action_{verb}",
        resource_type="ai_assistant_action",
        resource_id=action.id,
        details={"tool_name": action.tool_name, "params": dict(action.params or {})},
        user_id=user.id,
        username=user.username,
        request=request,
    )
    db.commit()
    if verb == "approve":
        # 审批后的执行结果经续轮汇报——先落 pending 占位：前端 approve 成功后
        # invalidate messages，据占位恢复 2s 轮询，否则汇报只落库不上屏
        ensure_pending_placeholder(action.session_id, db)
        # M5：持引用防 GC（asyncio 文档要求），异常必须留日志——
        # 否则 action 永远停在 approved 且无任何痕迹
        task = asyncio.create_task(asyncio.to_thread(execute_action, action.id))
        _BG_TASKS.add(task)
        task.add_done_callback(_bg_task_done)
        await asyncio.sleep(0)
        db.refresh(action)
    else:
        # 拒绝也续轮：让助手收到拒绝事实并继续对话
        await asyncio.to_thread(_enqueue_continuation_sync, action.session_id)
    db.refresh(action)
    return ok(_action_out(db, action))


def _enqueue_continuation_sync(session_id: int) -> None:
    from backend.services.ai_assistant.orchestrator import _enqueue_continuation

    _enqueue_continuation(session_id)


@router.post("/actions/{action_id}/approve", response_model=ApiResponse[AiActionOut])
async def approve_action(
    action_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    return await _decide_action(db, request, user, action_id, "approve")


@router.post("/actions/{action_id}/reject", response_model=ApiResponse[AiActionOut])
async def reject_action(
    action_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    return await _decide_action(db, request, user, action_id, "reject")


@router.get("/actions/{action_id}/log", response_model=ApiResponse[list])
def get_action_log(
    action_id: int,
    from_seq: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    action = _visible_action(db, user, action_id)
    if not action.console_run_id:
        return ok([])
    from backend.services.run_console import RunConsole

    data = RunConsole.instance().read_log(action.console_run_id, from_seq=from_seq)
    base_seq = int(data.get("from_seq", 1) or 1)
    entries = [
        {"seq": base_seq + i, "ts": None, "stream": "stdout", "line": str(line)}
        for i, line in enumerate(data.get("lines") or [])
    ]
    return ok(entries)


@router.post("/actions/{action_id}/cancel", response_model=ApiResponse[AiActionOut])
async def cancel_action(
    action_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    action = db.get(AiAssistantAction, action_id)
    if action is None:
        raise HTTPException(status_code=404, detail="action not found")
    if action.status != "running":
        raise HTTPException(status_code=409, detail=f"action is {action.status}, not running")
    if action.console_run_id:
        from backend.services.run_console import RunConsole

        RunConsole.instance().cancel(action.console_run_id)
    record_audit(
        db,
        action="ai_assistant_action_cancel",
        resource_type="ai_assistant_action",
        resource_id=action.id,
        user_id=user.id,
        username=user.username,
        request=request,
    )
    db.commit()
    db.refresh(action)
    return ok(_action_out(db, action))
