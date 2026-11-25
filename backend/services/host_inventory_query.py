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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

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


def load_host_ledger_device_counts(
    db: Session,
    host_ids: Optional[Sequence[str]] = None,
) -> Dict[str, int]:
    """只读统计各主机在册（未退役）设备台账行数。"""
    query = (
        db.query(Device.host_id, func.count(Device.id))
        .filter(Device.retired_at.is_(None), Device.host_id.is_not(None))
    )
    if host_ids is not None:
        id_list = [str(hid) for hid in host_ids]
        if not id_list:
            return {}
        query = query.filter(Device.host_id.in_(id_list))
    rows = query.group_by(Device.host_id).all()
    return {str(hid): int(cnt) for hid, cnt in rows if hid is not None}


def build_host_out_readonly(
    host: Host,
    *,
    desired_artifact_digest: Optional[str] = None,
    expected_code_revision: Optional[str] = None,
    now: Optional[datetime] = None,
    host_key_trust: Optional[str] = None,
    ledger_device_count: Optional[int] = None,
) -> HostOut:
    """只读构造 ``HostOut``（不含 DB 查询；不修改 ORM 对象）。"""
    out = HostOut.model_validate(host)
    out.status = effective_host_status(host, now=now)
    extra = sanitize_host_extra(host.extra)
    out.extra = extra
    out.capacity = extra.get("capacity")
    out.health = extra.get("health")
    out.health_probe = extra.get("health_probe") if isinstance(extra.get("health_probe"), dict) else None
    out.self_heal = extra.get("self_heal") if isinstance(extra.get("self_heal"), dict) else None
    out.host_key_trust = host_key_trust
    if ledger_device_count is not None:
        out.ledger_device_count = int(ledger_device_count)
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


def host_usb_fault_priority(out: HostOut) -> int:
    """计算主机 USB/Hub/探针故障优先级（3=严重需人工 2=劣化/未豁免空树 1=待办/闲置半枚举 0=正常）。

    与前端 ``ExpandableHostTable.tsx::hostUsbFaultPriority`` 严格保持同口径：
    - 退役主机恒为 0；
    - 硬件硬证据（主控死亡、PCI xHCI 失绑、根口禁用、过流、在用总线 Hub 半枚举、
      Hub 口禁用、端口卡死 ``default``）不受 ``emptied_at`` 或空台账豁免；
    - 纯 ``usb_tree_empty`` 仅在未置位 ``emptied_at`` 且台账仍有设备
      （``ledger_device_count != 0``）时计入 P2（对齐 ``StabilityHostUsbBlind``，
      避免无设备空柜淹没真实故障）。
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
            and out.ledger_device_count != 0
        )
        or "adb_multiple_servers" in reasons
        or disabled_hub_ports > 0
    ):
        return 2
    if (
        "adb_interfaces_missing" in reasons
        or unbound_hubs > 0
        or stuck_default_ports > 0
    ):
        return 1
    return 0


def host_has_mount_anomaly(out: HostOut) -> bool:
    """挂载异常判据：至少一条挂载项未挂载；空挂载字典绝不计为故障。"""
    mounts = out.mount_status
    if not isinstance(mounts, dict) or not mounts:
        return False
    for info in mounts.values():
        mounted = info is True or (isinstance(info, dict) and info.get("ok") is True)
        if not mounted:
            return True
    return False


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


def _matches_host_filters(out: HostOut, filters: HostQueryFilters) -> bool:
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

    if filters.usb_anomaly is True and host_usb_fault_priority(out) == 0:
        return False
    if filters.usb_anomaly is False and host_usb_fault_priority(out) > 0:
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
) -> List[HostOut]:
    """在分页切片前执行全局稳定排序，统一以 ``Host.id ASC`` 为尾键。"""
    # 兼容旧路径：未传 sort_by 时维持既有 Host.id ASC 顺序
    if sort_by is None or sort_by == "id":
        reverse = sort_dir == "desc"
        return sorted(items, key=lambda h: str(h.id), reverse=reverse)

    if sort_by in ("default", "priority"):
        # 严重 USB/Hub/探针异常优先置顶，同优先级按 Host.id ASC 稳定排序
        if sort_dir == "asc":
            return sorted(items, key=lambda h: (host_usb_fault_priority(h), str(h.id)))
        return sorted(items, key=lambda h: (-host_usb_fault_priority(h), str(h.id)))

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
) -> List[HostOut]:
    query = db.query(Host)
    if not include_retired:
        query = query.filter(Host.retired_at.is_(None))
    hosts = query.order_by(Host.id).all()
    # 单请求外层只求一次 expected_code_revision（facets/preview 不消费此溯源字段则跳过），
    # 避免按 N 台主机循环重复调用 get_agent_code_version()。
    expected_rev = get_agent_code_version() if include_expected_revision else ""
    ledger_counts = load_host_ledger_device_counts(
        db,
        [str(h.id) for h in hosts],
    )
    return [
        build_host_out_readonly(
            h,
            desired_artifact_digest=desired_artifact_digest,
            expected_code_revision=expected_rev,
            now=now,
            ledger_device_count=ledger_counts.get(str(h.id), 0),
        )
        for h in hosts
    ]


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
) -> Tuple[List[HostOut], int]:
    """执行只读筛选、全局排序并返回 ``(page_items, filtered_total)``。"""
    ref_now = now or datetime.now(timezone.utc)
    all_outs = _load_base_host_outs(
        db,
        include_retired=filters.include_retired,
        desired_artifact_digest=desired_artifact_digest,
        now=ref_now,
    )
    matched = [h for h in all_outs if _matches_host_filters(h, filters)]
    ordered = sort_host_outs(matched, sort_by=sort_by, sort_dir=sort_dir)
    total = len(ordered)
    return ordered[skip : skip + limit], total


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
    return _aggregate_host_facets(hosts)


def _aggregate_host_facets(hosts: Sequence[HostOut]) -> HostFacetsOut:
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

        if host_usb_fault_priority(h) > 0:
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
    matched = [h for h in all_outs if _matches_host_filters(h, filters)]
    ordered = sort_host_outs(matched, sort_by="id", sort_dir="asc")

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
