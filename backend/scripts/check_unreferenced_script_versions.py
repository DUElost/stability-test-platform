"""报告零 plan_step 引用的脚本版本（退役候选）与退役判据巡检。

背景（评审 P5 + #735）：flash_firmware 等脚本在 `script` 表每版本一行，累积速度快于
治理；`plan_step` 以 `script_name + script_version` 冗余引用（非 FK）。本工具**只读**
查询（任意 DATABASE_URL，含生产库），列出每个 script 版本的 plan_step 引用计数与
「active 且零引用」的原始候选面，并按 `backend/services/script_retirement.py` 的判据
给出可执行退役计划与豁免原因。

用法:
    python -m backend.scripts.check_unreferenced_script_versions
    python -m backend.scripts.check_unreferenced_script_versions --json
    python -m backend.scripts.check_unreferenced_script_versions --name flash_firmware
    python -m backend.scripts.check_unreferenced_script_versions --guard   # 巡检：超期零引用仍活跃 → exit 1

只读 SELECT；不写库、不改状态。退出码：默认恒 0（诊断工具，非门禁）；`--guard` 是显式
门禁模式——0 = 无到期项、1 = 存在应退役而未退役的版本、2 = 使用事实不可得（无从判定，
不降级为「零使用」）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from sqlalchemy import create_engine, text

from backend.core.database import normalize_sync_database_url
from backend.core.env_source import resolve_database_url
from backend.services.script_retirement import (
    STALE_COOLDOWN_DAYS,
    ScriptVersionFact,
    classify,
    days_until_cooldown_expiry,
    retirement_candidates,
    version_key,
)

_QUERY = text(
    """
    SELECT s.name, s.version, s.is_active,
           COUNT(ps.id) AS refs
    FROM script s
    LEFT JOIN plan_step ps
           ON ps.script_name = s.name AND ps.script_version = s.version
    GROUP BY s.id, s.name, s.version, s.is_active
    ORDER BY s.name, s.version
    """
)

# 留存窗口内的执行事实——plan_run.plan_snapshot 是派发时冻结的步骤快照（ADR-0020/0026），
# 与 plan_step 的「当前配置引用」是两个正交维度（#706 口径，见 routes/scripts.py get_script_usage）。
_USAGE_QUERY = text(
    """
    SELECT step->>'script_name' AS name,
           step->>'script_version' AS version,
           MAX(pr.started_at) AS last_used
    FROM plan_run pr
    CROSS JOIN LATERAL jsonb_array_elements(
        COALESCE(pr.plan_snapshot->'steps', '[]'::jsonb)
    ) AS step
    GROUP BY 1, 2
    """
)


class UsageFactsUnavailable(Exception):
    """执行事实维度取不到（非 PG 方言、无 plan_run 表等）。

    fail-loud 而非回退空集：空集会被判成「从未执行」，让巡检/退役计划偏向过度退役。
    """


def compute_reference_counts(db) -> list[dict]:
    """对给定 SQLAlchemy 连接/会话返回 [{name, version, is_active, refs}]。"""
    return [dict(r) for r in db.execute(_QUERY).mappings()]


def compute_usage_facts(db) -> dict[tuple[str, str], date]:
    """返回 {(name, version): 留存窗口内末次执行日}。"""
    out: dict[tuple[str, str], date] = {}
    try:
        rows = db.execute(_USAGE_QUERY).mappings()
    except Exception as exc:  # SQLAlchemy 的方言/表缺失异常族
        raise UsageFactsUnavailable(str(exc)[:200]) from exc
    for r in rows:
        last = r["last_used"]
        if last is None or not r["name"]:
            continue
        out[(str(r["name"]), str(r["version"]))] = (
            last.date() if hasattr(last, "date") else last
        )
    return out


def build_facts(
    rows: list[dict], usage: dict[tuple[str, str], date]
) -> list[ScriptVersionFact]:
    return [
        ScriptVersionFact(
            name=r["name"],
            version=r["version"],
            is_active=bool(r["is_active"]),
            refs=int(r["refs"]),
            last_used_on=usage.get((r["name"], r["version"])),
        )
        for r in rows
    ]


def _report(
    facts: list[ScriptVersionFact], *, today: date, cooldown_days: int
) -> tuple[list[ScriptVersionFact], dict]:
    verdicts = classify(facts, today=today, cooldown_days=cooldown_days)
    plan = retirement_candidates(facts, today=today, cooldown_days=cooldown_days)
    hold = {
        "latest_active": [],
        "recent_use": [],
        "referenced": [],
        "inactive": [],
    }
    bucket = {
        "KEEP_LATEST_ACTIVE": "latest_active",
        "KEEP_RECENT_USE": "recent_use",
        "KEEP_REFERENCED": "referenced",
        "KEEP_INACTIVE": "inactive",
    }
    for f in sorted(facts, key=lambda x: (x.name, version_key(x.version))):
        v = verdicts[(f.name, f.version)]
        if v.retire:
            continue
        entry = {"name": f.name, "version": f.version, "reason": v.reason}
        expiry = days_until_cooldown_expiry(f, today=today, cooldown_days=cooldown_days)
        if expiry is not None and expiry > today:
            entry["reevaluate_on"] = expiry.isoformat()
        hold[bucket[v.decision]].append(entry)
    return plan, hold


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--name", help="只看指定脚本名（如 flash_firmware）")
    parser.add_argument(
        "--guard",
        action="store_true",
        help=f"巡检模式：存在超期（≥{STALE_COOLDOWN_DAYS} 天）零引用仍活跃的版本即 exit 1",
    )
    parser.add_argument(
        "--cooldown-days",
        type=int,
        default=STALE_COOLDOWN_DAYS,
        help=f"冷却期天数（默认 {STALE_COOLDOWN_DAYS}，与判据常量一致）",
    )
    parser.add_argument(
        "--today",
        help="判定基准日 YYYY-MM-DD（默认取本机日期；测试/复算用）",
    )
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else date.today()

    url, source = resolve_database_url()
    # resolve_database_url 给的是异步驱动 URL（生产库即 postgresql+asyncpg://），
    # 直接交给同步 create_engine 会在首次连接时炸 MissingGreenlet（#735 §1.3）。
    engine = create_engine(normalize_sync_database_url(url))
    try:
        with engine.connect() as conn:
            rows = compute_reference_counts(conn)
            usage_error: str | None = None
            try:
                usage = compute_usage_facts(conn)
            except UsageFactsUnavailable as exc:
                usage, usage_error = {}, str(exc)
    finally:
        engine.dispose()

    if args.name:
        rows = [r for r in rows if r["name"] == args.name]

    candidates = [r for r in rows if r["refs"] == 0 and r["is_active"]]
    facts = build_facts(rows, usage)

    if usage_error is not None:
        # 使用事实缺失 → 判据不可用。默认模式仍可给原始候选面；guard 模式不得
        # 把「无从判定」降级为「零使用」后放行或误判。
        if args.guard:
            print(f"GUARD UNKNOWN: 执行事实维度不可得：{usage_error}", file=sys.stderr)
            return 2
        plan, hold = None, None
    else:
        plan, hold = _report(facts, today=today, cooldown_days=args.cooldown_days)

    if args.json:
        payload = {
            "rows": rows,
            "retirement_candidates": candidates,  # 工具原始口径：active ∧ 零引用
            "stale_cooldown_days": args.cooldown_days,
            "usage_facts_available": usage_error is None,
        }
        if plan is not None:
            payload["retirement_plan"] = [
                {"name": f.name, "version": f.version,
                 "last_used_on": f.last_used_on.isoformat() if f.last_used_on else None}
                for f in plan
            ]
            payload["hold"] = hold
        if args.guard:
            payload["guard"] = {
                "status": "UNKNOWN" if plan is None else ("FAIL" if plan else "OK"),
                "violations": 0 if plan is None else len(plan),
            }
        # --json 下所有结论都进 payload：追加人类可读行会让 jq / json.load 直接失败
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"# script 版本引用（env 源：{source}）")
        print(f"{'name':<24} {'version':<10} {'active':<7} {'refs':>4}")
        for r in rows:
            print(
                f"{r['name']:<24} {r['version']:<10} {str(r['is_active']):<7} {r['refs']:>4}"
            )
        print()
        print(f"工具口径（active 且 plan_step 零引用）：{len(candidates)} 个")
        if plan is None:
            print(f"判据未评估：执行事实维度不可得（{usage_error}）")
        else:
            print(f"可退役（判据 `script_retirement`，冷却 {args.cooldown_days} 天）：{len(plan)} 个")
            for f in plan:
                print(f"  {f.name}@{f.version}")
            print(
                f"保留：同族最新 active {len(hold['latest_active'])}、"
                f"冷却期内有执行 {len(hold['recent_use'])}、"
                f"仍被引用 {len(hold['referenced'])}、已退役 {len(hold['inactive'])}"
            )
            for entry in hold["recent_use"]:
                if entry.get("reevaluate_on"):
                    print(f"  到期复评 {entry['reevaluate_on']}: {entry['name']}@{entry['version']}")

    if args.guard:
        # 退出码与 --json 无关：`--json --guard` 正是自动化消费的形态，
        # 只在人类可读模式下追加结论行。
        if not args.json:
            if plan:
                print(f"GUARD FAIL: {len(plan)} 个版本超期零引用仍活跃，应退役（见 retirement_plan）")
            elif plan is not None:
                print("GUARD OK: 无超期零引用活跃版本")
        return 1 if plan else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
