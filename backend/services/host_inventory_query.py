"""Agent host 服务端增量筛选、全局优先级排序、独立 Facets 与跨页预览（#496 / #4117 阶段 B2-2）。

设计约束（``docs/design/2026-11-24-list-pagination-and-facets-contract.md`` §4）：
- 单一事实源：复用 ``inventory_status.effective_host_status`` 与 ``agent_version_info.build_host_version_view``，
  在同一请求内共享统一 ``now`` 时间基准与单次 ``desired_artifact_digest`` 计算；
- 严格纯只读：仅执行单次 ``SELECT``，零 DML / 零 COMMIT；
- 口径严格对齐：
  - ``trackable_agent_count`` 统计 ``ONLINE && agent_installed``；
  - ``aligned_agent_count`` 在该集合内统计 ``matched``；
  - ``upgrade_pending`` 在该集合内统计 ``drift``（快捷筛选 ``sync_status=upgrade_pending`` 复用完全相同谓词）；
  - ``unknown`` / ``pending`` 保留在分母 ``trackable_agent_count`` 中；
  - USB 故障优先级严格遵循既有 3/2/1/0 规则与退役/设备面已处置（``emptied_at``）豁免；
  - 挂载空数据不计为挂载故障；
- 排序稳定：支持全局 USB 故障优先级（``default`` / ``priority``）及显式列排序，统一以 ``Host.id ASC`` 为尾键。
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.api.schemas.host import (
    HostFacetsOut,
    HostOut,
    HostSelectionPreviewOut,
)
from backend.models.host import Device, Host
from backend.services.agent_version_info import build_host_version_view
from backend.services.host_updater import get_agent_code_version
from backend.services.inventory_status import effective_host_status

# 与前端 ExpandableHostTable.tsx REASON_LABELS 保持同源，确保 q 搜索可按中文原因命中
HEALTH_REASON_LABELS: Dict[str, str] = {
    "cpu_high": "CPU 过高",
    "ram_high": "内存过高",
    "disk_high": "磁盘过高",
    "disk_unknown": "磁盘未知",
    "mount_failed": "挂载失败",
    "adb_low_healthy_devices": "无健康设备",
    "usb_tree_empty": "USB 总线空树（主控/供电异常）",
    "adb_interfaces_missing": "USB 有设备但无 ADB 接口",
    "adb_multiple_servers": "ADB 多 server 冲突",
    "usb_host_controller_dead": "USB 主控失联（xHCI 死亡）",
    "usb_link_degraded": "USB 链路劣化",
    "single_instance_guard_degraded": "单实例守卫降级（双实例风险）",
}

ALLOWED_STATUS_FILTERS = frozenset({"ONLINE", "DEGRADED", "OFFLINE"})
ALLOWED_HEALTH_FILTERS = frozenset({"HEALTHY", "DEGRADED", "UNSCHEDULABLE", "unhealthy"})
ALLOWED_SYNC_FILTERS = frozenset({"upgrade_pending", "drift", "matched", "pending", "unknown"})
ALLOWED_SORT_BY = frozenset({"default", "priority", "id", "name", "devices", "cpu", "ram", "disk", "heartbeat"})
ALLOWED_SORT_DIR = frozenset({"asc", "desc"})
ALLOWED_PROJECTIONS = frozenset({"full", "list"})

# 只读动态排序快照缓存（内存 LRU + 短 TTL，零 DB 写入）：防止 cpu/ram/disk/heartbeat/priority
# 排序在翻页间隙因心跳排名波动产生跨页重复或漏项（契约 §4 规则 6）。
HOST_SORT_SNAPSHOT_TTL_SECONDS = 120.0
HOST_SORT_SNAPSHOT_MAX_ENTRIES = 256


class _HostSortSnapshotStore:
    """线程安全、有上限的只读排序快照表：仅存主机 ID 有序列表。"""

    def __init__(self, *, ttl_seconds: float, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._lock = threading.Lock()
        self._entries: "OrderedDict[str, Tuple[float, Tuple[Any, ...], Tuple[str, ...]]]" = OrderedDict()

    def _evict_expired_locked(self, now_mono: float) -> None:
        expired = [k for k, (exp, _, _) in self._entries.items() if exp <= now_mono]
        for key in expired:
            self._entries.pop(key, None)

    def put(self, scope_sig: Tuple[Any, ...], ordered_ids: Sequence[str]) -> str:
        token = uuid4().hex[:16]
        now_mono = time.monotonic()
        with self._lock:
            self._evict_expired_locked(now_mono)
            while len(self._entries) >= self._max:
                self._entries.popitem(last=False)
            self._entries[token] = (
                now_mono + self._ttl,
                scope_sig,
                tuple(str(hid) for hid in ordered_ids),
            )
        return token

    def get(self, token: str, scope_sig: Tuple[Any, ...]) -> Optional[Tuple[str, ...]]:
        now_mono = time.monotonic()
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            exp, saved_sig, ordered_ids = entry
            if exp <= now_mono:
                self._entries.pop(token, None)
                return None
            if saved_sig != scope_sig:
                return None
            self._entries.move_to_end(token)
            return ordered_ids

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_HOST_SORT_SNAPSHOTS = _HostSortSnapshotStore(
    ttl_seconds=HOST_SORT_SNAPSHOT_TTL_SECONDS,
    max_entries=HOST_SORT_SNAPSHOT_MAX_ENTRIES,
)


def clear_host_sort_snapshots() -> None:
    """清理内存中的只读主机排序快照（供测试隔离使用）。"""
    _HOST_SORT_SNAPSHOTS.clear()


def summarize_host_mounts(mounts: Any) -> Tuple[int, int, bool]:
    """统计主机挂载摘要 ``(total_count, failed_count, has_anomaly)``；空字典不计为异常。"""
    if not isinstance(mounts, dict) or not mounts:
        return 0, 0, False
    total = 0
    failed = 0
    for info in mounts.values():
        total += 1
        mounted = info is True or (isinstance(info, dict) and info.get("ok") is True)
        if not mounted:
            failed += 1
    return total, failed, failed > 0


def validate_host_projection(projection: Optional[str]) -> str:
    norm = projection.strip().lower() if isinstance(projection, str) and projection.strip() else "full"
    if norm not in ALLOWED_PROJECTIONS:
        raise HostQueryError(
            status_code=422,
            detail={
                "code": "INVALID_PROJECTION",
                "message": f"projection must be one of {sorted(ALLOWED_PROJECTIONS)}",
                "allowed": sorted(ALLOWED_PROJECTIONS),
            },
        )
    return norm

# 硬件体检全绿的纯 usb_tree_empty 仅在台账设备最近活跃时间落在该窗口内时计入 usb_fault；
# 超过窗口且硬件正常的主机自动视作长期空置（减少人工逐台点 emptied_at 负荷）。
EMPTY_TREE_RECENT_LEDGER_HOURS = 72


class HostQueryError(Exception):
    """主机查询校验领域错误（由路由层转为 HTTPException，遵守 C2 分层契约）。"""

    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class HostQueryFilters:
    """规范化后的 Agent host 查询筛选条件。"""

    include_retired: bool = False
    status: Optional[str] = None
    health_status: Optional[str] = None
    sync_status: Optional[str] = None
    usb_anomaly: Optional[bool] = None
    mount_anomaly: Optional[bool] = None
    q: Optional[str] = None


def derive_agent_installed(host: Host) -> Tuple[bool, Optional[str]]:
    """推断 Agent 是否曾安装成功（与 ONLINE/OFFLINE 正交）。

    信号优先级：extra.agent_installed → extra.agent_version → last_heartbeat 非空。
    """
    extra = host.extra if isinstance(host.extra, dict) else {}
    raw_at = extra.get("agent_installed_at")
    installed_at = str(raw_at) if raw_at else None
    if extra.get("agent_installed") is True:
        return True, installed_at
    if extra.get("agent_version"):
        return True, installed_at
    if host.last_heartbeat is not None:
        if not installed_at:
            installed_at = (
                host.last_heartbeat.isoformat()
                if hasattr(host.last_heartbeat, "isoformat")
                else str(host.last_heartbeat)
            )
        return True, installed_at
    return False, None


def sanitize_host_extra(raw_extra: Any) -> Dict[str, Any]:
    extra = dict(raw_extra or {}) if isinstance(raw_extra, dict) else {}
    for key in ("ssh_password", "ssh_key_path", "password", "secret", "token", "private_key"):
        extra.pop(key, None)
    return extra


def load_host_ledger_summary(
    db: Session,
    host_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Tuple[int, Optional[datetime]]]:
    """只读统计各主机在册（未退役）设备台账行数与最近活跃时间（``last_seen`` 缺省回落 ``created_at``）。"""
    query = (
        db.query(
            Device.host_id,
            func.count(Device.id),
            func.max(func.coalesce(Device.last_seen, Device.created_at)),
        )
        .filter(Device.retired_at.is_(None), Device.host_id.is_not(None))
    )
    if host_ids is not None:
        id_list = [str(hid) for hid in host_ids]
        if not id_list:
            return {}
        query = query.filter(Device.host_id.in_(id_list))
    rows = query.group_by(Device.host_id).all()
    return {
        str(hid): (int(cnt), latest_seen)
        for hid, cnt, latest_seen in rows
        if hid is not None
    }


def load_host_ledger_device_counts(
    db: Session,
    host_ids: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    """只读统计各主机在册（未退役）设备台账行数。"""
    return {
        hid: cnt
        for hid, (cnt, _) in load_host_ledger_summary(db, host_ids).items()
    }


def build_host_out_readonly(
    host: Host,
    *,
    desired_artifact_digest: Optional[str] = None,
    expected_code_revision: Optional[str] = None,
    now: Optional[datetime] = None,
    host_key_trust: Optional[str] = None,
    ledger_device_count: Optional[int] = None,
    ledger_latest_seen_at: Optional[datetime] = None,
    projection: str = "full",
) -> HostOut:
    """只读构造 ``HostOut``（不含 DB 查询；不修改 ORM 对象）。

    当 ``projection == "list"`` 时，仅输出挂载摘要标量（``mount_anomaly`` / ``mount_count`` /
    ``mount_failed_count``）并清空完整 ``mount_status`` 明细字典；完整挂载列表由
    ``GET /api/v1/hosts/{id}`` 按需返回。
    """
    out = HostOut.model_validate(host)
    out.status = effective_host_status(host, now=now)
    mount_count, mount_failed_count, mount_anomaly = summarize_host_mounts(out.mount_status)
    out.mount_count = mount_count
    out.mount_failed_count = mount_failed_count
    out.mount_anomaly = mount_anomaly
    if projection == "list":
        out.mount_status = {}
    extra = sanitize_host_extra(host.extra)
    out.extra = extra
    out.capacity = extra.get("capacity")
    out.health = extra.get("health")
    out.health_probe = extra.get("health_probe") if isinstance(extra.get("health_probe"), dict) else None
    out.self_heal = extra.get("self_heal") if isinstance(extra.get("self_heal"), dict) else None
    out.host_key_trust = host_key_trust
    if ledger_device_count is not None:
        out.ledger_device_count = int(ledger_device_count)
    if ledger_latest_seen_at is not None:
        out.ledger_latest_seen_at = ledger_latest_seen_at
    installed, installed_at = derive_agent_installed(host)
    out.agent_installed = installed
    out.agent_installed_at = installed_at
    version_view = build_host_version_view(
        extra,
        agent_artifact_digest=getattr(host, "agent_artifact_digest", None),
        desired_artifact_digest=desired_artifact_digest,
        expected_code_revision=expected_code_revision,
    )
    out.agent_protocol_version = version_view["agent_protocol_version"]
    out.agent_code_revision = version_view["agent_code_revision"]
    out.expected_code_revision = version_view["expected_code_revision"]
    out.agent_code_deployed = version_view["agent_code_deployed"]
    out.agent_code_deployed_at = version_view["agent_code_deployed_at"]
    out.agent_code_sync_status = version_view["agent_code_sync_status"]
    return out


def _cap_int(mapping: Any, key: str) -> int:
    if not isinstance(mapping, dict):
        return 0
    raw = mapping.get(key)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return max(0, int(raw))
    return 0


def _powered_off_ports_from_probe(out: HostOut) -> List[str]:
    probe = out.health_probe if isinstance(out.health_probe, dict) else {}
    raw = probe.get("powered_off_ports")
    if not isinstance(raw, list):
        return []
    return [str(p).strip() for p in raw if isinstance(p, str) and p.strip()]


def _has_recent_ledger_activity(
    out: HostOut,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """判断台账在册设备是否存在近期活跃（72h 内）。

    - ``ledger_device_count == 0``：明确无在册设备 → ``False``；
    - ``ledger_device_count is None`` 或 ``ledger_latest_seen_at is None``：
      未下发时间摘要时保守返回 ``True``（兼容旧调用方）；
    - 其余按 ``now - ledger_latest_seen_at <= 72h`` 判定。
    """
    if out.ledger_device_count == 0:
        return False
    if out.ledger_device_count is None or out.ledger_latest_seen_at is None:
        return True
    ref_now = now or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)
    seen = out.ledger_latest_seen_at
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return (ref_now - seen) <= timedelta(hours=EMPTY_TREE_RECENT_LEDGER_HOURS)


def host_usb_fault_priority(
    out: HostOut,
    *,
    now: Optional[datetime] = None,
) -> int:
    """计算主机 USB/Hub/探针故障优先级（3=严重需人工 2=劣化/未豁免空树 1=待办/VBUS 断电/闲置半枚举 0=正常）。

    与前端 ``ExpandableHostTable.tsx::hostUsbFaultPriority`` 严格保持同口径：
    - 退役主机恒为 0；
    - 硬件硬证据（主控死亡、PCI xHCI 失绑、根口禁用、过流、在用总线 Hub 半枚举、
      Hub 口禁用、端口卡死 ``default``）与 ``uhubctl`` VBUS 断电端口不受 ``emptied_at``
      或 72h 窗口豁免；
    - 纯 ``usb_tree_empty`` 仅在未置位 ``emptied_at``、台账有设备且最近活跃时间
      在 72 小时内时计入 P2（超过 72h 且硬件全绿自动归为长期空置，避免空柜淹没真实故障）。
    """
    if out.retired_at is not None:
        return 0
    health = out.health if isinstance(out.health, dict) else {}
    raw_reasons = health.get("reasons")
    reasons = {str(r) for r in raw_reasons} if isinstance(raw_reasons, list) else set()

    cap = out.capacity if isinstance(out.capacity, dict) else {}
    topo = cap.get("usb_hub_topology") if isinstance(cap.get("usb_hub_topology"), dict) else None
    unbound_hubs = _cap_int(topo, "unbound_hubs")
    unbound_idle = _cap_int(topo, "unbound_idle_bus_hubs")
    active_unbound_hubs = max(0, unbound_hubs - unbound_idle)

    ctrl = (
        cap.get("usb_controller_health")
        if isinstance(cap.get("usb_controller_health"), dict)
        else None
    )
    unbound_xhci = _cap_int(ctrl, "unbound_xhci_controllers")
    disabled_root_ports = _cap_int(ctrl, "disabled_root_ports")
    disabled_hub_ports = _cap_int(ctrl, "disabled_hub_ports")
    over_current_ports = _cap_int(ctrl, "over_current_ports")
    stuck_default_ports = _cap_int(ctrl, "stuck_default_ports")

    probe = out.health_probe if isinstance(out.health_probe, dict) else {}
    self_heal = out.self_heal if isinstance(out.self_heal, dict) else {}
    powered_off_count = len(_powered_off_ports_from_probe(out))

    if (
        "usb_host_controller_dead" in reasons
        or probe.get("strike_open") is True
        or self_heal.get("fuse_open") is True
        or active_unbound_hubs > 0
        or unbound_xhci > 0
        or disabled_root_ports > 0
        or over_current_ports > 0
    ):
        return 3
    if (
        "usb_link_degraded" in reasons
        or (
            "usb_tree_empty" in reasons
            and out.emptied_at is None
            and _has_recent_ledger_activity(out, now=now)
        )
        or "adb_multiple_servers" in reasons
        or disabled_hub_ports > 0
    ):
        return 2
    if (
        "adb_interfaces_missing" in reasons
        or unbound_hubs > 0
        or stuck_default_ports > 0
        or powered_off_count > 0
    ):
        return 1
    return 0


def host_has_mount_anomaly(out: HostOut) -> bool:
    """挂载异常判据：至少一条挂载项未挂载；空挂载字典绝不计为故障。"""
    if out.mount_anomaly:
        return True
    _, _, has_anomaly = summarize_host_mounts(out.mount_status)
    return has_anomaly


def host_effective_health_status(out: HostOut) -> str:
    health = out.health if isinstance(out.health, dict) else {}
    raw = health.get("status")
    if isinstance(raw, str) and raw in ("HEALTHY", "DEGRADED", "UNSCHEDULABLE"):
        return raw
    return "HEALTHY"


def host_is_trackable_agent(out: HostOut) -> bool:
    """Agent 同步统计分母口径：``ONLINE && agent_installed``。"""
    return out.status == "ONLINE" and bool(out.agent_installed)


def host_is_upgrade_pending(out: HostOut) -> bool:
    """升级待办口径（计数与快捷点击严格同源）：``ONLINE && agent_installed && drift``。"""
    return host_is_trackable_agent(out) and out.agent_code_sync_status == "drift"


def _normalize_optional_enum(
    value: Optional[str],
    *,
    field_name: str,
    allowed: frozenset[str],
    case_mode: str = "preserve",
) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped.lower() == "all":
        return None
    if case_mode == "upper":
        candidate = stripped.upper()
    elif case_mode == "lower":
        candidate = stripped.lower()
    else:
        candidate = stripped
    if candidate not in allowed:
        raise HostQueryError(
            status_code=422,
            detail={
                "code": f"INVALID_{field_name.upper()}",
                "message": f"{field_name} must be one of {sorted(allowed)}",
                "allowed": sorted(allowed),
            },
        )
    return candidate


def normalize_host_filters(
    *,
    include_retired: bool = False,
    status: Optional[str] = None,
    health_status: Optional[str] = None,
    sync_status: Optional[str] = None,
    usb_anomaly: Optional[bool] = None,
    mount_anomaly: Optional[bool] = None,
    q: Optional[str] = None,
) -> HostQueryFilters:
    norm_status = _normalize_optional_enum(
        status, field_name="status", allowed=ALLOWED_STATUS_FILTERS, case_mode="upper",
    )
    norm_health: Optional[str] = None
    if health_status is not None and health_status.strip() and health_status.strip().lower() != "all":
        raw_h = health_status.strip()
        cand_h = "unhealthy" if raw_h.lower() == "unhealthy" else raw_h.upper()
        if cand_h not in ALLOWED_HEALTH_FILTERS:
            raise HostQueryError(
                status_code=422,
                detail={
                    "code": "INVALID_HEALTH_STATUS",
                    "message": f"health_status must be one of {sorted(ALLOWED_HEALTH_FILTERS)}",
                    "allowed": sorted(ALLOWED_HEALTH_FILTERS),
                },
            )
        norm_health = cand_h
    norm_sync = _normalize_optional_enum(
        sync_status, field_name="sync_status", allowed=ALLOWED_SYNC_FILTERS, case_mode="lower",
    )
    norm_q = q.strip() if isinstance(q, str) and q.strip() else None
    return HostQueryFilters(
        include_retired=include_retired,
        status=norm_status,
        health_status=norm_health,
        sync_status=norm_sync,
        usb_anomaly=usb_anomaly,
        mount_anomaly=mount_anomaly,
        q=norm_q,
    )


def validate_host_sort_params(
    sort_by: Optional[str],
    sort_dir: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    norm_by = _normalize_optional_enum(
        sort_by, field_name="sort_by", allowed=ALLOWED_SORT_BY, case_mode="lower",
    )
    norm_dir = _normalize_optional_enum(
        sort_dir, field_name="sort_dir", allowed=ALLOWED_SORT_DIR, case_mode="lower",
    )
    return norm_by, norm_dir


def _matches_host_filters(
    out: HostOut,
    filters: HostQueryFilters,
    *,
    now: Optional[datetime] = None,
) -> bool:
    if filters.status is not None and out.status != filters.status:
        return False

    if filters.health_status is not None:
        eff_health = host_effective_health_status(out)
        if filters.health_status == "unhealthy":
            if eff_health not in ("DEGRADED", "UNSCHEDULABLE"):
                return False
        elif eff_health != filters.health_status:
            return False

    if filters.sync_status is not None:
        if filters.sync_status == "upgrade_pending":
            if not host_is_upgrade_pending(out):
                return False
        elif out.agent_code_sync_status != filters.sync_status:
            return False

    if filters.usb_anomaly is True and host_usb_fault_priority(out, now=now) == 0:
        return False
    if filters.usb_anomaly is False and host_usb_fault_priority(out, now=now) > 0:
        return False

    if filters.mount_anomaly is True and not host_has_mount_anomaly(out):
        return False
    if filters.mount_anomaly is False and host_has_mount_anomaly(out):
        return False

    if filters.q:
        tokens = [tok for tok in filters.q.lower().split() if tok]
        if tokens:
            health = out.health if isinstance(out.health, dict) else {}
            raw_reasons = health.get("reasons")
            reason_parts = []
            if isinstance(raw_reasons, list):
                for reason in raw_reasons:
                    code = str(reason)
                    label = HEALTH_REASON_LABELS.get(code, "")
                    reason_parts.append(f"{code} {label}")
            powered_off = _powered_off_ports_from_probe(out)
            if powered_off:
                reason_parts.append("vbus 断电 uhubctl " + " ".join(powered_off))
            haystack = " ".join(
                str(part)
                for part in (
                    out.id,
                    out.name,
                    out.ip,
                    out.agent_code_revision,
                    out.agent_code_deployed,
                    out.agent_protocol_version,
                    out.status,
                    " ".join(reason_parts),
                )
                if part
            ).lower()
            if not all(token in haystack for token in tokens):
                return False

    return True


def _metric_number(value: Any, *, fallback: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return fallback


def _host_online_devices(out: HostOut) -> int:
    if out.status != "ONLINE":
        return 0
    cap = out.capacity if isinstance(out.capacity, dict) else {}
    raw = cap.get("online_healthy_devices")
    return int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0


def _host_disk_usage(out: HostOut) -> float:
    if out.status != "ONLINE":
        return -1.0
    extra = out.extra if isinstance(out.extra, dict) else {}
    disk = extra.get("disk_usage")
    if isinstance(disk, dict):
        return _metric_number(disk.get("usage_percent"), fallback=-1.0)
    return -1.0


def _host_heartbeat_epoch(out: HostOut) -> float:
    if out.last_heartbeat is None:
        return 0.0
    dt = out.last_heartbeat
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def sort_host_outs(
    items: List[HostOut],
    *,
    sort_by: Optional[str],
    sort_dir: Optional[str],
    now: Optional[datetime] = None,
) -> List[HostOut]:
    """在分页切片前执行全局稳定排序，统一以 ``Host.id ASC`` 为尾键。"""
    # 兼容旧路径：未传 sort_by 时维持既有 Host.id ASC 顺序
    if sort_by is None or sort_by == "id":
        reverse = sort_dir == "desc"
        return sorted(items, key=lambda h: str(h.id), reverse=reverse)

    if sort_by in ("default", "priority"):
        # 严重 USB/Hub/探针异常优先置顶，同优先级按 Host.id ASC 稳定排序
        if sort_dir == "asc":
            return sorted(items, key=lambda h: (host_usb_fault_priority(h, now=now), str(h.id)))
        return sorted(items, key=lambda h: (-host_usb_fault_priority(h, now=now), str(h.id)))

    # 显式列排序：先按 Host.id ASC 预排，再利用 Python Timsort 稳定性按目标列排序，
    # 保证无论 asc/desc，同值行始终按 Host.id ASC 确定输出。
    stable = sorted(items, key=lambda h: str(h.id))
    default_desc = sort_by != "name"
    reverse = (sort_dir == "desc") if sort_dir is not None else default_desc

    key_extractors: Dict[str, Callable[[HostOut], Any]] = {
        "name": lambda h: (h.name or h.ip or str(h.id)).lower(),
        "devices": _host_online_devices,
        "cpu": lambda h: (
            _metric_number(h.extra.get("cpu_load"), fallback=-1.0)
            if h.status == "ONLINE"
            else -1.0
        ),
        "ram": lambda h: (
            _metric_number(h.extra.get("ram_usage"), fallback=-1.0)
            if h.status == "ONLINE"
            else -1.0
        ),
        "disk": _host_disk_usage,
        "heartbeat": _host_heartbeat_epoch,
    }
    extractor = key_extractors[sort_by]
    return sorted(stable, key=extractor, reverse=reverse)


def _load_base_host_outs(
    db: Session,
    *,
    include_retired: bool,
    desired_artifact_digest: Optional[str],
    now: datetime,
    include_expected_revision: bool = True,
    projection: str = "full",
) -> List[HostOut]:
    query = db.query(Host)
    if not include_retired:
        query = query.filter(Host.retired_at.is_(None))
    hosts = query.order_by(Host.id).all()
    # 单请求外层只求一次 expected_code_revision（facets/preview 不消费此溯源字段则跳过），
    # 避免按 N 台主机循环重复调用 get_agent_code_version()。
    expected_rev = get_agent_code_version() if include_expected_revision else ""
    ledger_summary = load_host_ledger_summary(
        db,
        [str(h.id) for h in hosts],
    )
    return [
        build_host_out_readonly(
            h,
            desired_artifact_digest=desired_artifact_digest,
            expected_code_revision=expected_rev,
            now=now,
            ledger_device_count=ledger_summary.get(str(h.id), (0, None))[0],
            ledger_latest_seen_at=ledger_summary.get(str(h.id), (0, None))[1],
            projection=projection,
        )
        for h in hosts
    ]


def _hydrate_hosts_by_ids(
    db: Session,
    ordered_ids: Sequence[str],
    *,
    desired_artifact_digest: Optional[str],
    now: datetime,
    projection: str = "full",
) -> List[HostOut]:
    """按快照冻结顺序加载目标页主机的最新实时字段（不重排跨页归属）。"""
    if not ordered_ids:
        return []
    rows = db.query(Host).filter(Host.id.in_(list(ordered_ids))).all()
    by_id = {str(h.id): h for h in rows}
    expected_rev = get_agent_code_version()
    ledger_summary = load_host_ledger_summary(db, list(by_id.keys()))
    result: List[HostOut] = []
    for hid in ordered_ids:
        host = by_id.get(str(hid))
        if host is None:
            continue
        result.append(
            build_host_out_readonly(
                host,
                desired_artifact_digest=desired_artifact_digest,
                expected_code_revision=expected_rev,
                now=now,
                ledger_device_count=ledger_summary.get(str(host.id), (0, None))[0],
                ledger_latest_seen_at=ledger_summary.get(str(host.id), (0, None))[1],
                projection=projection,
            )
        )
    return result


def query_hosts_page(
    db: Session,
    filters: HostQueryFilters,
    *,
    skip: int,
    limit: int,
    sort_by: Optional[str],
    sort_dir: Optional[str],
    desired_artifact_digest: Optional[str],
    now: Optional[datetime] = None,
    projection: str = "full",
    snapshot_id: Optional[str] = None,
) -> Tuple[List[HostOut], int, str]:
    """执行只读筛选、全局排序并返回 ``(page_items, filtered_total, snapshot_id)``。

    携带有效 ``snapshot_id`` 翻页时复用同一代次的冻结 ID 顺序切片，再回填当页最新实时指标，
    消除动态列（cpu / ram / disk / heartbeat / devices / priority）翻页间隙的重复与漏项。
    """
    ref_now = now or datetime.now(timezone.utc)
    scope_sig = (filters, sort_by, sort_dir)

    if snapshot_id:
        frozen_ids = _HOST_SORT_SNAPSHOTS.get(snapshot_id, scope_sig)
        if frozen_ids is not None:
            page_ids = frozen_ids[skip : skip + limit]
            page_items = _hydrate_hosts_by_ids(
                db,
                page_ids,
                desired_artifact_digest=desired_artifact_digest,
                now=ref_now,
                projection=projection,
            )
            return page_items, len(frozen_ids), snapshot_id

    all_outs = _load_base_host_outs(
        db,
        include_retired=filters.include_retired,
        desired_artifact_digest=desired_artifact_digest,
        now=ref_now,
        projection=projection,
    )
    matched = [h for h in all_outs if _matches_host_filters(h, filters, now=ref_now)]
    ordered = sort_host_outs(matched, sort_by=sort_by, sort_dir=sort_dir, now=ref_now)
    total = len(ordered)
    new_snapshot_id = _HOST_SORT_SNAPSHOTS.put(scope_sig, [str(h.id) for h in ordered])
    return ordered[skip : skip + limit], total, new_snapshot_id


def build_host_facets(
    db: Session,
    *,
    include_retired: bool = False,
    desired_artifact_digest: Optional[str] = None,
    now: Optional[datetime] = None,
) -> HostFacetsOut:
    """按 ``include_retired`` 基础全集计算 10 项 Agent host 聚合计数（不受搜索与翻页影响）。"""
    ref_now = now or datetime.now(timezone.utc)
    hosts = _load_base_host_outs(
        db,
        include_retired=include_retired,
        desired_artifact_digest=desired_artifact_digest,
        now=ref_now,
        include_expected_revision=False,
    )
    return _aggregate_host_facets(hosts, now=ref_now)


def _aggregate_host_facets(
    hosts: Sequence[HostOut],
    *,
    now: Optional[datetime] = None,
) -> HostFacetsOut:
    online = 0
    degraded = 0
    offline = 0
    usb_anomaly = 0
    upgrade_pending = 0
    health_anomaly = 0
    mount_anomaly = 0
    aligned_agent_count = 0
    trackable_agent_count = 0

    for h in hosts:
        if h.status == "ONLINE":
            online += 1
        elif h.status == "DEGRADED":
            degraded += 1
        else:
            offline += 1

        if host_usb_fault_priority(h, now=now) > 0:
            usb_anomaly += 1

        if host_is_trackable_agent(h):
            trackable_agent_count += 1
            if h.agent_code_sync_status == "matched":
                aligned_agent_count += 1
            elif h.agent_code_sync_status == "drift":
                upgrade_pending += 1

        if host_effective_health_status(h) in ("DEGRADED", "UNSCHEDULABLE"):
            health_anomaly += 1

        if host_has_mount_anomaly(h):
            mount_anomaly += 1

    return HostFacetsOut(
        all=len(hosts),
        online=online,
        degraded=degraded,
        offline=offline,
        usb_anomaly=usb_anomaly,
        upgrade_pending=upgrade_pending,
        health_anomaly=health_anomaly,
        mount_anomaly=mount_anomaly,
        aligned_agent_count=aligned_agent_count,
        trackable_agent_count=trackable_agent_count,
    )


def build_host_selection_preview(
    db: Session,
    filters: HostQueryFilters,
    *,
    desired_artifact_digest: Optional[str] = None,
    now: Optional[datetime] = None,
) -> HostSelectionPreviewOut:
    """跨页主机批量选择预览（只读）：冻结匹配 host_id 并统计可安装/可热更新/退役摘要。"""
    ref_now = now or datetime.now(timezone.utc)
    all_outs = _load_base_host_outs(
        db,
        include_retired=filters.include_retired,
        desired_artifact_digest=desired_artifact_digest,
        now=ref_now,
        include_expected_revision=False,
    )
    matched = [h for h in all_outs if _matches_host_filters(h, filters, now=ref_now)]
    ordered = sort_host_outs(matched, sort_by="id", sort_dir="asc", now=ref_now)

    retired_count = 0
    online_count = 0
    offline_count = 0
    degraded_count = 0
    first_install_count = 0
    reinstall_count = 0
    hot_update_count = 0
    flash_prereqs_count = 0

    for h in ordered:
        if h.status == "ONLINE":
            online_count += 1
        elif h.status == "DEGRADED":
            degraded_count += 1
        else:
            offline_count += 1

        if h.retired_at is not None:
            retired_count += 1
            continue

        if h.status == "ONLINE":
            hot_update_count += 1
        elif h.agent_installed:
            reinstall_count += 1
        else:
            first_install_count += 1

        if h.agent_installed:
            flash_prereqs_count += 1

    return HostSelectionPreviewOut(
        total_matched=len(ordered),
        host_ids=[str(h.id) for h in ordered],
        online_count=online_count,
        offline_count=offline_count,
        degraded_count=degraded_count,
        retired_count=retired_count,
        first_install_count=first_install_count,
        reinstall_count=reinstall_count,
        hot_update_count=hot_update_count,
        flash_prereqs_count=flash_prereqs_count,
    )
