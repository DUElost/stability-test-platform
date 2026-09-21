"""每个 host 的脚本目标可达集（#2958 第五道闸的输入面）。

背景
----
「脚本版本生效」现有账都只看 DB / 部署树；run 作用域的 host 侧核验
（`backend/services/admission_pump.py` 的 `_verify_scripts_phase`）只在派发时覆盖
「本 run 的 host × 本 run 快照」，维护窗 / 近期无 run 的 host 无账（#2958 实证：
`172.21.15.89` 缺 3 个版本目录而 DB 面全绿）。

本工具**只读**计算「每台 host 预期会用到哪些 (族, 版本)」= **可达集**，以及相对
**全集**的差集（`n_a`：全集有、该 host 不会跑到 → 不判红）。它是第五道闸的输入面；
host 侧核验复用既有 `verify_scripts` RPC（见 #2958 评论的落库方案）。

可达判据（两层并集，均实测过）
------------------------------
1. **调度可达**：`task_schedules.device_ids` 中的设备**当前**绑定的 host
   （链式计划；实测生产库仅 2 条 schedule）；
2. **历史可达**：近 `--days` 天（默认 30）`plan_run_host` 出现过的 host
   （手工/一次性计划的主要来源；实测近 30 天 46 台 host / 47 个 plan）。

目标集口径与运行快照同源（`backend/services/plan_dispatcher_core.py` 组快照时
过滤 `enabled is False`）：`plan_step.enabled` × `script.is_active`。
实测生产库 = **51 版本 / 29 族**——[废弃] plan#13 唯一的 `flash_firmware@1.3.15`
已被 `enabled` 过滤掉，无需另设「排除废弃计划」判据。

只读 SELECT；不写库、不改状态。退出码：0 = 对账完成；2 = 使用事实不可得
（表缺失 / 方言不支持——**不降级为空集**）；3 = 工具自身异常。

用法:
    python -m backend.scripts.compute_host_script_targets
    python -m backend.scripts.compute_host_script_targets --json
    python -m backend.scripts.compute_host_script_targets --host 172.21.15.89 --detail
    python -m backend.scripts.compute_host_script_targets --only-gaps --days 7
    python -m backend.scripts.compute_host_script_targets --full
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 以 `python backend/scripts/...py` 这种**路径形态**调用时 sys.path[0] 是
# backend/scripts，仓库根不在 path 上 ⇒ 下面的 `from backend…` 抛 ModuleNotFoundError。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402

from backend.core.database import normalize_sync_database_url  # noqa: E402
from backend.core.env_source import resolve_database_url  # noqa: E402

class TargetFactsUnavailable(Exception):
    """使用事实不可得（表缺失 / 方言不支持）——exit 2，不降级为空集。"""


# ── 纯函数层（测试只测这一层）───────────────────────────────────────────────

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


def compute_host_targets(
    host_rows: list[dict],
    *,
    full: list[tuple[str, str]],
    by_plan: dict[int, set[tuple[str, str]]],
    scheduled: dict[str, set[int]],
    observed: dict[str, set[int]],
) -> list[dict]:
    """逐 host 汇总：可达集 + n/a 差集 + 来源计数（保持入参顺序）。"""
    full_set = set(full)
    out: list[dict] = []
    for h in host_rows:
        host_id = str(h["id"])
        sched = scheduled.get(host_id, set())
        obs = observed.get(host_id, set())
        reachable: set[tuple[str, str]] = set()
        for plan_id in sched | obs:
            reachable |= by_plan.get(plan_id, set())
        reachable &= full_set  # 与全集同口径：停用/未注册版本不进可达集
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


def summarize(host_targets: list[dict], full: list[tuple[str, str]]) -> dict:
    counts = sorted(len(h["reachable"]) for h in host_targets) or [0]
    covered = set()
    for h in host_targets:
        covered |= set(h["reachable"])
    return {
        "full_versions": len(full),
        "full_families": len({name for name, _ in full}),
        "hosts": len(host_targets),
        "hosts_with_gap_free": sum(1 for h in host_targets if not h["n_a"]),
        "reachable_cells": sum(len(h["reachable"]) for h in host_targets),
        "reachable_min": counts[0],
        "reachable_max": counts[-1],
        "union_covered_versions": len(covered),
        "union_missing_versions": len(set(full) - covered),
    }


# ── DB 层（薄；失败 fail-loud，不降级）────────────────────────────────────

_SQL_STEPS = text(
    "SELECT plan_id, script_name, script_version FROM plan_step WHERE enabled"
)
_SQL_SCRIPTS = text("SELECT name, version, is_active FROM script WHERE is_active")
_SQL_HOSTS = text(
    "SELECT id, status FROM host WHERE retired_at IS NULL ORDER BY id"
)
_SQL_DEVICES = text("SELECT id, host_id FROM device WHERE host_id IS NOT NULL")
_SQL_SCHEDULES = text("SELECT plan_id, device_ids FROM task_schedules")
_SQL_RUNS = text(
    """
    SELECT DISTINCT pr.plan_id, prh.host_id
    FROM plan_run pr
    JOIN plan_run_host prh ON prh.plan_run_id = pr.id
    WHERE pr.started_at >= :cutoff
    """
)


def _fetch(conn, sql, **params) -> list[dict]:
    try:
        return [dict(r) for r in conn.execute(sql, params).mappings()]
    except Exception as exc:  # 方言/表缺失异常族
        raise TargetFactsUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc


def load_facts(conn, *, days: int) -> dict:
    """载入全部输入行；任一面不可得 → TargetFactsUnavailable（exit 2）。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(0, days))
    return {
        "steps": _fetch(conn, _SQL_STEPS),
        "scripts": _fetch(conn, _SQL_SCRIPTS),
        "hosts": _fetch(conn, _SQL_HOSTS),
        "devices": _fetch(conn, _SQL_DEVICES),
        "schedules": _fetch(conn, _SQL_SCHEDULES),
        "runs": _fetch(conn, _SQL_RUNS, cutoff=cutoff),
    }


# ── CLI ───────────────────────────────────────────────────────────────────

def _render_table(host_targets: list[dict], *, detail: bool) -> None:
    print(f"{'host_id':22s} {'status':9s} {'sched':>5s} {'obs':>4s} "
          f"{'reach':>6s} {'n/a':>4s}")
    for h in host_targets:
        print(f"{h['host_id']:22s} {h['status']:9s} {h['scheduled_plans']:5d} "
              f"{h['observed_plans']:4d} {len(h['reachable']):6d} {len(h['n_a']):4d}")
        if detail and h["n_a"]:
            for name, version in h["n_a"]:
                print(f"      n/a  {name}@{version}")


def _evaluate(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--host", help="只看指定 host_id")
    parser.add_argument("--days", type=int, default=30, help="历史可达窗口天数（默认 30）")
    parser.add_argument("--detail", action="store_true", help="展开每台 host 的 n/a 列表")
    parser.add_argument("--only-gaps", action="store_true", help="只列 n/a 非空的 host")
    parser.add_argument("--full", action="store_true", help="打印全集 (族, 版本) 列表")
    args = parser.parse_args(argv)

    url, _source = resolve_database_url()
    engine = create_engine(normalize_sync_database_url(url))
    try:
        with engine.connect() as conn:
            facts = load_facts(conn, days=args.days)
    except TargetFactsUnavailable as exc:
        print(f"TARGETS UNKNOWN: 使用事实不可得：{exc}", file=sys.stderr)
        return 2
    finally:
        engine.dispose()

    full = build_full_target_set(facts["steps"], facts["scripts"])
    by_plan = group_steps_by_plan(facts["steps"])
    scheduled = scheduled_host_plans(facts["schedules"], facts["devices"])
    observed = observed_host_plans(facts["runs"])
    rows = facts["hosts"]
    if args.host:
        rows = [r for r in rows if str(r["id"]) == args.host]

    targets = compute_host_targets(
        rows, full=full, by_plan=by_plan, scheduled=scheduled, observed=observed
    )
    if args.only_gaps:
        targets = [h for h in targets if h["n_a"]]
    summary = summarize(targets, full)

    if args.json:
        print(json.dumps(
            {
                "full": [{"name": n, "version": v} for n, v in full],
                "hosts": [
                    {
                        "host_id": h["host_id"],
                        "status": h["status"],
                        "scheduled_plans": h["scheduled_plans"],
                        "observed_plans": h["observed_plans"],
                        "reachable": [{"name": n, "version": v} for n, v in h["reachable"]],
                        "n_a": [{"name": n, "version": v} for n, v in h["n_a"]],
                    }
                    for h in targets
                ],
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ))
        return 0

    if args.full:
        print(f"全集：{summary['full_versions']} 版本 / {summary['full_families']} 族")
        for name, version in full:
            print(f"  {name}@{version}")
        print()

    _render_table(targets, detail=args.detail)
    print()
    print(
        "summary: hosts={hosts} 无 n/a={hosts_with_gap_free} "
        "可达格={reachable_cells}（min={reachable_min} max={reachable_max}） "
        "并集覆盖={union_covered_versions}/{full_versions} 未覆盖={union_missing_versions}".format(
            **summary
        )
    )
    return 0


def main() -> int:
    try:
        return _evaluate()
    except TargetFactsUnavailable as exc:  # 兜底：主流程外的同类失败也按 exit 2
        print(f"TARGETS UNKNOWN: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # 工具自身异常：与「无从判定」区分（exit 3）
        print(f"TARGETS ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
