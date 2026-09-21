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
    STP_SCRIPT_ROOT=... python -m backend.scripts.check_unreferenced_script_versions --pending-activation  # #2931 待激活视图
    STP_SCRIPT_ROOT=... python -m backend.scripts.check_unreferenced_script_versions --plan-step-drift     # #3030 重指漂移视图
    python backend/scripts/check_unreferenced_script_versions.py --guard   # 路径形态等价

只读 SELECT；不写库、不改状态。退出码：默认恒 0（诊断工具，非门禁）；`--guard` 是显式
门禁模式——0 = 无到期项、1 = 存在应退役而未退役的版本、2 = 使用事实不可得（无从判定，
不降级为「零使用」）、3 = 工具自身异常（**不是判定结果**）。3 与 1 必须可区分：判红会驱动
运维去退役生产版本，把崩溃读成判红就是反向的过度退役。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Iterable
from datetime import date
from pathlib import Path

# 以 `python backend/scripts/check_unreferenced_script_versions.py` 这种**路径形态**调用时
# sys.path[0] 是 backend/scripts，仓库根不在 path 上 ⇒ 下面的 `from backend…` 抛
# ModuleNotFoundError，解释器退出码恰好与 `--guard` 的「存在应退役版本」同码为 1
# （2026-09-17 实测踩到）。文档契约形态本是 `python -m`，但误用形态不得伪装成判红，
# 故与 tools/dev/retire_script_versions.py（PR #2430）同款补 bootstrap，与 cwd 无关。
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
)  # version_key 同供待激活视图使用（#2931）

# `--guard` 三个判定码之外的第四个退出码：工具自身异常，与 0/1/2 正交（不是判定结果）。
GUARD_ERROR_EXIT = 3

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


# ── #2931：待激活视图（磁盘 head 版本 vs script 表）─────────────────────────
# 判据按票面教训落在**目录行与数据行**上，不做散字符串相邻匹配；每族只看磁盘
# head 版本——#735 的存量零引用 backlog 会把全量对拍淹掉（今天实测踩过），而
# 「激活滞后」的定义就在 head 上：族内旧版未引用是退役问题，head 缺行/未激活
# 才是「修复合入了却不生效」的上线问题。三态：
#   unregistered（script 表无行）→ 第 3 道（scan 注册）未做；
#   inactive（有行但 is_active=false）→ 反激活遗留/退役误伤；
#   不列出 active——视图的输出即「落后集合」，空 = 全部生效，
#   与 script-versioning.md 的收尾判据「合入后该视图不再列出它」同构。

_DIR_VERSION_RE = re.compile(r"^v(.+)$")


def scan_disk_script_versions(root: Path) -> dict[str, list[str]]:
    """枚举 `STP_SCRIPT_ROOT` 下 `<name>/v<version>/` 目录（非法名/非目录跳过）。"""
    out: dict[str, list[str]] = {}
    if not root.is_dir():
        return out
    for fam in sorted(root.iterdir()):
        if not fam.is_dir() or fam.name.startswith((".", "_")):
            continue
        versions: list[str] = []
        for child in fam.iterdir():
            if not child.is_dir():
                continue
            m = _DIR_VERSION_RE.match(child.name)
            if m and m.group(1):
                versions.append(m.group(1))
        if versions:
            out[fam.name] = sorted(versions)
    return out


def pending_activation_view(
    disk: dict[str, list[str]],
    db_rows: Iterable[dict],  # {name, version, is_active}
    *,
    name_filter: "str | None" = None,
) -> list[dict]:
    """每族磁盘 head 版本（version_key 最大）的激活状态；只返回落后项。"""
    by_key = {(r["name"], r["version"]): bool(r["is_active"]) for r in db_rows}
    lagging: list[dict] = []
    for name, versions in sorted(disk.items()):
        if name_filter and name != name_filter:
            continue
        head = max(versions, key=version_key)
        key = (name, head)
        if key not in by_key:
            lagging.append({"name": name, "head_version": head, "state": "unregistered"})
        elif not by_key[key]:
            lagging.append({"name": name, "head_version": head, "state": "inactive"})
    return lagging


# ── plan_step 重指漂移视图（#3030）───────────────────────────────────────────
# 四道里第 4 道（既有 Plan 的 `plan_step.script_version` 重指）此前无账：第 3 道由上面的
# `--pending-activation` 记账，本视图补第 4 道。两轴分类按 owner 裁决（#3030，2026-09-21）：
#   轴 2 活跃度：≤14d 有 PlanRun 或 enabled schedule = active；≤30d = semi_active；否则
#               historical（不追，须并进冻结登记或列待裁决）。判据以 PlanRun 为主——
#               实测 44 个落后 Plan 里只有 1 个有 enabled schedule，只按 schedule 判会漏 43。
#   轴 1 追平风险（Δ 类型，优先级 review_required > metadata_diff > metadata_compatible）：
#               - review_required：族/Plan 命中 `_PLAN_PIN_REVIEW` 登记表（含复查期）；
#               - metadata_diff：新旧版本在 DB 的 default_params/param_schema 有差异，
#                 或 head/钉版在库缺行（保守判需人工核）；
#               - metadata_compatible：其余。**不等于安全**——check_device v1.0.2 的
#                 150s 内建预算在 DB 元数据上查不出来（#2981），登记表是必要补充。
# 只读；账本非门禁（exit 0），`STP_SCRIPT_ROOT` 未配置 = 无从判定（exit 2），与
# `--pending-activation` 同姿势。

PLAN_STEP_DRIFT_ACTIVE_DAYS = 14
PLAN_STEP_DRIFT_SEMI_ACTIVE_DAYS = 30

#: 冻结/待核登记表（轴 1 的 (a)/(c) 类载体）：key = `family:<name>` 或 `plan:<id>`。
#: 命中即把相关落后步骤标 review_required 并带 reason + 复查期；复查期过期时在报告里
#: 单列「须重新裁决」——不允许无登记、无复查期的沉默冻结。
_PLAN_PIN_REVIEW: dict[str, dict[str, str]] = {
    "family:check_device": {
        "reason": (
            "追至 >=1.0.2 需同族步骤 timeout ≥180s（内建 total_budget_seconds=150，"
            "#2981）；配套修订未落地前不追"
        ),
        "review_by": "2026-10-21",
    },
}

_PLAN_STEP_DRIFT_QUERY = text(
    """
    SELECT ps.plan_id,
           p.name AS plan_name,
           ps.script_name,
           ps.script_version,
           (SELECT max(pr.started_at)::date
              FROM plan_run pr WHERE pr.plan_id = ps.plan_id) AS last_run_on,
           COALESCE((SELECT bool_or(ts.enabled)
                       FROM task_schedules ts WHERE ts.plan_id = ps.plan_id), false)
             AS schedule_enabled
    FROM plan_step ps
    JOIN plan p ON p.id = ps.plan_id
    ORDER BY ps.plan_id, ps.script_name
    """
)

_SCRIPT_METADATA_QUERY = text(
    """
    SELECT name, version,
           default_params::text AS default_params,
           param_schema::text AS param_schema
    FROM script
    """
)


def compute_plan_step_facts(db) -> list[dict]:
    """返回 [{plan_id, plan_name, script_name, script_version, last_run_on, schedule_enabled}]。"""
    out: list[dict] = []
    for r in db.execute(_PLAN_STEP_DRIFT_QUERY).mappings():
        last = r["last_run_on"]
        out.append(
            {
                "plan_id": int(r["plan_id"]),
                "plan_name": r["plan_name"],
                "script_name": str(r["script_name"]),
                "script_version": str(r["script_version"]),
                "last_run_on": last if isinstance(last, date) else None,
                "schedule_enabled": bool(r["schedule_enabled"]),
            }
        )
    return out


def compute_script_metadata(db) -> dict[tuple[str, str], tuple[str, str]]:
    """{(name, version): (default_params, param_schema)}（文本形态，只做相等性对拍）。"""
    return {
        (str(r["name"]), str(r["version"])): (r["default_params"], r["param_schema"])
        for r in db.execute(_SCRIPT_METADATA_QUERY).mappings()
    }


def classify_plan_activity(
    last_run_on: "date | None", schedule_enabled: bool, *, today: date
) -> str:
    """轴 2：active / semi_active / historical（判据以 PlanRun 为主、schedule 为辅）。"""
    if schedule_enabled:
        return "active"
    if last_run_on is None:
        return "historical"
    age = (today - last_run_on).days
    if age <= PLAN_STEP_DRIFT_ACTIVE_DAYS:
        return "active"
    if age <= PLAN_STEP_DRIFT_SEMI_ACTIVE_DAYS:
        return "semi_active"
    return "historical"


def _review_expired(review_by: "str | None", today: date) -> bool:
    """登记表复查期判定；值不可解析按过期处理（宁红不漏）。"""
    if not review_by:
        return False
    try:
        return date.fromisoformat(str(review_by)) < today
    except ValueError:
        return True


def plan_step_drift_view(
    disk: dict[str, list[str]],
    plan_steps: Iterable[dict],
    script_meta: dict[tuple[str, str], tuple[str, str]],
    *,
    today: date,
    registry: "dict[str, dict[str, str]] | None" = None,
    name_filter: "str | None" = None,
) -> dict:
    """对拍「磁盘 head vs plan_step 钉版」，只返回落后步骤（含两轴标注）。

    族在磁盘无 head（未收录/已删）时跳过并计数——无从判定不等于不落后。
    """
    reg = _PLAN_PIN_REVIEW if registry is None else registry
    heads = {name: max(versions, key=version_key) for name, versions in disk.items() if versions}
    steps: list[dict] = []
    skipped_no_head = 0
    for s in plan_steps:
        name = s["script_name"]
        if name_filter and name != name_filter:
            continue
        head = heads.get(name)
        if head is None:
            skipped_no_head += 1
            continue
        pinned = s["script_version"]
        if pinned == head:
            continue
        entry = reg.get(f"plan:{s['plan_id']}") or reg.get(f"family:{name}")
        if entry is not None:
            delta = "review_required"
        elif (name, pinned) not in script_meta or (name, head) not in script_meta:
            delta = "metadata_diff"  # 库缺行：保守判需人工核
        elif script_meta[(name, pinned)] != script_meta[(name, head)]:
            delta = "metadata_diff"
        else:
            delta = "metadata_compatible"
        item = {
            "plan_id": s["plan_id"],
            "plan_name": s["plan_name"],
            "script_name": name,
            "pinned_version": pinned,
            "head_version": head,
            "activity": classify_plan_activity(
                s.get("last_run_on"), bool(s.get("schedule_enabled")), today=today
            ),
            "delta_type": delta,
        }
        if entry is not None:
            item["review_reason"] = entry.get("reason", "")
            item["review_by"] = entry.get("review_by", "")
        steps.append(item)

    by_activity = {"active": 0, "semi_active": 0, "historical": 0}
    by_delta = {"review_required": 0, "metadata_diff": 0, "metadata_compatible": 0}
    for it in steps:
        by_activity[it["activity"]] += 1
        by_delta[it["delta_type"]] += 1
    expired = [
        {"key": k, "reason": v.get("reason", ""), "review_by": v.get("review_by", "")}
        for k, v in sorted(reg.items())
        if _review_expired(v.get("review_by"), today)
    ]
    return {
        "steps": steps,
        "summary": {
            "lagging_steps": len(steps),
            "lagging_plans": len({it["plan_id"] for it in steps}),
            "by_activity": by_activity,
            "by_delta": by_delta,
            "skipped_no_disk_head": skipped_no_head,
        },
        "expired_review_entries": expired,
    }


def _evaluate_plan_step_drift(args, today: date) -> int:
    """`--plan-step-drift` 分支：只读取事实、打印账本，不写库。"""
    script_root = (os.getenv("STP_SCRIPT_ROOT") or "").strip()
    if not script_root:
        print(
            "PLAN-STEP-DRIFT UNKNOWN: STP_SCRIPT_ROOT 未设置，磁盘 head 不可枚举",
            file=sys.stderr,
        )
        return 2
    url, source = resolve_database_url()
    engine = create_engine(normalize_sync_database_url(url))
    try:
        with engine.connect() as conn:
            plan_steps = compute_plan_step_facts(conn)
            script_meta = compute_script_metadata(conn)
    finally:
        engine.dispose()

    view = plan_step_drift_view(
        scan_disk_script_versions(Path(script_root)),
        plan_steps,
        script_meta,
        today=today,
        name_filter=args.name,
    )
    if args.json:
        print(
            json.dumps({"plan_step_drift": view}, ensure_ascii=False, indent=2, default=str)
        )
        return 0

    summary = view["summary"]
    print(f"# plan_step 重指漂移（env 源：{source}；磁盘根：{script_root}）")
    print(
        f"落后 {summary['lagging_steps']} 步 / {summary['lagging_plans']} Plan"
        f"（活跃 {summary['by_activity']['active']}、"
        f"半活跃 {summary['by_activity']['semi_active']}、"
        f"历史 {summary['by_activity']['historical']}；"
        f"需人工核 {summary['by_delta']['review_required']}、"
        f"元数据差异 {summary['by_delta']['metadata_diff']}）"
    )
    if summary["skipped_no_disk_head"]:
        print(f"（{summary['skipped_no_disk_head']} 步的族在磁盘无 head，已跳过）")
    labels = {"active": "活跃", "semi_active": "半活跃", "historical": "历史"}
    for bucket in ("active", "semi_active", "historical"):
        bucket_steps = [s for s in view["steps"] if s["activity"] == bucket]
        if not bucket_steps:
            continue
        print(f"\n[{labels[bucket]}] {len(bucket_steps)} 步")
        for s in sorted(bucket_steps, key=lambda x: (x["plan_id"], x["script_name"])):
            mark = ""
            if s["delta_type"] == "review_required":
                mark = (
                    f"  需人工核：{s.get('review_reason', '')}"
                    f"（复查 {s.get('review_by', '')}）"
                )
            elif s["delta_type"] == "metadata_diff":
                mark = "  元数据差异（先核参数契约再追）"
            print(
                f"  plan {s['plan_id']:>3} {s['plan_name'][:28]:<28} "
                f"{s['script_name']} {s['pinned_version']} → {s['head_version']}{mark}"
            )
    if view["expired_review_entries"]:
        print("\n冻结/待核登记已过期（须重新裁决）：")
        for e in view["expired_review_entries"]:
            print(f"  {e['key']}  复查期 {e['review_by']}  {e['reason']}")
    # 账本非门禁：落后项的处置走版本五步第 4 道（重指），不在此判红。
    return 0


def _evaluate(argv: list[str] | None = None) -> int:
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
    parser.add_argument(
        "--pending-activation",
        action="store_true",
        help="待激活视图（#2931）：枚举 STP_SCRIPT_ROOT 的磁盘 head 版本与 script 表对账，"
        "只列 unregistered/inactive——版本上线五步的第 3 道有没有做，从此可查",
    )
    parser.add_argument(
        "--plan-step-drift",
        action="store_true",
        help="重指漂移视图（#3030）：对拍 STP_SCRIPT_ROOT 的磁盘 head 与 plan_step 钉版，"
        "按活跃度三态（活跃/半活跃/历史）+ Δ 类型（需人工核/元数据差异/元数据兼容）"
        "只列落后步骤——版本上线五步的第 4 道有没有做，从此可查",
    )
    args = parser.parse_args(argv)
    today = date.fromisoformat(args.today) if args.today else date.today()

    if args.plan_step_drift:
        return _evaluate_plan_step_drift(args, today)

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

    if args.pending_activation:
        script_root = (os.getenv("STP_SCRIPT_ROOT") or "").strip()
        if not script_root:
            # 与 scan 端点同姿势：根未配置 = 无从判定（exit 2），不是「没有落后项」。
            print("PENDING UNKNOWN: STP_SCRIPT_ROOT 未设置，磁盘侧不可枚举", file=sys.stderr)
            return 2
        lagging = pending_activation_view(
            scan_disk_script_versions(Path(script_root)), rows,
            name_filter=args.name,
        )
        if args.json:
            print(json.dumps({"pending_activation": lagging}, ensure_ascii=False, indent=2))
        elif not lagging:
            print("pending-activation: 全部脚本族磁盘 head 版本均已在库且 active（无待激活项）")
        else:
            print(f"pending-activation: {len(lagging)} 个族 head 落后（未注册/未激活）：")
            for item in lagging:
                print(f"  {item['name']} v{item['head_version']}  -> {item['state']}")
        # 视图是账本不是门禁：退出码 0 表「对账完成」；落后项的处置走版本五步，
        # 不在此处判红（guard 语义保留给 #735 的退役巡检）。
        return 0

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


def main(argv: list[str] | None = None) -> int:
    """把非预期异常折成 `GUARD_ERROR_EXIT`，保住「1 只来自真判定」这条契约。

    bootstrap 堵掉最常见的一条（调用形态错导致的 import 期崩溃），这里兜其余同源风险：
    连接失败、schema 漂移、依赖缺失。bootstrap 之前 import 期就炸，任何 wrapper 都抓不到
    ⇒ 两者必须同时存在，退出码契约才成立。
    """
    try:
        return _evaluate(argv)
    except SystemExit:
        raise  # argparse 的 --help / 参数错误自己处理，不改写
    except Exception as exc:  # noqa: BLE001 — 就是要兜住一切非判定异常
        print(
            f"GUARD ERROR: 工具自身异常（退出码 {GUARD_ERROR_EXIT}，"
            f"这**不是**「存在应退役版本」）：{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return GUARD_ERROR_EXIT


if __name__ == "__main__":
    sys.exit(main())
