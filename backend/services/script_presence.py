"""host × 脚本版本的在位矩阵：常设 sweep 与五态判定（#2958 第五道闸）。

问题（#2958）：run 作用域已有 host 侧核验（`admission_pump._verify_scripts_phase`：
`verify_scripts` RPC 比 sha256，失败走 SFTP 治愈），但**只在派发时覆盖「本 run 的
host × 本 run 快照」**，维护窗 / 近期无 run 的 host 无账——`.89` 缺 3 个版本目录而
DB 面全绿就是该盲区的实证。本模块把核验做成**常设账**：

- **目标集（全集）** = `plan_step.enabled` 引用 ∩ `script.is_active`（与运行快照
  `expected_scripts_for_run` 同口径，不另造第二口径）；
- **per-host 可达集** = 调度可达（`task_schedules.device_ids` 的设备**当前**绑定的
  host）∪ 历史可达（近 `days` 天 `plan_run_host`）——「全集有、可达集无」判 ``n_a``，
  不判红（实测每族实跑 host 数 1–46 不均，保守 51×48 会给假缺口）；
- **核验**：复用既有 `verify_scripts` RPC（`precheck/verify.gather_verify`，10s 超时），
  只读、不占维护窗；
- **落库**：`host_script_presence` 每轮全量 upsert（`checked_at`/`sweep_id` 刷新），
  读取面 = 指标（抓取期现算）+ 只读 API。

`state` 是**闭词表**（`PRESENCE_STATES`）：``present / missing / mismatch / unknown /
n_a / maintenance``。两条刻意的口径：

- ``unknown``（agent 不可达）**不是绿**——与 `agent_offline` 语义一致；
- 维护窗内 host 的缺口记 ``maintenance`` 而非 missing/mismatch：维护窗兼作升级锁，
  窗口内不收作业、无即时影响（归队前补分发由 #2865 遗留项盯），否则维护期恒红。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.core.database import SessionLocal
from backend.models.host import Device, Host
from backend.models.plan import PlanStep
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.models.schedule import TaskSchedule
from backend.models.script import Script
from backend.models.script_presence import HostScriptPresence
from backend.services.host_maintenance import in_maintenance_window

logger = logging.getLogger(__name__)

#: 五态 + 维护态（闭词表；指标/守卫按此枚举，新增值必须同步 metrics 与守卫测试）
STATE_PRESENT = "present"
STATE_MISSING = "missing"
STATE_MISMATCH = "mismatch"
STATE_UNKNOWN = "unknown"
STATE_N_A = "n_a"
STATE_MAINTENANCE = "maintenance"
PRESENCE_STATES: tuple[str, ...] = (
    STATE_PRESENT,
    STATE_MISSING,
    STATE_MISMATCH,
    STATE_UNKNOWN,
    STATE_N_A,
    STATE_MAINTENANCE,
)

#: 判定为「缺口」的态（告警口径：gap = missing + mismatch）
GAP_STATES: tuple[str, ...] = (STATE_MISSING, STATE_MISMATCH)

#: 历史可达窗口默认天数（与 CLI 脚本一致）
DEFAULT_HISTORY_DAYS = 30


# ── 纯函数层（CLI 脚本与 sweep 共用同一实现）───────────────────────────────

def build_full_target_set(
    step_rows: list[dict], script_rows: list[dict]
) -> list[tuple[str, str]]:
    """全集 = 启用步骤引用的版本 ∩ script 表 active 行（与运行快照同口径）。"""
    referenced = {
        (str(r["script_name"]), str(r["script_version"]))
        for r in step_rows
    }
    active = {(str(r["name"]), str(r["version"])) for r in script_rows if r.get("is_active")}
    return sorted(referenced & active)


def build_expected_manifests(
    script_rows: list[dict], full: Iterable[tuple[str, str]]
) -> list[dict]:
    """verify_scripts 载荷（`{name, version, nfs_path, sha256, support_files}`）。

    与 `precheck.scripts.expected_scripts_for_run` 同字段形态：agent 侧
    `script_verifier` 按 `nfs_path` 逐文件 sha256，并对 `support_files` 逐个比对。
    """
    wanted = set(full)
    out: list[dict] = []
    for r in sorted(script_rows, key=lambda x: (str(x["name"]), str(x["version"]))):
        key = (str(r["name"]), str(r["version"]))
        if key not in wanted:
            continue
        entry = {
            "name": key[0],
            "version": key[1],
            "nfs_path": str(r.get("nfs_path") or ""),
            "sha256": str(r.get("content_sha256") or ""),
        }
        support = r.get("support_files_manifest") or {}
        if isinstance(support, dict) and support:
            entry["support_files"] = {str(k): str(v) for k, v in support.items()}
        out.append(entry)
    return out


def group_steps_by_plan(step_rows: list[dict]) -> dict[int, set[tuple[str, str]]]:
    """plan_id -> 该 plan 启用步骤的 (族, 版本) 集合。"""
    out: dict[int, set[tuple[str, str]]] = {}
    for r in step_rows:
        out.setdefault(int(r["plan_id"]), set()).add(
            (str(r["script_name"]), str(r["script_version"]))
        )
    return out


def parse_device_ids(raw: object) -> set[int]:
    """`task_schedules.device_ids` 兼容 list / JSON 字符串 / NULL；非法项忽略。"""
    if raw is None:
        return set()
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return set()
    if not isinstance(raw, (list, tuple)):
        return set()
    out: set[int] = set()
    for item in raw:
        try:
            out.add(int(item))
        except (TypeError, ValueError):
            continue
    return out


def scheduled_host_plans(
    schedule_rows: list[dict], device_rows: list[dict]
) -> dict[str, set[int]]:
    """调度可达：host_id -> {plan_id}（device_ids × 设备当前绑定）。"""
    device_to_host = {
        int(r["id"]): str(r["host_id"])
        for r in device_rows
        if r.get("host_id") is not None
    }
    out: dict[str, set[int]] = {}
    for r in schedule_rows:
        plan_id = int(r["plan_id"])
        for device_id in parse_device_ids(r.get("device_ids")):
            host_id = device_to_host.get(device_id)
            if host_id is not None:
                out.setdefault(host_id, set()).add(plan_id)
    return out


def observed_host_plans(run_rows: list[dict]) -> dict[str, set[int]]:
    """历史可达：host_id -> {plan_id}（近 N 天 plan_run_host 实测）。"""
    out: dict[str, set[int]] = {}
    for r in run_rows:
        if r.get("host_id") is None or r.get("plan_id") is None:
            continue
        out.setdefault(str(r["host_id"]), set()).add(int(r["plan_id"]))
    return out


def reachable_targets_for_host(
    host_id: str,
    *,
    full: Iterable[tuple[str, str]],
    by_plan: dict[int, set[tuple[str, str]]],
    scheduled: dict[str, set[int]],
    observed: dict[str, set[int]],
) -> set[tuple[str, str]]:
    """单台 host 的可达集（与全集求交：停用/未注册版本不进可达集）。"""
    plans = scheduled.get(host_id, set()) | observed.get(host_id, set())
    reachable: set[tuple[str, str]] = set()
    for plan_id in plans:
        reachable |= by_plan.get(plan_id, set())
    return reachable & set(full)


def compute_host_targets(
    host_rows: list[dict],
    *,
    full: list[tuple[str, str]],
    by_plan: dict[int, set[tuple[str, str]]],
    scheduled: dict[str, set[int]],
    observed: dict[str, set[int]],
) -> list[dict]:
    """逐 host 汇总：可达集 + n/a 差集 + 来源计数（保持入参顺序）。

    CLI 表格与 sweep 共用同一实现（避免两处各写一份可达性循环）。
    """
    full_set = set(full)
    out: list[dict] = []
    for h in host_rows:
        host_id = str(h["id"])
        sched = scheduled.get(host_id, set())
        obs = observed.get(host_id, set())
        reachable = reachable_targets_for_host(
            host_id, full=full_set, by_plan=by_plan, scheduled=scheduled, observed=observed,
        )
        out.append(
            {
                "host_id": host_id,
                "status": str(h.get("status") or ""),
                "scheduled_plans": len(sched),
                "observed_plans": len(obs),
                "reachable": sorted(reachable),
                "n_a": sorted(full_set - reachable),
            }
        )
    return out


def classify_host_presence(
    *,
    host_id: str,
    full: Iterable[tuple[str, str]],
    reachable: set[tuple[str, str]],
    in_maintenance: bool,
    verify_ok: bool,
    verify_entries: list[dict],
    verify_error: Optional[str],
) -> dict[tuple[str, str], tuple[str, str]]:
    """把「RPC 结果 + 可达性 + 维护窗」折成逐目标的 ``(state, detail)``。

    优先级：``n_a``（可达集外）> ``unknown``（RPC 失败）> ``maintenance``（窗口内缺口）
    > agent 报的 present/missing/mismatch。细节：

    - RPC 失败（agent 不可达）时**所有可达目标都记 unknown**，不写成 present——未知不是绿；
    - 维护窗只把**缺口**改记 maintenance；``present`` 保持 present（在位是事实）；
    - agent 结果里缺行（老 agent / 未上报）记 ``unknown`` + ``not_reported``，
      不猜 missing；
    - support 文件不符（agent 的 ``support_file_mismatch``）归 ``mismatch``。
    """
    by_key: dict[tuple[str, str], dict] = {
        (str(e.get("name")), str(e.get("version"))): e for e in (verify_entries or [])
    }
    out: dict[tuple[str, str], tuple[str, str]] = {}
    for key in sorted(set(full)):
        name, version = key
        if key not in reachable:
            out[key] = (STATE_N_A, "")
            continue
        if not verify_ok:
            out[key] = (STATE_UNKNOWN, (verify_error or "verify_failed")[:256])
            continue
        entry = by_key.get(key)
        if entry is None:
            state, detail = STATE_UNKNOWN, "not_reported"
        elif entry.get("ok"):
            state, detail = STATE_PRESENT, ""
        else:
            err = str(entry.get("error") or "")
            if err == "file_missing_or_unreadable" or not entry.get("exists"):
                state, detail = STATE_MISSING, err or "file_missing_or_unreadable"
            else:
                state, detail = STATE_MISMATCH, err or "sha_mismatch"
        if in_maintenance and state in GAP_STATES:
            detail = (detail + " " if detail else "") + "(maintenance)"
            state = STATE_MAINTENANCE
        out[key] = (state, detail[:256])
    return out


def summarize_states(rows: Iterable[dict]) -> dict[str, Any]:
    """按态汇总（供 API/指标共用；``hosts_with_gap`` 用 host 维度去重）。"""
    counts = {state: 0 for state in PRESENCE_STATES}
    gap_hosts: set[str] = set()
    checked: list[datetime] = []
    for r in rows:
        state = str(r["state"])
        counts[state] = counts.get(state, 0) + 1
        if state in GAP_STATES:
            gap_hosts.add(str(r["host_id"]))
        if r.get("checked_at"):
            checked.append(r["checked_at"])
    return {
        "counts": counts,
        "hosts_with_gap": len(gap_hosts),
        "checked_at_min": min(checked) if checked else None,
        "checked_at_max": max(checked) if checked else None,
    }


# ── DB 载入（sync；由 sweep 在线程里调用）─────────────────────────────────

def load_facts(db: Session, *, days: int) -> dict[str, list[dict]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))
    steps = [
        dict(r) for r in db.execute(
            select(PlanStep.plan_id, PlanStep.script_name, PlanStep.script_version)
            .where(PlanStep.enabled.is_(True))
        ).mappings()
    ]
    scripts = [
        dict(r) for r in db.execute(
            select(
                Script.name, Script.version, Script.is_active,
                Script.nfs_path, Script.content_sha256, Script.support_files_manifest,
            ).where(Script.is_active.is_(True))
        ).mappings()
    ]
    hosts = [
        dict(r) for r in db.execute(
            select(Host.id, Host.status, Host.maintenance_until)
            .where(Host.retired_at.is_(None))
            .order_by(Host.id)
        ).mappings()
    ]
    devices = [
        dict(r) for r in db.execute(
            select(Device.id, Device.host_id).where(Device.host_id.is_not(None))
        ).mappings()
    ]
    schedules = [
        dict(r) for r in db.execute(
            select(TaskSchedule.plan_id, TaskSchedule.device_ids)
        ).mappings()
    ]
    runs = [
        dict(r) for r in db.execute(
            select(PlanRun.plan_id, PlanRunHost.host_id)
            .join(PlanRunHost, PlanRunHost.plan_run_id == PlanRun.id)
            .where(PlanRun.started_at >= cutoff)
            .distinct()
        ).mappings()
    ]
    return {
        "steps": steps, "scripts": scripts, "hosts": hosts,
        "devices": devices, "schedules": schedules, "runs": runs,
    }


def _persist_rows(db: Session, rows: list[dict], *, chunk: int = 500) -> int:
    """整轮 upsert（唯一键 (host_id, name, version)），返回写入行数。"""
    if not rows:
        return 0
    written = 0
    for start in range(0, len(rows), chunk):
        batch = rows[start:start + chunk]
        stmt = pg_insert(HostScriptPresence).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["host_id", "name", "version"],
            set_={
                "state": stmt.excluded.state,
                "detail": stmt.excluded.detail,
                "checked_at": stmt.excluded.checked_at,
                "sweep_id": stmt.excluded.sweep_id,
            },
        )
        db.execute(stmt)
        written += len(batch)
    db.commit()
    return written


async def run_sweep(
    *,
    days: int = DEFAULT_HISTORY_DAYS,
    host_ids: Optional[list[str]] = None,
    db_factory=SessionLocal,
) -> dict[str, Any]:
    """执行一轮 sweep：算可达集 → verify_scripts RPC → 五态 → 整轮 upsert。

    返回汇总（写库行数、各态计数、缺口 host 数、轮次 id）。RPC 只对**可达集非空**
    的 host 发起（无目标的 host 只写 ``n_a`` 行，仍刷新新鲜度）。
    """
    from backend.services.precheck.verify import gather_verify

    sweep_id = _new_sweep_id()

    def _facts() -> dict:
        with db_factory() as db:
            return load_facts(db, days=days)

    facts = await asyncio.to_thread(_facts)
    full = build_full_target_set(facts["steps"], facts["scripts"])
    manifests = build_expected_manifests(facts["scripts"], full)
    by_plan = group_steps_by_plan(facts["steps"])
    scheduled = scheduled_host_plans(facts["schedules"], facts["devices"])
    observed = observed_host_plans(facts["runs"])

    hosts = facts["hosts"]
    if host_ids is not None:
        wanted = {str(h) for h in host_ids}
        hosts = [h for h in hosts if str(h["id"]) in wanted]

    per_host: list[tuple[dict, set[tuple[str, str]]]] = []
    for h in hosts:
        hid = str(h["id"])
        per_host.append((h, reachable_targets_for_host(
            hid, full=full, by_plan=by_plan, scheduled=scheduled, observed=observed,
        )))

    need_rpc = [str(h["id"]) for h, reachable in per_host if reachable]
    verify: dict[str, tuple[bool, list[dict], Optional[str]]] = {}
    if need_rpc and manifests:
        verify = await gather_verify(need_rpc, manifests)

    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for h, reachable in per_host:
        hid = str(h["id"])
        ok, entries, err = verify.get(hid, (True, [], None)) if reachable else (True, [], None)
        states = classify_host_presence(
            host_id=hid,
            full=full,
            reachable=reachable,
            in_maintenance=in_maintenance_window(h.get("maintenance_until"), now=now),
            verify_ok=ok,
            verify_entries=entries,
            verify_error=err,
        )
        for (name, version), (state, detail) in states.items():
            rows.append({
                "host_id": hid, "name": name, "version": version,
                "state": state, "detail": detail,
                "checked_at": now, "sweep_id": sweep_id,
            })

    written = await asyncio.to_thread(_persist, db_factory, rows)
    summary = summarize_states(rows)
    result = {
        "sweep_id": sweep_id,
        "hosts": len(hosts),
        "hosts_verified": len(need_rpc),
        "full_versions": len(full),
        "rows": written,
        **summary,
    }
    logger.info(
        "script_presence_sweep_done sweep=%s hosts=%d verified=%d rows=%d gaps=%d unknown=%d",
        sweep_id, len(hosts), len(need_rpc), written,
        summary["counts"].get(STATE_MISSING, 0) + summary["counts"].get(STATE_MISMATCH, 0),
        summary["counts"].get(STATE_UNKNOWN, 0),
    )
    return result


def _persist(db_factory, rows: list[dict]) -> int:
    with db_factory() as db:
        return _persist_rows(db, rows)


def _new_sweep_id() -> str:
    from uuid import uuid4
    return uuid4().hex


# ── 读取面（API / 指标）──────────────────────────────────────────────────

def host_presence_rows(db: Session, host_id: str) -> list[HostScriptPresence]:
    """单台 host 的矩阵行（按态优先级与族名排序，供 UI 直出）。"""
    return list(db.execute(
        select(HostScriptPresence)
        .where(HostScriptPresence.host_id == host_id)
        .order_by(HostScriptPresence.state.desc(), HostScriptPresence.name)
    ).scalars())


def global_presence_rows(db: Session) -> list[dict]:
    """全部行（指标抓取期现算用；行数 = host × 目标版本）。"""
    return [
        dict(r) for r in db.execute(
            select(
                HostScriptPresence.host_id, HostScriptPresence.state,
                HostScriptPresence.checked_at,
            )
        ).mappings()
    ]


def presence_counts_by_host(db: Session) -> list[dict]:
    """per-host × state 计数（指标读面，避免把明细行搬进进程）。"""
    return [
        dict(r) for r in db.execute(
            select(
                HostScriptPresence.host_id,
                HostScriptPresence.state,
                func.count().label("n"),
            ).group_by(HostScriptPresence.host_id, HostScriptPresence.state)
        ).mappings()
    ]


def sweep_freshness(db: Session) -> Optional[datetime]:
    """最近一次**完整** sweep 的观测时刻 = `min(checked_at)`。

    每轮全量 upsert 覆盖全部未退役 host × 全集，故 min 即「最旧一行的观测时刻」；
    sweep 中途失败会让部分行停留在旧值 → min 变旧，新鲜度告警据此可判。
    """
    return db.execute(select(func.min(HostScriptPresence.checked_at))).scalar()


def sweep_freshness_range(db: Session) -> tuple[Optional[datetime], Optional[datetime]]:
    """``(min, max)``——min 判新鲜度，max 给 UI 显示「最近一次观测」。"""
    row = db.execute(
        select(
            func.min(HostScriptPresence.checked_at),
            func.max(HostScriptPresence.checked_at),
        )
    ).one()
    return row[0], row[1]
