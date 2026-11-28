"""PlanRun 专项离线容忍策略与 Minimap 异常离线判定。

区分两类稳定性专项：
- **离线是测试过程一部分（``offline_expected=True``）**：
  - 开关机专项（``power-cycle`` / ``powercycle_*``）：设备反复重启；
  - Aging DDR 专项（``aging_ddr_*``）：展锐 DST 阶段处于无 USB 枚举的底层测试态；
  - 刷机专项（``flash_*`` / ``vbus_flash*``）：下载口/重启期间无 ADB。
  此类专项中，设备 ADB 离线属于正常测试过程，Minimap 不应将其标红为 fail。
- **长时间 ADB 离线即异常（``offline_expected=False``）**：
  - 休眠唤醒（``sleep``）、Monkey（``monkey``）、MTBF（``mtbf``）、GPU（``gpu``）等。
  此类专项若设备在 Host 在线时持续 ADB 离线达到阈值（默认 1800s / 30 分钟，
  优先读取快照 patrol 步骤中的 ``max_offline_seconds``），Minimap 应立即标红提示
  异常离线（fail），覆盖已在跑的存量 PlanRun（如锁定旧版巡检脚本的 7 天长稳任务）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Tuple

from backend.models.enums import JobStatus
from backend.services.plan_run_read_common import aware

DEFAULT_ABNORMAL_OFFLINE_SECONDS = 1800

_OFFLINE_EXPECTED_SPECIALTIES = frozenset({"power-cycle"})
_OFFLINE_EXPECTED_SCRIPTS = frozenset({
    "powercycle_setup",
    "powercycle_check",
    "powercycle_adb_patrol",
    "aging_ddr_setup",
    "aging_ddr_check",
})
_OFFLINE_EXPECTED_SCRIPT_PREFIXES = ("flash_", "vbus_flash")
_ADB_OFFLINE_LINK_STATUSES = frozenset({"offline", "adb_error"})


def _extract_step_script_name(step: Any) -> str:
    if not isinstance(step, dict):
        return ""
    name = step.get("script_name")
    if isinstance(name, str) and name.strip():
        return name.strip().lower()
    action = step.get("action")
    if isinstance(action, str) and action.startswith("script:"):
        return action.split(":", 1)[1].strip().lower()
    return ""


def _iter_snapshot_steps(plan_snapshot: Any) -> list[dict[str, Any]]:
    if not isinstance(plan_snapshot, dict):
        return []
    steps: list[dict[str, Any]] = []
    raw_steps = plan_snapshot.get("steps")
    if isinstance(raw_steps, list):
        steps.extend(s for s in raw_steps if isinstance(s, dict))
    # 兼容直接含 lifecycle.init / patrol.steps 的快照形态
    lifecycle = plan_snapshot.get("lifecycle")
    if isinstance(lifecycle, dict):
        for phase_key in ("init", "teardown"):
            phase_steps = lifecycle.get(phase_key)
            if isinstance(phase_steps, list):
                steps.extend(s for s in phase_steps if isinstance(s, dict))
        patrol = lifecycle.get("patrol")
        if isinstance(patrol, dict) and isinstance(patrol.get("steps"), list):
            steps.extend(s for s in patrol["steps"] if isinstance(s, dict))
    return steps


def resolve_run_offline_policy(
    plan_snapshot: Any,
    specialty_key: Optional[str] = None,
) -> Tuple[bool, int]:
    """根据 PlanRun 快照与专项键解析 ``(offline_expected, threshold_seconds)``。"""
    spec = (specialty_key or "").strip().lower()
    if not spec and isinstance(plan_snapshot, dict):
        plan_meta = plan_snapshot.get("plan")
        if isinstance(plan_meta, dict):
            spec = str(plan_meta.get("specialty_key") or "").strip().lower()
        if not spec:
            spec = str(plan_snapshot.get("specialty_key") or "").strip().lower()

    if spec in _OFFLINE_EXPECTED_SPECIALTIES:
        return True, 0

    steps = _iter_snapshot_steps(plan_snapshot)
    explicit_threshold: Optional[int] = None
    for st in steps:
        sname = _extract_step_script_name(st)
        if not sname:
            continue
        if sname in _OFFLINE_EXPECTED_SCRIPTS or sname.startswith(_OFFLINE_EXPECTED_SCRIPT_PREFIXES):
            return True, 0
        stage = str(st.get("stage") or "").strip().lower()
        params = st.get("params")
        if isinstance(params, dict) and "max_offline_seconds" in params:
            if stage in ("", "patrol"):
                try:
                    explicit_threshold = max(0, int(str(params["max_offline_seconds"]).strip()))
                except (TypeError, ValueError):
                    pass

    threshold = DEFAULT_ABNORMAL_OFFLINE_SECONDS if explicit_threshold is None else explicit_threshold
    return False, threshold


def is_prolonged_abnormal_offline(
    *,
    job_status: str,
    link_status: str,
    device_last_seen: Optional[datetime],
    now: datetime,
    offline_expected: bool,
    threshold_seconds: int = DEFAULT_ABNORMAL_OFFLINE_SECONDS,
) -> bool:
    """判断单个在跑 Job 是否命中「非预期长时间 ADB 离线异常」。

    仅当：
    1. 当前专项不以离线为正常过程（``not offline_expected``）且阈值 > 0；
    2. Job 仍处于 ``RUNNING``；
    3. 设备链路为 ``offline`` 或 ``adb_error``（Host 离线归宿主故障，不混为设备 fail）；
    4. ``device_last_seen`` 非空且距 ``now`` 已达到 ``threshold_seconds``。
    """
    if offline_expected or threshold_seconds <= 0:
        return False
    if job_status != JobStatus.RUNNING.value:
        return False
    if link_status not in _ADB_OFFLINE_LINK_STATUSES:
        return False
    last_seen = aware(device_last_seen)
    if last_seen is None:
        return False
    elapsed = (aware(now) - last_seen).total_seconds()
    return elapsed >= threshold_seconds
