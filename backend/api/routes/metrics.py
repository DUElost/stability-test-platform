"""
Metrics API Endpoint

Exposes Prometheus metrics at /metrics endpoint.
"""

from __future__ import annotations

import logging
import os
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.database import get_db
from backend.core.metrics import (
    chain_coverage_devices,
    device_online,
    get_metrics_response,
    host_device_adb_state,
    host_health_reason,
    host_kernel_log_channel,
    host_online,
    is_prometheus_available,
    sweep_stale_host_gauge_children,
    record_db_lock_waiters,
)
from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device, Host
from backend.models.schedule import TaskSchedule
from backend.services.auth_session import authenticate_token

logger = logging.getLogger(__name__)

router = APIRouter()


# #1258：host/device 在线计数在 /metrics 拉取时现算（表小、低基数，无周期
# 任务的 staleness；label 用小写枚举值与仪表板 PromQL 对齐）。
# ADR-0038 D5：host 侧排除退役（退役 = 不再是容量）——`stability_host_online`
# 的 PromQL 语义变化：退役主机的 online/offline/... 计数归 0，range 查询会在
# 退役时刻出现台阶（历史序列保留旧值）；若有告警按 fleet 规模阈值判断，退役
# 会正常触发「规模下降」而非误报。
_FLEET_GAUGES = (
    (Host, host_online, HostStatus, Host.retired_at.is_(None)),
    (Device, device_online, DeviceStatus, None),
)


def _refresh_fleet_gauges(db: Session) -> None:
    if not is_prometheus_available():
        return
    try:
        for model, gauge, status_enum, extra_filter in _FLEET_GAUGES:
            query = db.query(model.status, func.count()).group_by(model.status)
            if extra_filter is not None:
                query = query.filter(extra_filter)
            counts = dict(query.all())
            for member in status_enum:
                gauge.labels(status=member.value.lower()).set(counts.get(member.value, 0))
    except SQLAlchemyError:
        # 观测面不因 DB 抖动整体 500：保留其余指标输出，仅跳过舰队计数。
        logger.warning("metrics_fleet_gauge_refresh_failed", exc_info=True)


#: #2754：adb_state 的**封闭分桶词表**（series 基数 = 在册 host 数 × 4，
#: 不随 adb 原始状态串漂移；顺序即落值顺序，测试与告警选择器都按它对齐）。
_ADB_STATE_BUCKETS = ("device", "offline", "unauthorized", "other")

#: #2791：本进程**已暴露过**的 host_id。prometheus_client 的 label child 一旦创建
#: 就常驻 registry，只刷新在册集合会留下冻结的故障值（「先 live、批量 offline、
#: 后退役」的 host 会被 StabilityHostAdbOfflineConcentration 永久 firing），
#: 基数也会随机器轮换单调增长。故每轮按差集 `remove`（位置参数形态）。
_adb_gauge_exposed_hosts: set[str] = set()


def _adb_state_bucket(raw: Optional[str]) -> str:
    """把 adb 的自由字符串归进封闭词表。

    不归桶有两个后果：`no permissions` / 空串 / 各版本 adb 的自造状态会各自成为一条
    series 标签值（基数交给运气），且告警选择器必须逐值列举——**漏一个值就静默不告**，
    正是本仓反复踩的「绿而空」形态（#1958 死锁四周零指标、#1257 不存在的标签选择器）。
    """
    value = (raw or "").strip()
    return value if value in _ADB_STATE_BUCKETS else "other"


def _refresh_host_device_adb_gauges(db: Session) -> None:
    """#2754：per-host × adb_state 计数，拉取期现算（与 `_refresh_fleet_gauges` 同口径）。

    存在理由：fleet 级 `stability_device_online{status=...}` 只有总数——2026-09-18 host
    `.81` 的 15/16 台 adb offline 在平台上**零告警**（host ONLINE、心跳新鲜、mount ok），
    总量视角下那只是 fleet 里少了几台。按 host 分桶后，「单台 host 的设备批量不可达」
    才是可表达的事实。

    三条口径是刻意的：

    - **退役 host 不进指标**（ADR-0038 D5：退役 = 不再是容量）——否则退役机上残留的设备行
      会让告警永远盯着一台已不存在的机器，且没人会去处理它。**含差集清理**（#2791）：
      「先 live 后退役」的 host 其 label child 必须被 remove，而不是只停止刷新
      （prometheus_client 的 child 常驻 registry，停刷新 = 冻结故障值）；
    - 设备行经 `join Host` 过滤：`host_id` 为空或指向不存在 host 的设备**不计**——
      它们没有 host 归属，硬造一个 `(none)` 标签值会让 fleet 级异常混进 per-host 视角；
    - 每台在册 host 的四个桶**全部落值（含 0）**：缺 series 时 `max_over_time` 窗口里没有
      基线，「从来没设备」与「刚掉光」就无法区分（后者正是要告的形态）。
    """
    if not is_prometheus_available():
        return
    try:
        rows = (
            db.query(Device.host_id, Device.adb_state, func.count())
            .join(Host, Host.id == Device.host_id)
            .filter(
                Host.retired_at.is_(None),
                Host.status == HostStatus.ONLINE.value,
            )
            .group_by(Device.host_id, Device.adb_state)
            .all()
        )
        counted: dict[str, dict[str, int]] = {}
        for host_id, adb_state, count in rows:
            buckets = counted.setdefault(host_id, {b: 0 for b in _ADB_STATE_BUCKETS})
            buckets[_adb_state_bucket(adb_state)] += int(count)
        # liveness 门同口径：OFFLINE/DEGRADED host 不进在册集——其旧 label child
        # 由 #2791 差集机制 remove，故障值不会冻结在 registry。
        live_hosts = [
            host_id
            for (host_id,) in db.query(Host.id).filter(
                Host.retired_at.is_(None),
                Host.status == HostStatus.ONLINE.value,
            ).all()
        ]
        for host_id in live_hosts:
            buckets = counted.get(host_id, {b: 0 for b in _ADB_STATE_BUCKETS})
            for bucket in _ADB_STATE_BUCKETS:
                host_device_adb_state.labels(host_id=host_id, state=bucket).set(
                    buckets.get(bucket, 0)
                )
        # #2791：退役/移出在册的 host 必须**移除**其 label child——只刷新 live 集合会
        # 把退役前的故障值冻结在 registry 里（告警无 liveness 门 ⇒ 15m 起永久 firing），
        # 与「退役 host 不进指标（ADR-0038 D5）」相反。差集清理只对上一轮已暴露过的
        # host 生效；remove 不存在的 child 是 no-op，故进程重启后首轮也安全。
        live_set = set(live_hosts)
        for host_id in _adb_gauge_exposed_hosts - live_set:
            for bucket in _ADB_STATE_BUCKETS:
                host_device_adb_state.remove(host_id, bucket)
        _adb_gauge_exposed_hosts.clear()
        _adb_gauge_exposed_hosts.update(live_set)
    except SQLAlchemyError:
        # 与舰队 gauge 同一失败姿势：DB 抖动时跳过本组，不拖垮整次抓取。
        logger.warning("metrics_host_adb_gauge_refresh_failed", exc_info=True)


#: #2900/#2957：host 健康 reason 的**封闭分桶词表**。逐字取自 agent 侧的产出点
#: （`capacity_reporter._compute_health` 的 `reasons.append(...)` 字面量 +
#: `kernel_usb_faults` 的 `REASON_*` 常量），由
#: `tests/test_host_health_reason_surface.py` 双向绑回源码：agent 加了新 reason 而
#: 这里没跟 ⇒ 红；这里留了 agent 已不产出的 reason ⇒ 也红。**不做成 import agent 模块**
#: 是因为词表一半是字面量、一半是常量，混两种口径比统一抄一遍更容易漂（守卫测的是
#: 真值本身，抄错了当场红）。
#: `other` 是兜底桶：agent 先于控制面发新 reason 时，它进 `other` 而**不是**消失——
#: 与 `_adb_state_bucket` 同理由（不让自由字符串直接当 label 值，也不让事实凭空蒸发）。
_HEALTH_REASONS = (
    "cpu_high",
    "ram_high",
    "disk_high",
    "disk_unknown",
    "mount_failed",
    "adb_low_healthy_devices",
    "adb_multiple_servers",
    "usb_tree_empty",
    "usb_host_controller_dead",
    "usb_link_degraded",
    "other",
)

#: #2957：内核日志通道可用性词表（agent 侧 `kernel_usb_faults.CHANNEL_STATES`）。
#: `unknown` 同时兜住「老 agent 没这个字段」——那是**未覆盖**而非「通道正常」。
_KERNEL_LOG_STATES = ("ok", "unavailable", "unknown")

#: #2900/#2957：本轮**已暴露过** label child 的 host，两个 gauge 各一份——
#: 一台 host 可以「通道有值但 reason 未上报」（老 agent / health 块缺 reasons），
#: 共用一份差集会让它上一轮的 reason 值被冻结在 registry 里（停刷新 ≠ 停暴露，
#: #2791 的教训就是这一条）。
_reason_gauge_exposed_hosts: set[str] = set()
_channel_gauge_exposed_hosts: set[str] = set()


def _refresh_host_health_gauges(db: Session) -> None:
    """#2900 的控制面半边 + #2957 的通道可见性：把 `host.extra` 里的 agent 判定折成可告警 series。

    存在理由（#2900 原文的失效形状）：Agent 上报的 reason 落到 `host_extra["health"]`
    这个 JSON 就停了（`api/routes/heartbeat.py` 只赋值、不计量），告警文件里没有任何
    expr 引用它 ⇒「xHCI 主控死亡、整机 USB 全盲 11 天」零告警。本函数补的是那条链的
    最后一跳：**指标化才有资格被告警**。

    四条口径是刻意的：

    - **在册 + ONLINE 才落值**（与 `_refresh_host_device_adb_gauges` 同口径，
      ADR-0038 D5）：OFFLINE/DEGRADED host 的 reason 是**上一次心跳的快照**，冻结在
      registry 里就是一台已失联机器的永久红灯；那种机器该由心跳超时类告警负责；
    - **拿不到 `health.reasons` 列表的 host 不进 reason 指标**（未知 ≠ 干净）：
      宁可不产 series，也不把「没上报」写成「一切正常」——那正是本单要治的假绿；
    - **每台落值 host 的全词表都写（含 0）**：缺 series 时 PromQL 窗口里没有基线，
      「从来没这个 reason」与「刚掉出词表」不可分辨；
    - **两 gauge 独立差集 remove**（#2791 同族）：移出在册/转 OFFLINE 的 host 其 child
      必须被移除，而不是停刷新。
    """
    if not is_prometheus_available():
        return
    try:
        rows = (
            db.query(Host.id, Host.extra)
            .filter(
                Host.retired_at.is_(None),
                Host.status == HostStatus.ONLINE.value,
            )
            .all()
        )
    except SQLAlchemyError:
        logger.warning("metrics_host_health_gauge_refresh_failed", exc_info=True)
        return

    live_reason: set[str] = set()
    live_channel: set[str] = set()
    try:
        for raw_host_id, extra in rows:
            host_id = str(raw_host_id)
            blob = extra if isinstance(extra, dict) else {}

            health = blob.get("health")
            reasons = health.get("reasons") if isinstance(health, dict) else None
            if isinstance(reasons, list):
                present = {r for r in reasons if isinstance(r, str)}
                known = present & set(_HEALTH_REASONS)
                if present - known:
                    # 新 reason 先落 `other` 兜底桶：不静默消失（词表绑定见守卫测试）
                    known = known | {"other"}
                for reason in _HEALTH_REASONS:
                    host_health_reason.labels(host_id=host_id, reason=reason).set(
                        1 if reason in known else 0
                    )
                live_reason.add(host_id)

            # #2957：通道可用性落 capacity（观测面，不参与 health.status，更不打闸）。
            # 键缺失 = 老 agent / 从未扫成 → `unknown`，与 `unavailable`（明确读不到）分开：
            # 前者是覆盖缺口，后者是 #2900 判据恒不命中的直接原因。
            capacity = blob.get("capacity")
            raw_channel = (
                capacity.get("usb_kernel_log") if isinstance(capacity, dict) else None
            )
            state = raw_channel if raw_channel in _KERNEL_LOG_STATES else "unknown"
            for candidate in _KERNEL_LOG_STATES:
                host_kernel_log_channel.labels(host_id=host_id, state=candidate).set(
                    1 if candidate == state else 0
                )
            live_channel.add(host_id)
    except Exception:
        # 渲染中途出错：本轮不落新值，也不清旧 child（下轮自愈），但绝不 500 整次抓取。
        logger.warning("metrics_host_health_gauge_render_failed", exc_info=True)
        return

    for host_id in _reason_gauge_exposed_hosts - live_reason:
        for reason in _HEALTH_REASONS:
            host_health_reason.remove(host_id, reason)
    for host_id in _channel_gauge_exposed_hosts - live_channel:
        for state in _KERNEL_LOG_STATES:
            host_kernel_log_channel.remove(host_id, state)
    _reason_gauge_exposed_hosts.clear()
    _reason_gauge_exposed_hosts.update(live_reason)
    _channel_gauge_exposed_hosts.clear()
    _channel_gauge_exposed_hosts.update(live_channel)


_LOCK_WAIT_SQL = text(
    "SELECT count(*) AS waiters, "
    # 必须用 clock_timestamp()（真实当前时间）而不是 now()：now() 是**事务起始**
    # 时间，而抓取事务通常开在等待出现之前 → now() - query_start 会是负数，
    # 取 max 后被 clamp 成 0，指标恒 0（本单回归测试实测踩到）。
    "COALESCE(max(EXTRACT(EPOCH FROM (clock_timestamp() - query_start))), 0) "
    "AS max_wait_seconds "
    "FROM pg_stat_activity "
    # 限定本库：pg_stat_activity 是全实例视图，不过滤会把别的库的等待算进来。
    "WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid() "
    "AND datname = current_database()"
)


def _refresh_lock_wait_gauges(db: Session) -> None:
    """#2104：把「此刻有多少会话在等锁 / 等最久多久」同步到 Prometheus。

    为什么需要它：锁序修复（#1959/#1980/#1985/#2022）消掉了环路等待，但代价会转移到
    **普通等待**（清理事务持行锁期间热路径排队、反向亦然）——这类等待对
    ``stability_db_deadlock_total`` 不可见，只看死锁计数会得出「计数为 0 = 无代价」
    的错误结论。与舰队 gauge 同口径：拉取期现算（一条聚合，无周期任务 staleness），
    失败只跳过本组、不拖垮整次抓取；非 PG 方言（sqlite）直接跳过，因为
    ``pg_stat_activity`` 是 PG 专有视图。
    """
    if not is_prometheus_available():
        return
    try:
        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            return
        # **必须先清统计快照**：`pg_stat_activity` 属 `pg_stat_*` 视图族，PG 15+ 在
        # **同一事务内**读的是事务起始时的写时复制快照。本函数前面刚跑过舰队 gauge
        # 查询（同一个请求 session），若不清快照，本次抓取就看不到「本事务开始之后
        # 才出现的等待」——表现为采样恒偏低甚至恒 0（#2022 在
        # `pg_stat_database.deadlocks` 上踩过同一坑；本单的回归测试也正因此先红）。
        db.execute(text("SELECT pg_stat_clear_snapshot()"))
        waiters, max_wait_seconds = db.execute(_LOCK_WAIT_SQL).one()
    except SQLAlchemyError:
        logger.warning("metrics_lock_wait_gauge_refresh_failed", exc_info=True)
        return
    record_db_lock_waiters(int(waiters or 0), float(max_wait_seconds or 0.0))


def _metrics_auth_required() -> bool:
    return os.getenv("STP_METRICS_AUTH_REQUIRED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def verify_metrics_access(
    authorization: Optional[str] = Header(None),
    x_agent_secret: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> None:
    """Optional Bearer access token or X-Agent-Secret when auth is enabled.

    R02-D3（#903）：Bearer 分支走 auth_session 完整校验面（此前仅签名级
    decode——停用/删除用户的 token 到 exp 前全通）。"""
    if not _metrics_auth_required():
        return

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if authenticate_token(db, token, expected_type="access"):
            return

    if x_agent_secret:
        try:
            expected = require_agent_secret()
        except AgentSecretNotConfiguredError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc
        if secrets.compare_digest(x_agent_secret, expected):
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Metrics authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.get("/metrics")
async def metrics(
    db: Session = Depends(get_db),
    _auth: None = Depends(verify_metrics_access),
):
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus exposition format.
    """
    _refresh_fleet_gauges(db)
    _refresh_host_device_adb_gauges(db)
    _refresh_host_health_gauges(db)
    _refresh_lock_wait_gauges(db)
    _refresh_chain_coverage_gauges(db)
    _sweep_push_host_gauge_children(db)
    data, content_type = get_metrics_response()
    return Response(content=data, media_type=content_type)


@router.get("/metrics/health")
async def metrics_health():
    """
    Metrics subsystem health check.

    Returns whether the Prometheus client library is available.
    The authoritative /health endpoint (with DB connectivity check) is in main.py.
    """
    return {
        "status": "healthy",
        "prometheus_available": is_prometheus_available()
    }


def _sweep_push_host_gauge_children(db: Session) -> None:
    """#2873：拉取端差集清理推式 per-host gauge 的退役 host child。

    live=「在册」（retired_at IS NULL）——短暂掉线 host 的末值有操作意义不清；
    只处理「不再是容量」的（ADR-0038 D5），与 adb_state 的 #2791 差集同族但
    live 口径更宽。失败只跳过本轮（不拖垮渲染），下个拉取周期自愈。
    """
    try:
        live = {
            str(hid)
            for (hid,) in db.query(Host.id).filter(Host.retired_at.is_(None)).all()
        }
    except SQLAlchemyError:
        logger.warning("metrics_push_gauge_sweep_query_failed", exc_info=True)
        return
    try:
        removed = sweep_stale_host_gauge_children(live)
        if removed:
            logger.info("metrics_push_gauge_children_swept count=%d", removed)
    except Exception:
        # registry 状态异常不应让 /metrics 500：child 清理是尽力而为的卫生动作。
        logger.warning("metrics_push_gauge_sweep_failed", exc_info=True)

def _refresh_chain_coverage_gauges(db: Session) -> None:
    """#2909③：链覆盖差三元组，拉取期现算（fleet 表小，与 _refresh_fleet_gauges 同法）。

    清单侧读 `task_schedule.device_ids`（enabled 行 JSON 并集）——这是**权威源**而非
    观测样本：链 run 的即时修剪（#2909 诊断里规模浮动的来源）不参与判定，
    度量的是「结构上有没有设备掉出全部清单」，正是本告警要抓的失效。
    退役 host 的设备不计 online_total（ADR-0038 D5：退役=不再是容量，与 fleet
    gauge 同族口径；否则退役机上冻结的 ONLINE 行会把 gap 虚报大）。
    """
    try:
        online_ids = set(
            db.execute(
                select(Device.id)
                .join(Host, Host.id == Device.host_id)
                .where(
                    Device.status == DeviceStatus.ONLINE.value,
                    Host.retired_at.is_(None),
                )
            ).scalars().all()
        )
        scheduled: set[int] = set()
        for (ids,) in db.query(TaskSchedule.device_ids).filter(
            TaskSchedule.enabled == True,  # noqa: E712
        ).all():
            if isinstance(ids, list):
                for x in ids:
                    try:
                        scheduled.add(int(x))
                    except (TypeError, ValueError):
                        continue
        gap = len(online_ids - scheduled)
        chain_coverage_devices.labels(kind="online_total").set(len(online_ids))
        chain_coverage_devices.labels(kind="scheduled_union").set(len(scheduled))
        chain_coverage_devices.labels(kind="gap_missing").set(gap)
    except SQLAlchemyError:
        logger.warning("metrics_chain_coverage_refresh_failed", exc_info=True)