"""#1956 存量回填：为「无 scan 门禁」的平台事件补齐上送提升。

背景
    展锐（UNISOC）的 ``event_type=UNIVIEW`` 事件**没有 scan 产物**，其有效性在 Agent
    解析期即已判定（#1946：normalboot-only 目录直接丢弃），因此入库即可上送；
    而 ``LOCAL`` 在本模型里的语义是「有 scan 但未被引用 → 有意不传」（ADR-0028 方案 A）。
    把展锐事件长期留在 ``LOCAL`` 属于**语义误用**：它既不会上送，也不会进入归档。

    #1957 起，该提升在**入库时**生效（``resolve_initial_upload_state``，调用点
    ``backend/api/routes/agent_api.py``）；对**存量行不追溯** —— 于是此前产生的
    UNIVIEW 行永远停在 ``LOCAL``。本脚本把库存量纠正为「按现行规则应有的状态」。

规则来源（不复制）
    候选判定**直接调用** ``backend/services/device_log_event.resolve_initial_upload_state``，
    本脚本不复制任何判据；规则自身有单测：
    ``backend/tests/services/test_device_log_event_uniview_upload_1956.py``。

约束
    - 默认 **dry-run**：只打印将改动哪些行及其原因；``--apply`` 才落库；
    - **幂等**：提升后行的 state 已不在等待态 → 重跑不再命中；
    - 竞态保护：``UPDATE ... WHERE state = <旧值>``，若期间被其他流程改动则跳过该行；
    - 只动 ``state``（+ ``updated_at``），不触碰路径/大小/校验和等领域字段。

用法
    python tools/dev/backfill-no-scan-gate-upload-state.py            # 预演
    python tools/dev/backfill-no-scan-gate-upload-state.py --apply    # 落库
    python tools/dev/backfill-no-scan-gate-upload-state.py --apply --limit 10

边界（#2285）
    - ``--limit`` 是 **SQL 级**扫描上界（默认 1000）：此前先物化全部候选行再在 Python
      里截断，大表上是一次无界加载；``--limit 0`` 才不限（需自行确认表规模）。
    - ``--apply`` 另有单次落库行数上限 ``--max-rows``（默认 500）：超出即拒跑，避免
      一条命令扫全表地改生产库；分批复跑是幂等的。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import create_engine, func, select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend.core.database import normalize_sync_database_url  # noqa: E402
from backend.core.env_source import resolve_database_url  # noqa: E402
from backend.models.device_log_event import DeviceLogEvent  # noqa: E402
from backend.services.device_log_event import (  # noqa: E402
    _AWAITING_UPLOAD_STATES,
    resolve_initial_upload_state,
)

#: 仍处于「等待上送」的行才可能被提升。#2285：直接复用判据真源（同模块的私有常量），
#: 不再复制字面量——等待态新增第三态时本工具不会静默漏掉。
CANDIDATE_STATES = tuple(sorted(_AWAITING_UPLOAD_STATES))


def plan_changes(db: Session, limit: int = 0) -> tuple[list, bool]:
    """返回 ``([(row, target_state)], 是否触到 limit 上界)``。

    按现行规则**应当**处于其它状态、且当前仍是等待态的存量行。``limit`` 下推到 SQL
    （#2285），故扫到的行数有上界；命中上界时第二项为 True，调用方提示分批。
    """
    stmt = (
        select(DeviceLogEvent)
        .where(DeviceLogEvent.state.in_(CANDIDATE_STATES))
        .order_by(DeviceLogEvent.created_at, DeviceLogEvent.id)
    )
    if limit:
        stmt = stmt.limit(limit)
    rows = db.execute(stmt).scalars().all()
    changes = []
    for row in rows:
        target = resolve_initial_upload_state(row.event_type, row.state)
        if target == row.state:
            continue
        changes.append((row, target))
    return changes, bool(limit) and len(rows) >= limit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="#1956 存量事件上送状态回填（默认 dry-run）",
    )
    parser.add_argument("--apply", action="store_true", help="真正落库（缺省仅预演）")
    parser.add_argument(
        "--limit", type=int, default=1000,
        help="SQL 级扫描上界：最多取多少候选行（默认 1000；0 = 不限）",
    )
    parser.add_argument(
        "--max-rows", type=int, default=500,
        help="--apply 单次落库行数上限（防误操作；确需更多时显式调大）",
    )
    args = parser.parse_args()

    # 与 tools/dev/backfill-test-project.py 同款取用方式：resolve_database_url() 返回 (url, source)。
    url, _source = resolve_database_url()
    engine = create_engine(normalize_sync_database_url(url))
    with Session(engine) as db:
        changes, hit_limit = plan_changes(db, args.limit)

        print(
            f"[scan] 候选行扫描上界 {args.limit or '∞'}，命中 {len(changes)} 行"
            f"（{'APPLY' if args.apply else 'DRY-RUN'}）"
        )
        if hit_limit:
            print(
                f"[warn] 已触到 --limit {args.limit} 上界，可能还有更多候选："
                "重跑本工具（幂等）或调大 --limit"
            )
        for row, target in changes:
            print(
                f"  {row.state} -> {target}  serial={row.serial} platform={row.platform} "
                f"type={row.event_type} run={row.plan_run_id} id={row.id}"
            )

        if not changes:
            print("[done] 无需改动（幂等收敛）")
            return 0
        if not args.apply:
            print("[done] 预演结束，未落库；确认后加 --apply")
            return 0
        if len(changes) > args.max_rows:
            print(
                f"[abort] 本次将落库 {len(changes)} 行 > --max-rows {args.max_rows}；"
                "先用 --limit 分批，或确认范围后显式调大 --max-rows",
                file=sys.stderr,
            )
            return 2

        applied = skipped = 0
        for row, target in changes:
            result = db.execute(
                update(DeviceLogEvent)
                .where(DeviceLogEvent.id == row.id, DeviceLogEvent.state == row.state)
                .values(state=target, updated_at=func.now())
            )
            if result.rowcount:
                applied += 1
            else:  # 期间被其它流程改动 → 不覆盖
                skipped += 1
                print(f"  [race-skip] id={row.id} 期望旧态 {row.state}，实际已变")
        db.commit()
        print(f"[done] 已提升 {applied} 行，跳过 {skipped} 行；Agent EventUploader 将随后上送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
