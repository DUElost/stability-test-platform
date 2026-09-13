"""AEE emit 意图簿（#1719，承接 #803 第 1 处）。

#1687 折衷版把崩溃窗口从「emit 后未落盘 → 重启重拉重发（重复）」换成
「processed 已落盘、emit 未发生 → 永久丢失」。意图簿消除后者：

1. processor 在写 processed **之前**落一条 intent 占位（记录 payload，keys
   为空）——占位语义 = 「该条目已在本地 finalize，emit 必须发生」；
2. reconciler 执行效果前把幂等 keys（``seq_no`` + envelope + 事件 UUID）
   写进占位（**先持久化、后效果**）；效果本身幂等：
   log_signal 靠 `(job_id, seq_no)`（LocalDB `INSERT OR IGNORE` + 后端
   `ON CONFLICT DO NOTHING`）、DLE 靠预分配 UUID（后端按 id upsert，
   #1051/R09-R01）；
3. 完成（emit + DLE 都已发起）后标 ``done``；
4. reconciler 每轮 sweep：``!done`` → 重放（keys 缺失则新分配，同幂等键）；
   ``done`` 且 line 已 processed → 清理；line 未 processed → 保留（重拉
   路径会复用 keys）。

存储与 processed/pending 同库（LocalDB ``agent_state``），键挂在 processed
键命名空间下：``{processed_key}:emit_intents``。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

INTENT_KEY_SUFFIX = ":emit_intents"

# 重放失败的有界重试上限（防坏数据/持久故障无限重放刷屏；达阈值丢弃并计
# signals_dropped，交日志审计）。
MAX_REPLAY_ATTEMPTS = 5


def intent_state_key(processed_key: str) -> str:
    """意图簿状态键：与 processed 同命名空间（serial/aee_type/prefix 隔离）。"""
    return f"{processed_key}{INTENT_KEY_SUFFIX}"


def load_intents(state_store: Any, processed_key: str) -> Dict[str, dict]:
    """读取意图簿：{line: record}。坏数据按空簿处理（不阻塞主流程）。"""
    raw = state_store.get_state(intent_state_key(processed_key), "{}")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("aee_emit_intent_book_unreadable key=%s", processed_key)
        return {}
    if not isinstance(data, dict):
        logger.warning("aee_emit_intent_book_shape_invalid key=%s", processed_key)
        return {}
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}


def save_intents(state_store: Any, processed_key: str, intents: Dict[str, dict]) -> None:
    """写回意图簿（空簿写 ``{}``——ScriptStateStore 无 delete_state）。"""
    state_store.set_state(
        intent_state_key(processed_key),
        json.dumps(intents, ensure_ascii=False, sort_keys=True),
    )


def new_intent_record(
    *,
    payload: Dict[str, Any],
    job_id: int,
    detected_at_iso: str,
    entry_origin: str,
    detected_at_override_iso: str = "",
) -> dict:
    """构造占位记录（keys 为空，done=False）。

    payload 形状见 processor.process_device_logs 的 on_entry_intent 文档。
    """
    return {
        "job_id": int(job_id),
        "aee_type": str(payload.get("aee_type") or ""),
        "parsed": dict(payload.get("parsed") or {}),
        "output_subdir": str(payload.get("output_subdir") or ""),
        "entry_origin": entry_origin,
        "detected_at": detected_at_iso,
        "detected_at_override": detected_at_override_iso or None,
        "seq_no": None,
        "signal_envelope": None,
        "dle_payload": None,
        "done": False,
        "attempts": 0,
    }
