"""ADR-0029 P1 M-b / M-c 回填工具。

- M-b：建 6 个项目行（5 真实 + LEGACY，按 project_key 幂等 upsert）+ 专项字典
  种子（mtbf / power-cycle / monkey）+ 回填 ``plan.project_id`` / ``plan_run.project_id``
  （存量 4 Plan / 93 PlanRun 全归 LEGACY）。
- M-c：``device.project_id`` 按背景分析 §5 清单逐批回填（515 台）。
  **不自动推断**：model → 项目映射是 2026-08-18 人工确认的清单，本脚本只落库；
  清单外 model **拒绝执行**（防漏划）。

约束（ADR-0029 §迁移与回滚）：
- 幂等可重跑：回填以「目标列为 NULL」为条件，重跑不覆盖已确认的归属；
  Legacy 按 project_key upsert。
- --dry-run 必备：输出「将把哪些行划入哪个项目」，确认后再执行。
- M-c 完成标准：回填后 ``device.project_id`` 无 NULL（509 台入 5 项目 + 6 台入 LEGACY）。

用法：
    python tools/dev/backfill-test-project.py --phase mb [--dry-run]
    python tools/dev/backfill-test-project.py --phase mc [--dry-run]
    python tools/dev/backfill-test-project.py --phase all [--dry-run]

DB 目标遵循 env 单一源（backend/core/env_source.resolve_database_url）：
ambient DATABASE_URL 优先，否则仓库根 .env.backend。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.core.database import normalize_sync_database_url  # noqa: E402
from backend.core.env_source import resolve_database_url  # noqa: E402

# ── §5 清单（2026-08-18 决策者确认；jira_project_key 全留 NULL，P3 填齐）─────────

PROJECTS: list[dict] = [
    # project_key / display_name / customer / platform / form_factor
    {"key": "HONOR-MLD", "display": "荣耀 MLD 系列", "customer": "荣耀", "platform": "MTK", "form": "PHONE"},
    {"key": "HONOR-ELA", "display": "荣耀 ELA 系列", "customer": "荣耀", "platform": "MTK", "form": "PHONE"},
    {"key": "ZTE-Z258", "display": "中兴 Z258 系列", "customer": "中兴", "platform": "UNISOC", "form": "PHONE"},
    {"key": "ODM-DAM", "display": "ODM DAM 系列", "customer": "ODM", "platform": "MTK", "form": "PHONE"},
    {"key": "TRANSSION-X110", "display": "传音 X110 系列", "customer": "传音", "platform": "MTK", "form": "TABLET"},
    # LEGACY：承载 6 台未识别设备（model 空）与存量 Plan / PlanRun 回填
    {"key": "LEGACY", "display": "未分配（Legacy）", "customer": None, "platform": None, "form": None},
]

# model → project_key（族 = 项目粒度，D3；族内变体由 device.model 区分）
MODEL_TO_PROJECT: dict[str, str] = {
    "MLD_LX2": "HONOR-MLD",
    "MLD_LX3": "HONOR-MLD",
    "ELA_LX2": "HONOR-ELA",
    "ELA_LX3": "HONOR-ELA",
    "Z2581": "ZTE-Z258",
    "Z2582": "ZTE-Z258",
    "DAM_M500": "ODM-DAM",
    "Infinix_X1102D": "TRANSSION-X110",
}

# 专项字典种子（D6：MTBF / 开关机 / MONKEY / …，供下拉与聚合）
SPECIALTIES: list[tuple[str, str, int]] = [
    ("mtbf", "MTBF", 1),
    ("power-cycle", "开关机", 2),
    ("monkey", "MONKEY", 3),
]


def get_engine():
    url, source = resolve_database_url()
    print(f"[db] 目标来源: {source}（url 不打印，防密码入日志）")
    return create_engine(normalize_sync_database_url(url))


def project_id_by_key(db: Session) -> dict[str, int]:
    rows = db.execute(text("SELECT project_key, id FROM test_project")).all()
    return {key: pid for key, pid in rows}


def upsert_projects(db: Session, dry_run: bool) -> None:
    existing = project_id_by_key(db)
    to_create = [p for p in PROJECTS if p["key"] not in existing]
    print(f"[mb] 项目行: 已有 {len(existing)} 个, 将新建 {len(to_create)} 个")
    for p in to_create:
        print(f"    + {p['key']}: {p['display']} (customer={p['customer']}, "
              f"platform={p['platform']}, form={p['form']})")
    if dry_run or not to_create:
        return
    for p in to_create:
        db.execute(
            text(
                "INSERT INTO test_project (project_key, display_name, jira_project_key, "
                "product_line, customer, platform, form_factor, status) "
                "VALUES (:key, :display, NULL, NULL, :customer, :platform, :form, 'ACTIVE')"
            ),
            {"key": p["key"], "display": p["display"], "customer": p["customer"],
             "platform": p["platform"], "form": p["form"]},
        )
    db.commit()


def upsert_specialties(db: Session, dry_run: bool) -> None:
    existing = {row[0] for row in db.execute(text("SELECT key FROM specialty")).all()}
    to_create = [s for s in SPECIALTIES if s[0] not in existing]
    print(f"[mb] 专项字典: 已有 {len(existing)} 个, 将新建 {len(to_create)} 个")
    for key, display, order in to_create:
        print(f"    + {key}: {display} (sort_order={order})")
    if dry_run or not to_create:
        return
    for key, display, order in to_create:
        db.execute(
            text("INSERT INTO specialty (key, display_name, sort_order) "
                 "VALUES (:key, :display, :order)"),
            {"key": key, "display": display, "order": order},
        )
    db.commit()


def backfill_plan_ownership(db: Session, dry_run: bool, legacy_id: int) -> None:
    """plan.project_id / plan_run.project_id：NULL → LEGACY（存量归 Legacy，M-b）。"""
    plans = db.execute(
        text("SELECT id FROM plan WHERE project_id IS NULL ORDER BY id")
    ).all()
    runs = db.execute(
        text("SELECT id FROM plan_run WHERE project_id IS NULL ORDER BY id")
    ).all()
    print(f"[mb] 回填 plan.project_id → LEGACY: {len(plans)} 行 "
          f"(id={[r[0] for r in plans[:20]]}{'…' if len(plans) > 20 else ''})")
    print(f"[mb] 回填 plan_run.project_id → LEGACY: {len(runs)} 行 "
          f"(id={[r[0] for r in runs[:20]]}{'…' if len(runs) > 20 else ''})")
    if dry_run:
        return
    if plans:
        db.execute(
            text("UPDATE plan SET project_id = :pid WHERE project_id IS NULL"),
            {"pid": legacy_id},
        )
    if runs:
        db.execute(
            text("UPDATE plan_run SET project_id = :pid WHERE project_id IS NULL"),
            {"pid": legacy_id},
        )
    db.commit()


def plan_device_ownership(db: Session) -> tuple[dict[str, list], list]:
    """M-c 计划：model → 项目 的待回填设备；返回 (目标清单, 未覆盖 model 列表)。

    未覆盖 model **不归 LEGACY**——未知 model 不能推断归属（不自动推断原则），
    由人工确认后再补清单。
    """
    rows = db.execute(
        text("SELECT id, serial, model FROM device ORDER BY id")
    ).all()
    plan: dict[str, list] = {}
    unknown_models: list[str] = []
    for dev_id, serial, model in rows:
        target = None
        if model:  # 非空 model 必须命中清单，否则视为未覆盖
            target = MODEL_TO_PROJECT.get(model)
            if target is None:
                unknown_models.append(model)
        else:
            target = "LEGACY"  # model 空 → 未识别设备，归 LEGACY（M-c 完成标准）
        if target:
            plan.setdefault(target, []).append((dev_id, serial, model))
    return plan, sorted(set(unknown_models))


def backfill_device_ownership(db: Session, dry_run: bool) -> None:
    """M-c：device.project_id 逐批回填（幂等：仅 NULL 行）。"""
    plan, unknown_models = plan_device_ownership(db)
    if unknown_models:
        print("[mc] 未覆盖 model（清单外，不推断归属，须人工确认）：")
        for m in unknown_models:
            print(f"    ✗ {m}")
        if not dry_run:
            print("[mc] 存在清单外 model，拒绝执行（防漏划）；确认后补入清单再跑。")
            sys.exit(2)
        print("[mc] （执行模式将拒绝并 exit 2——须先人工确认未知 model 的归属后补入清单）")

    pids = project_id_by_key(db)
    print("[mc] 待回填设备清单（--dry-run 即此清单，确认后执行）:")
    total = 0
    for key in sorted(plan):
        # 幂等：只数/只更新 project_id IS NULL 的行
        ids = [r[0] for r in plan[key]]
        already = set(
            db.execute(
                text("SELECT id FROM device WHERE project_id IS NOT NULL "
                     "AND id = ANY(:ids)"),
                {"ids": ids},
            ).scalars()
        )
        to_fill = [r for r in plan[key] if r[0] not in already]
        print(f"    {key}: 族内 {len(plan[key])} 台, 已填 {len(already)} 台, "
              f"本次将回填 {len(to_fill)} 台")
        for _dev_id, serial, model in to_fill[:10]:
            print(f"        - {serial} ({model})")
        if len(to_fill) > 10:
            print(f"        … 共 {len(to_fill)} 台")
        total += len(to_fill)
        plan[key] = to_fill
    print(f"[mc] 合计将回填 {total} 台（dry-run 结束，无写入）" if dry_run
          else f"[mc] 合计回填 {total} 台")

    if dry_run:
        return
    for key, items in plan.items():
        if not items:
            continue
        ids = [r[0] for r in items]
        db.execute(
            text("UPDATE device SET project_id = :pid WHERE id = ANY(:ids) "
                 "AND project_id IS NULL"),
            {"pid": pids[key], "ids": ids},
        )
    db.commit()

    remaining = db.execute(
        text("SELECT COUNT(*) FROM device WHERE project_id IS NULL")
    ).scalar_one()
    print(f"[mc] 完成标准检查: device.project_id 为 NULL 的剩余 {remaining} 台"
          + (" ✅ 归零" if remaining == 0 else " ❌ 未归零（需人工核查）"))
    if remaining != 0:
        sys.exit(2)


def run_phase_mb(db: Session, dry_run: bool) -> None:
    upsert_projects(db, dry_run)
    upsert_specialties(db, dry_run)
    pids = project_id_by_key(db)
    legacy_id = pids.get("LEGACY")
    if legacy_id is None:
        if dry_run:
            # dry-run 只出计划：LEGACY 建成后 plan 回填指向它，此处不落库
            print("[mb] dry-run: LEGACY 项目行将先建，plan/plan_run 回填目标 = LEGACY")
            backfill_plan_ownership(db, dry_run=True, legacy_id=0)
            return
        print("[mb] LEGACY 项目行不存在——先建项目行（当前 dry-run 不落库）。")
        sys.exit(2)
    backfill_plan_ownership(db, dry_run, legacy_id)


def run_phase_mc(db: Session, dry_run: bool) -> None:
    pids = project_id_by_key(db)
    missing = [p["key"] for p in PROJECTS if p["key"] not in pids]
    if missing:
        print(f"[mc] 项目行缺失: {missing}——先跑 --phase mb。")
        sys.exit(2)
    backfill_device_ownership(db, dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", choices=["mb", "mc", "all"], required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="只输出计划清单，不写库（M-c 必先跑）")
    args = parser.parse_args()

    engine = get_engine()
    with Session(engine) as db:
        if args.phase in ("mb", "all"):
            run_phase_mb(db, args.dry_run)
        if args.phase in ("mc", "all"):
            run_phase_mc(db, args.dry_run)


if __name__ == "__main__":
    main()
