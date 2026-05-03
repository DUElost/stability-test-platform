"""Watcher 子系统的接口契约定义（Agent 侧期望的后端 API shape）。

本文件是**单边声明**，不依赖后端真实已实现的 Schema。
作用：
  1. JobSession / WatcherManager / SignalEmitter 在处理 payload 时做 shape 校验（fail-fast）
  2. 后端在实现 /agent/jobs/claim、/agent/jobs/{id}/complete、/agent/log-signals 时
     以本文件为契约对齐字段，不一致必须同步改两边
  3. 运维定位时可直接读本文件了解"Agent 假设了什么"

字段约定：
  - 所有时间戳使用 ISO8601 带时区（UTC 推荐）
  - 所有 id 使用整数（job_id, device_id）；serial/host_id 使用字符串
  - payload 应为纯 JSON 友好对象（不含 datetime 等非 JSON 原生类型）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


# ----------------------------------------------------------------------
# 契约 1: GET /api/v1/agent/jobs/pending（或 /claim）响应中的 Job 对象
# ----------------------------------------------------------------------

class JobClaimPayload(TypedDict, total=False):
    """Agent claim 到一个 Job 时，后端必须返回的最小字段集。

    total=False 允许后端返回更多字段；但下面 REQUIRED_CLAIM_FIELDS
    列出的字段必须全部存在，否则 JobSession 启动即失败。
    """

    # ---- 必需字段 ----
    id:                int    # job_instance.id
    device_id:         int
    device_serial:     str    # 设备序列号（adb -s 参数）
    host_id:           str
    pipeline_def:      Dict[str, Any]

    # ---- 可选字段 ----
    workflow_run_id:   int
    task_template_id:  int
    watcher_policy:    Optional[Dict[str, Any]]    # 覆盖 WatcherPolicy 默认
    device_lease_expires_at: Optional[str]          # ISO8601


REQUIRED_CLAIM_FIELDS: tuple[str, ...] = (
    "id",
    "device_id",
    "device_serial",
    "host_id",
    "pipeline_def",
)


# ----------------------------------------------------------------------
# 契约 2: POST /api/v1/agent/jobs/{id}/complete 请求体
# ----------------------------------------------------------------------

class WatcherSummaryPayload(TypedDict, total=False):
    """JobSessionSummary.to_complete_payload() 的 shape。

    后端 complete 端点应把这些字段回填到 job_instance.watcher_* 列。
    """

    watcher_id:         Optional[str]
    watcher_started_at: Optional[str]    # ISO8601, UTC
    watcher_stopped_at: Optional[str]    # ISO8601, UTC
    watcher_capability: str              # inotifyd_root | inotifyd_shell | polling | unavailable | skipped | stub
    log_signal_count:   int
    watcher_stats:      Dict[str, int]   # { events_total, events_dropped, pulls_ok, ... }


class JobCompleteRequest(TypedDict, total=False):
    """Agent 发送 /complete 时的请求体 shape。"""

    # ---- 业务字段（已有）----
    status:         str                # FINISHED | FAILED | CANCELED
    reason:         Optional[str]
    result_summary: Optional[Dict[str, Any]]

    # ---- Watcher 回传（本次新增）----
    watcher_summary: WatcherSummaryPayload


# ----------------------------------------------------------------------
# 契约 3: POST /api/v1/agent/log-signals（outbox → 后端）
# ----------------------------------------------------------------------

class LogSignalEnvelope(TypedDict, total=False):
    """单条 log_signal 从 Agent outbox 同步到后端的信封。

    对应 DB 表 job_log_signal 的字段（migration k9f0a1b2c3d4）。
    幂等键：(job_id, seq_no)
    """

    # ---- 必需字段 ----
    job_id:         int
    seq_no:         int
    host_id:        str
    device_serial:  str
    category:       str    # ANR | AEE | VENDOR_AEE | MOBILELOG
    source:         str    # inotifyd | polling
    path_on_device: str
    detected_at:    str    # ISO8601

    # ---- 可选字段（LogPuller 完成后补充）----
    artifact_uri:   Optional[str]   # NFS 路径
    sha256:         Optional[str]
    size_bytes:     Optional[int]
    first_lines:    Optional[str]
    extra:          Optional[Dict[str, Any]]


REQUIRED_LOG_SIGNAL_FIELDS: tuple[str, ...] = (
    "job_id",
    "seq_no",
    "host_id",
    "device_serial",
    "category",
    "source",
    "path_on_device",
    "detected_at",
)


class LogSignalBatch(TypedDict):
    """OutboxDrainer 批量上送的请求体。"""

    signals: List[LogSignalEnvelope]


# ----------------------------------------------------------------------
# 校验工具（供 JobSession / Emitter fail-fast 使用）
# ----------------------------------------------------------------------

class ContractViolation(ValueError):
    """payload 不满足契约时抛出。"""


def validate_claim_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """校验 /agent/jobs/claim 响应。

    返回原 payload（便于链式调用）；字段缺失抛 ContractViolation。
    """
    missing = [k for k in REQUIRED_CLAIM_FIELDS if k not in payload or payload[k] is None]
    if missing:
        raise ContractViolation(
            f"claim_payload missing required fields: {missing}; "
            f"契约来自 backend/agent/watcher/contracts.py:REQUIRED_CLAIM_FIELDS"
        )
    # 类型宽松校验
    for int_field in ("id", "device_id"):
        if not isinstance(payload[int_field], int):
            raise ContractViolation(f"claim_payload.{int_field} must be int, got {type(payload[int_field])}")
    for str_field in ("device_serial", "host_id"):
        if not isinstance(payload[str_field], str) or not payload[str_field]:
            raise ContractViolation(f"claim_payload.{str_field} must be non-empty string")
    return payload


def validate_log_signal(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """校验单条 log_signal 信封。"""
    missing = [k for k in REQUIRED_LOG_SIGNAL_FIELDS if k not in envelope or envelope[k] is None]
    if missing:
        raise ContractViolation(
            f"log_signal missing required fields: {missing}"
        )
    if envelope["category"] not in {"ANR", "AEE", "VENDOR_AEE", "MOBILELOG"}:
        raise ContractViolation(f"log_signal.category unknown: {envelope['category']}")
    if envelope["source"] not in {"inotifyd", "polling"}:
        raise ContractViolation(f"log_signal.source unknown: {envelope['source']}")
    return envelope
