# -*- coding: utf-8 -*-
"""AI 助手编排器（ADR-0031 D5 + 计划 v1.3）。

轮次 = 一个 SAQ 任务（job key `ai-turn:{session_id}` 串行防并发轮）。
设计要点（可行性分析风险 #8 采纳）：T1 自动路径与 T2 白名单/审批路径
**同构**——都创建 action 并经 RunConsole/服务执行，完成后经 on_complete
入队**续轮**；轮次任务本身只含 LLM 调用 + T0 快查，天然短，不受
enqueue_sync 默认 60s job timeout 约束。
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from functools import partial

from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.ai_assistant import (
    AiAssistantAction,
    AiAssistantConfig,
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
from backend.services.ai_assistant.authz import user_may_invoke_tool
from backend.services.ai_assistant.tools import (
    TOOLS,
    ToolValidationError,
    allowed_tool_names,
    build_runconsole_plan,
    execute_query,
    to_openai_tools,
)

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20

# action 终态：内联执行（服务型工具 / 启动即失败）在轮次内就会落到这些状态
_ACTION_TERMINAL = {"succeeded", "failed", "cancelled", "rejected", "expired"}

SYSTEM_PROMPT = (
    "你是稳定性测试平台的运维助手。回答使用简体中文，简洁、基于事实。"
    "你可以调用工具查询平台状态、运行测试与质量门禁、执行低危运维动作。"
    "有副作用的操作会生成操作卡：需要管理员审批的你会收到等待提示；"
    "自动执行的命令完成后结果会回传给你，你再向用户汇报。"
    "禁止承诺执行平台未提供的能力（如重启主机、修改数据库数据）。"
    "你不知道也永远不应询问 LLM API Key 等平台凭据。"
)


def get_or_create_config(db: Session) -> AiAssistantConfig:
    cfg = db.get(AiAssistantConfig, 1)
    if cfg is None:
        cfg = AiAssistantConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def load_effective_config(db: Session) -> tuple[AiAssistantConfig, str]:
    """返回 (配置行, 解密后的 api_key)。未启用/缺三元组 → AiNotConfigured。"""
    from backend.core.ai_security import AiSecurityConfigError, decrypt_api_key

    cfg = get_or_create_config(db)
    if not cfg.enabled or not cfg.base_url or not cfg.model:
        raise AiNotConfigured("ai assistant not configured")
    try:
        api_key = decrypt_api_key(cfg.api_key_encrypted or "")
    except AiSecurityConfigError as exc:
        raise AiNotConfigured(f"api key unreadable: {exc}") from exc
    if not api_key:
        raise AiNotConfigured("api key not set")
    return cfg, api_key


def _touch_session(db: Session, session: AiChatSession) -> None:
    session.updated_at = datetime.now(timezone.utc)
    db.add(session)


def _add_message(
    db: Session,
    session: AiChatSession,
    *,
    role: str,
    content: str = "",
    tool_calls: list | None = None,
    tool_call_id: str | None = None,
    status: str = "completed",
    meta: dict | None = None,
) -> AiChatMessage:
    msg = AiChatMessage(
        session_id=session.id,
        role=role,
        content=content or "",
        tool_calls=tool_calls or [],
        tool_call_id=tool_call_id,
        status=status,
        meta=meta or {},
    )
    db.add(msg)
    _touch_session(db, session)
    db.commit()
    db.refresh(msg)
    return msg


def _fail_pending_messages(db: Session, session: AiChatSession, error: str) -> None:
    pending = (
        db.query(AiChatMessage)
        .filter(
            AiChatMessage.session_id == session.id,
            AiChatMessage.status.in_(["pending", "running"]),
        )
        .all()
    )
    for msg in pending:
        msg.status = "failed"
        meta = dict(msg.meta or {})
        meta["error"] = error[:500]
        msg.meta = meta
    _touch_session(db, session)
    db.commit()


def _history_as_llm_messages(db: Session, session_id: int) -> list[dict]:
    rows = (
        db.query(AiChatMessage)
        .filter(
            AiChatMessage.session_id == session_id,
            AiChatMessage.role.in_(["user", "assistant", "tool"]),
        )
        .order_by(AiChatMessage.id.desc())
        .limit(HISTORY_LIMIT)
        .all()
    )
    rows.reverse()
    messages: list[dict] = []
    for row in rows:
        if row.role == "user":
            messages.append({"role": "user", "content": row.content})
        elif row.role == "assistant":
            # pending/running 是 UI 占位（send_message 先落占位再入队轮次），
            # 不是对话内容——发给严格供应商会 400
            # "content or tool_calls must be set"（线上实测）。
            if row.status in ("pending", "running"):
                continue
            tool_calls_fmt = [
                {
                    "id": tc.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": tc.get("name", ""),
                        "arguments": json.dumps(
                            tc.get("arguments") or {}, ensure_ascii=False
                        ),
                    },
                }
                for tc in row.tool_calls
            ]
            # 失败占位常为空 content 且无 tool_calls，同样会被拒绝
            if not row.content and not tool_calls_fmt:
                continue
            entry: dict = {"role": "assistant", "content": row.content or None}
            if tool_calls_fmt:
                entry["tool_calls"] = tool_calls_fmt
            messages.append(entry)
        else:
            if not row.tool_call_id:
                # 动作完成回执（无对应 assistant tool_calls）——以 user 角色
                # 注入，避免「tool 消息没有前置 tool_calls」的严格校验拒绝
                messages.append(
                    {"role": "user", "content": f"[执行回执] {row.content}"}
                )
            else:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": row.tool_call_id,
                        "content": row.content,
                    }
                )
    return messages


def _decide_execution_mode(spec, cfg: AiAssistantConfig, user) -> str:
    """返回 "auto"（直接执行）或 "proposed"（操作卡审批）。"""
    # auto_approve / T1 自动均须发起人本身有权调用（D8：不得借助手越权）
    if not user_may_invoke_tool(user, spec):
        return "proposed"
    if spec.tier == "T1":
        return "proposed" if cfg.t1_require_confirm else "auto"
    # T2：白名单内低危工具可免确认（仅 whitelistable 标记的工具可入名单）
    if spec.tier == "T2" and spec.whitelistable and spec.name in (cfg.auto_approve_tools or []):
        return "auto"
    return "proposed"


def _create_action(
    db: Session,
    session: AiChatSession,
    *,
    tool_name: str,
    arguments: dict,
    mode: str,
) -> AiAssistantAction:
    action = AiAssistantAction(
        session_id=session.id,
        tool_name=tool_name,
        params=arguments or {},
        status="approved" if mode == "auto" else "proposed",
        requested_by_user_id=session.user_id,
        decided_by_user_id=session.user_id if mode == "auto" else None,
        decided_at=datetime.now(timezone.utc) if mode == "auto" else None,
    )
    db.add(action)
    db.commit()
    db.refresh(action)
    return action


def _run_query(name: str, args: dict) -> str:
    db = SessionLocal()
    try:
        return execute_query(db, name, args)
    finally:
        db.close()


# ─────────────────────────── action 执行面 ───────────────────────────

def _finalize_action(action_id: int, status: str, summary: str) -> None:
    db = SessionLocal()
    try:
        action = db.get(AiAssistantAction, action_id)
        if action is None:
            logger.error("ai_action_finalize_missing id=%s", action_id)
            return
        action.status = status
        action.result_summary = summary[:1000]
        session = db.get(AiChatSession, action.session_id)
        if session is not None:
            db.add(
                AiChatMessage(
                    session_id=session.id,
                    role="tool",
                    tool_call_id=None,
                    content=f"{action.tool_name} → {status}：{summary[:500]}",
                )
            )
            _touch_session(db, session)
        db.commit()
        if session is not None:
            _enqueue_continuation(session.id)
    finally:
        db.close()


def ensure_pending_placeholder(session_id: int, db: Session | None = None) -> None:
    """确保会话有一条 pending 占位——它是前端「助手仍欠一条回复」的唯一信号。

    幂等：已有 pending/running 占位时不重复插入。占位不进 LLM 历史
    （`_history_as_llm_messages` 跳过 pending），只驱动前端 2s 轮询：
    没有它，动作完成后的续轮汇报只落库、不上屏（全局 refetchOnWindowFocus=false）。
    """
    own = db is None
    session_db = db or SessionLocal()
    try:
        exists = (
            session_db.query(AiChatMessage.id)
            .filter(
                AiChatMessage.session_id == session_id,
                AiChatMessage.role == "assistant",
                AiChatMessage.status.in_(["pending", "running"]),
            )
            .first()
        )
        if exists:
            return
        session_db.add(
            AiChatMessage(
                session_id=session_id, role="assistant", content="", status="pending"
            )
        )
        session_db.commit()
    finally:
        if own:
            session_db.close()


def _enqueue_continuation(session_id: int) -> None:
    """执行完成后续轮（lazy import 防循环依赖：saq_worker → saq_tasks → 本模块）。"""
    try:
        from backend.tasks.saq_worker import enqueue_sync

        # M2：轮次最多 max_turns 次串行 LLM 调用，超时按最坏情况估
        #（默认 60s 的 SAQ timeout 会把多轮 T0 链中途砍掉，占位滞留 pending）
        # M3：轮次任务有副作用（写消息/建 action），retries=0 禁止整轮重放
        timeout = 240
        try:
            db = SessionLocal()
            try:
                cfg = get_or_create_config(db)
                timeout = cfg.request_timeout_seconds * max(int(cfg.max_turns), 1) + 120
            finally:
                db.close()
        except Exception:  # noqa: BLE001 - 超时取默认值即可
            pass
        # 续轮期间前端据占位继续轮询；入队失败时下面立刻把它标 failed，不留悬挂
        ensure_pending_placeholder(session_id)
        enqueued = enqueue_sync(
            "ai_assistant_turn_task",
            key=f"ai-turn:{session_id}",
            timeout=timeout,
            retries=0,
            session_id=session_id,
        )
        if not enqueued:
            logger.warning("ai_continuation_enqueue_failed session=%s", session_id)
            _converge_pending(
                session_id, False, error="续轮任务入队失败（SAQ 不可用），请重新提问。"
            )
    except Exception:  # noqa: BLE001 - 续轮失败不影响已完成动作的留痕
        logger.exception("ai_continuation_enqueue_error session=%s", session_id)


def _run_service_tool(name: str, params: dict) -> str:
    """T2 服务型工具（非 RunConsole）：在 worker 线程执行，返回结果摘要。"""
    import os

    if name == "scan_script_catalog":
        from backend.services.script_catalog import scan_script_root

        root = os.getenv("STP_SCRIPT_ROOT", "").strip()
        if not root:
            raise RuntimeError("STP_SCRIPT_ROOT 未配置（scripts scan 503 同源约束）")
        db = SessionLocal()
        try:
            result = scan_script_root(db, root)
        finally:
            db.close()
        return (
            f"扫描完成：新建 {result.created} / 跳过 {result.skipped} / "
            f"停用 {result.deactivated} / 冲突 {len(result.conflicts)}"
        )

    if name == "test_notification_channel":
        from backend.models.notification import NotificationChannel
        from backend.services.notification_service import send_to_channel

        db = SessionLocal()
        try:
            channel = db.get(NotificationChannel, int(params.get("channel_id", 0)))
            if channel is None:
                raise RuntimeError(f"通知渠道 #{params.get('channel_id')} 不存在")
            channel_name = channel.name
        finally:
            db.close()
        send_to_channel(channel, "【AI 助手】通知通道测试消息")
        return f"已向渠道「{channel_name}」发送测试消息"

    if name == "reload_agent_config":
        from backend.models.host import Host
        from backend.realtime.socketio_server import call_agent_control_sync

        host_id = str(params.get("host_id", ""))
        db = SessionLocal()
        try:
            host = db.get(Host, host_id)
            if host is None:
                raise RuntimeError(f"主机 {host_id} 不存在")
            if host.status != "ONLINE":
                raise RuntimeError(f"主机 {host_id} 状态为 {host.status}，须 ONLINE")
        finally:
            db.close()
        # H3：sio 归主事件循环所有——asyncio.run 新建循环跨循环 emit 不受支持
        # （可能静默丢弃）。走 run_coroutine_threadsafe 桥接并等待 Agent ack。
        acked = call_agent_control_sync(host_id, "reload_config", timeout=5.0)
        if not acked:
            raise RuntimeError(
                f"reload_config 下发未获主机 {host_id} 确认（离线/超时/未注册）"
            )
        return f"已向主机 {host_id} 下发 reload_config 并获 Agent ack"

    raise ToolValidationError(f"unknown service tool: {name}")


def execute_action(action_id: int) -> None:
    """执行一个已批准的 action（RunConsole 启动或服务调用）。

    由审批端点（to_thread）或轮次内 auto 路径调用；RunConsole 完成/服务返回
    后经 _finalize_action 回写状态并续轮。
    """
    from backend.services.run_console import RunConsole, RunConsoleError, RunKeyBusyError

    db = SessionLocal()
    try:
        action = db.get(AiAssistantAction, action_id)
        # Low-1：放行 proposed 等于「未经审批也能执行」——闸门只认 approved
        if action is None or action.status != "approved":
            logger.warning("ai_action_execute_skipped id=%s status=%s",
                           action_id, getattr(action, "status", None))
            return
        spec = TOOLS.get(action.tool_name)
        if spec is None:
            _finalize_action(action_id, "failed", f"unknown tool {action.tool_name}")
            return

        from backend.models.user import User as UserModel

        requester = (
            db.get(UserModel, action.requested_by_user_id)
            if action.requested_by_user_id is not None
            else None
        )
        if requester is None or not user_may_invoke_tool(requester, spec):
            _finalize_action(
                action_id,
                "failed",
                "权限不足：发起人无权执行该操作（与账号 API 权限一致）",
            )
            return

        if spec.kind == "runconsole":
            plan = build_runconsole_plan(action.tool_name, action.params or {})
            action.status = "running"
            db.commit()
            try:
                run_id = RunConsole.instance().start(
                    run_key=plan.run_key,
                    cmd=plan.cmd,
                    cwd=str(plan.cwd),
                    env=dict(plan.env) if plan.env else None,
                    label=f"ai:{action.tool_name}",
                    on_complete=partial(_on_action_complete, action.id),
                )
            except RunKeyBusyError:
                _finalize_action(action_id, "failed", "同 run_key 任务正在运行，请稍后重试")
                return
            except RunConsoleError as exc:
                _finalize_action(action_id, "failed", f"启动失败：{exc}")
                return
            action.console_run_id = run_id
            db.commit()
            _arm_run_timeout(run_id, plan.timeout_seconds)
        else:
            action.status = "running"
            db.commit()
            try:
                summary = _run_service_tool(action.tool_name, action.params or {})
            except Exception as exc:  # noqa: BLE001 - 服务工具失败即终态
                _finalize_action(action_id, "failed", str(exc)[:500])
                return
            _finalize_action(action_id, "succeeded", summary)
    finally:
        db.close()


def _arm_run_timeout(run_id: str, timeout_seconds: int) -> None:
    """给 RunConsole run 装一个看门狗定时器（RunConsole 本身无超时机制）。

    到时仍在 RUNNING 才取消——已完成/已取消的 run 不受影响；取消会走
    正常 on_complete 终态回填（CANCELED），无需重复 finalize。
    """
    import threading

    from backend.services.run_console import RunConsole

    def _fire():
        try:
            st = RunConsole.instance().status(run_id)
            if st and st.get("status") == "RUNNING":
                RunConsole.instance().cancel(run_id)
                logger.warning("ai_action_run_timeout_cancelled run_id=%s after=%ss",
                               run_id, timeout_seconds)
        except Exception:  # noqa: BLE001 - 看门狗失败不影响的执行面
            logger.exception("ai_action_run_timeout_error run_id=%s", run_id)

    timer = threading.Timer(max(int(timeout_seconds), 60), _fire)
    timer.daemon = True
    timer.start()


def _on_action_complete(action_id: int, run) -> None:
    status_map = {"SUCCESS": "succeeded", "FAILED": "failed", "CANCELED": "cancelled"}
    status = status_map.get(getattr(run, "status", "FAILED"), "failed")
    summary = f"{getattr(run, 'status', '?')}（exit={getattr(run, 'exit_code', None)}）"
    _finalize_action(action_id, status, summary)


# ─────────────────────────── 轮次任务 ───────────────────────────

def _converge_pending(
    session_id: int, produced_output: bool, *, error: str | None = None
) -> None:
    """H1：任何退出路径都不得留 pending 占位（否则前端无限轮询+气泡永挂）。

    轮次已产出真实回复 → 删除占位；未产出（异常路径）→ 标 failed 留错误可见。
    例外：止轮等待自动执行的续轮时**不调用本函数**——占位要留着驱动前端轮询，
    由续轮自己收口（见 `ai_assistant_turn_task` 的 finally）。
    """
    db = SessionLocal()
    try:
        pending = (
            db.query(AiChatMessage)
            .filter(
                AiChatMessage.session_id == session_id,
                AiChatMessage.role == "assistant",
                AiChatMessage.status.in_(["pending", "running"]),
            )
            .all()
        )
        if not pending:
            return
        if produced_output:
            for msg in pending:
                db.delete(msg)
        else:
            for msg in pending:
                msg.status = "failed"
                meta = dict(msg.meta or {})
                meta["error"] = error or "本轮未产出回复（编排异常，详见后端日志）"
                msg.meta = meta
        db.commit()
    finally:
        db.close()


async def ai_assistant_turn_task(ctx: dict, *, session_id: int) -> None:
    db = SessionLocal()
    produced_output = False
    awaiting_continuation = False
    try:
        session = db.get(AiChatSession, session_id)
        if session is None:
            logger.warning("ai_turn_session_missing id=%s", session_id)
            return
        try:
            cfg, api_key = load_effective_config(db)
        except AiNotConfigured as exc:
            _fail_pending_messages(db, session, f"ai_not_configured: {exc}")
            return

        client = LlmClient(
            base_url=cfg.base_url,
            api_key=api_key,
            model=cfg.model,
            timeout_seconds=float(cfg.request_timeout_seconds),
        )
        # 工具面按用户角色裁剪（PR-Agent gate：审计/设置为 admin-only 端点，
        # 镜像它们的工具不得对普通用户开放）
        from backend.models.user import User as UserModel

        user_row = db.get(UserModel, session.user_id)
        is_admin = getattr(user_row, "role", None) == "admin"
        tool_names = allowed_tool_names(is_admin)
        openai_tools = to_openai_tools(tool_names)
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(_history_as_llm_messages(db, session.id))

        try:
            for _turn in range(max(int(cfg.max_turns), 1)):
                reply = await client.chat(
                    messages, tools=openai_tools, temperature=float(cfg.temperature)
                )
                if not reply.tool_calls:
                    produced_output = True
                    _add_message(
                        db, session, role="assistant", content=reply.content,
                        meta={
                            "usage": reply.usage,
                            "latency_ms": reply.latency_ms,
                        },
                    )
                    return

                meta: dict = {"usage": reply.usage, "latency_ms": reply.latency_ms}
                produced_output = True
                assistant_msg = _add_message(
                    db, session, role="assistant", content=reply.content,
                    tool_calls=[
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in reply.tool_calls
                    ],
                    meta=meta,
                )
                messages.append(
                    {
                        "role": "assistant",
                        "content": reply.content or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(
                                        tc.arguments, ensure_ascii=False
                                    ),
                                },
                            }
                            for tc in reply.tool_calls
                        ],
                    }
                )

                yielded = False
                proposed_action_id: int | None = None
                for tc in reply.tool_calls:
                    spec = TOOLS.get(tc.name)
                    if spec is None or tc.name not in tool_names:
                        # 双门禁：payload 裁剪 + 执行面校验——模型点名调用
                        # 角色外工具（如幻觉出的 admin-only 工具）同样拒绝
                        note = f"未知工具 {tc.name}（不可用）"
                    elif spec.kind == "query":
                        try:
                            result = await asyncio.to_thread(_run_query, tc.name, tc.arguments)
                            note = result
                        except ToolValidationError as exc:
                            note = f"参数校验失败：{exc}"
                        except Exception as exc:  # noqa: BLE001 - 查询失败回填重试
                            note = f"查询失败：{exc}"
                            logger.warning("ai_query_failed tool=%s err=%s", tc.name, exc)
                    elif not user_may_invoke_tool(user_row, spec):
                        note = f"无权使用工具 {tc.name}（需要更高权限）"
                    else:
                        mode = _decide_execution_mode(spec, cfg, user_row)
                        action = _create_action(
                            db, session,
                            tool_name=tc.name, arguments=tc.arguments, mode=mode,
                        )
                        proposed_action_id = action.id
                        if mode == "proposed":
                            note = (
                                f"该操作需要管理员审批（操作卡 #{action.id}）。"
                                "请在平台上批准或拒绝。"
                            )
                            yielded = True
                        else:
                            try:
                                await asyncio.to_thread(execute_action, action.id)
                            except Exception as exc:  # noqa: BLE001
                                note = f"启动执行失败：{exc}"
                                yielded = True
                            else:
                                db.refresh(action)
                                if action.status in _ACTION_TERMINAL:
                                    # 内联出终态（服务型工具 / RunKeyBusy / spawn 失败）：
                                    # 此刻续轮与本轮 SAQ job 同 key 会被静默丢弃
                                    # （saq enqueue 对已存在的 key 返回 None），所以必须
                                    # 在本轮把真实结果喂回模型——也不能谎报「已开始执行」
                                    note = (
                                        f"操作卡 #{action.id} 已结束（{action.status}）："
                                        f"{action.result_summary or ''}"
                                    )
                                else:
                                    note = (
                                        f"已开始执行（操作卡 #{action.id}），"
                                        "完成后我会汇报结果。"
                                    )
                                    yielded = True
                                    awaiting_continuation = True

                    _add_message(
                        db, session, role="tool", content=note, tool_call_id=tc.id or None
                    )
                    messages.append(
                        {"role": "tool", "tool_call_id": tc.id or "", "content": note}
                    )

                if proposed_action_id is not None and assistant_msg is not None:
                    assistant_msg.meta = {**dict(assistant_msg.meta or {}),
                                         "proposed_action_id": proposed_action_id}
                    db.commit()

                if yielded:
                    return  # 止轮：等 on_complete 续轮 / 审批

            produced_output = True
            _add_message(
                db, session, role="assistant",
                content="已达单轮工具迭代上限（max_turns），请拆分请求或继续对话。",
            )
        except (AiAuthError, AiUpstreamTimeout, AiBadResponse) as exc:
            _fail_pending_messages(db, session, str(exc))
            logger.warning("ai_turn_llm_failed session=%s err=%s", session_id, exc)
    finally:
        try:
            if awaiting_continuation:
                # 止轮等自动执行：占位留给续轮驱动前端轮询，由续轮自己收口。
                # 删掉它 UI 就停轮，动作完成后的汇报只落库不上屏。
                ensure_pending_placeholder(session_id)
            else:
                _converge_pending(session_id, produced_output)
        except Exception:  # noqa: BLE001 - 收口失败仅记日志，不掩盖主流程异常
            logger.exception("ai_turn_converge_pending_failed session=%s", session_id)
        db.close()
