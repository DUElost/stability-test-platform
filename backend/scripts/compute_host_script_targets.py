"""每个 host 的脚本目标可达集（#2958 第五道闸的输入面，只读 CLI）。

判据与实现**单一事实源**在 `backend/services/script_presence.py`——同一套纯函数被常设
sweep（`run_sweep`）复用，本文件只是它的只读 CLI 壳，避免两处各写一份可达性口径。

- **全集** = `plan_step.enabled` 引用 ∩ `script.is_active`（与运行快照
  `precheck/scripts.expected_scripts_for_run` 同口径；实测生产库 51 版本 / 29 族）；
- **可达集** = 调度可达（`task_schedules.device_ids` 的设备**当前**绑定的 host）∪
  历史可达（近 `--days` 天 `plan_run_host`）；`n_a` = 全集 − 可达（不判红）；
- host 侧核验复用既有 `verify_scripts` RPC（由常设 sweep 承担，本工具不发起 RPC）。

只读 SELECT；不写库、不改状态。退出码：0 = 对账完成；2 = 使用事实不可得
（表缺失 / 方言不支持——**不降级为空集**）；3 = 工具自身异常。

用法:
    python -m backend.scripts.compute_host_script_targets
    python -m backend.scripts.compute_host_script_targets --json
    python -m backend.scripts.compute_host_script_targets --host 172.21.x.x --detail
    python -m backend.scripts.compute_host_script_targets --only-gaps --days 7
    python -m backend.scripts.compute_host_script_targets --full
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 以 `python backend/scripts/...py` 这种**路径形态**调用时 sys.path[0] 是
# backend/scripts，仓库根不在 path 上 ⇒ 下面的 `from backend…` 抛 ModuleNotFoundError。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine  # noqa: E402

from backend.core.database import normalize_sync_database_url  # noqa: E402
from backend.core.env_source import resolve_database_url  # noqa: E402
from backend.services.script_presence import (  # noqa: E402
    DEFAULT_HISTORY_DAYS,
    build_full_target_set,
    compute_host_targets,
    group_steps_by_plan,
    load_facts,
    observed_host_plans,
    scheduled_host_plans,
)


class TargetFactsUnavailable(Exception):
    """使用事实不可得（表缺失 / 方言不支持）——exit 2，不降级为空集。"""


def _load(conn, *, days: int) -> dict:
    try:
        return load_facts(conn, days=days)
    except Exception as exc:  # SQLAlchemy 的方言/表缺失异常族
        raise TargetFactsUnavailable(f"{type(exc).__name__}: {str(exc)[:200]}") from exc


def summarize(host_targets: list[dict], full: list[tuple[str, str]]) -> dict:
    """呈现层汇总（矩阵规模 / 并集覆盖）；sweep 侧的态汇总见 `summarize_states`。"""
    counts = sorted(len(h["reachable"]) for h in host_targets) or [0]
    covered: set[tuple[str, str]] = set()
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
    parser.add_argument(
        "--days", type=int, default=DEFAULT_HISTORY_DAYS,
        help=f"历史可达窗口天数（默认 {DEFAULT_HISTORY_DAYS}）",
    )
    parser.add_argument("--detail", action="store_true", help="展开每台 host 的 n/a 列表")
    parser.add_argument("--only-gaps", action="store_true", help="只列 n/a 非空的 host")
    parser.add_argument("--full", action="store_true", help="打印全集 (族, 版本) 列表")
    args = parser.parse_args(argv)

    url, _source = resolve_database_url()
    engine = create_engine(normalize_sync_database_url(url))
    try:
        with engine.connect() as conn:
            facts = _load(conn, days=args.days)
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
    except Exception as exc:  # 工具自身异常：与「无从判定」区分（exit 3）
        print(f"TARGETS ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
