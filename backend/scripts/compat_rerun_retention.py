#!/usr/bin/env python3
"""``compat_reruns/`` 派生族留存：默认只读 Dry-run，回收必须逐笔点名（#4045 第 2 刀）。

**警告**：``--purge-rerun-id`` 会真的删磁盘目录。缺省动作永远是只读盘点；回收必须由
操作人显式列出 rerun id 并给出 ``--operator``，且受 ``COMPAT_RERUN_PURGE_BATCH_LIMIT``
限制。Dry-run 的输出**不是**删除授权——每笔删除前都会用当前库状态重判定一次。

三栏呈现（裁决 3 = 3a）：``candidates`` 可回收、``retained`` 逐条给保留理由、
``purged`` 已回收（不再判定，也不出现在保留栏里制造噪音）。报告始终带 ``anchor_days``
与 ``anchor_note``：候选为空时先排除「旋钮把期限设成一百年」这种假干净。

用法::

    python -m backend.scripts.compat_rerun_retention
    python -m backend.scripts.compat_rerun_retention --json
    python -m backend.scripts.compat_rerun_retention --retention-days 14
    python -m backend.scripts.compat_rerun_retention \\
        --purge-rerun-id 12 --operator alice

退出码：0 正常；2 发起不合法（空列表 / 超批次上限 / 缺 operator）；1 运行失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any, Optional

from backend.core.database import SessionLocal
from backend.services import compat_rerun_retention as retention
from backend.services.compat_rerun_retention import CompatRerunRetentionError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.scripts.compat_rerun_retention",
        description=__doc__.splitlines()[0],
    )
    parser.add_argument("--log-root", default=None, help="日志根（默认 STP_AEE_NFS_ROOT）")
    parser.add_argument(
        "--retention-days", type=int, default=None,
        help="本次判定用的本族保留天数（默认取 COMPAT_RERUNS_RETENTION_DAYS）",
    )
    parser.add_argument(
        "--purge-rerun-id", action="append", type=int, default=[], dest="purge_ids",
        help="显式回收某笔补跑产物，可重复；不给即纯只读 Dry-run",
    )
    parser.add_argument(
        "--operator", default="", help="回收发起人（写入 audit，缺失即拒绝回收）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非文本报告")
    return parser


def format_report(report: dict[str, Any]) -> str:
    counts = report["counts"]
    lines = [
        f"family_root: {report['family_root']}",
        f"anchor: {report['anchor_days']}d ({report['anchor_source']}) → cutoff {report['cutoff']}",
    ]
    if report.get("anchor_note"):
        lines.append(f"  [anchor warning] {report['anchor_note']}")
    lines.append(
        f"registered={counts['registered']} candidates={counts['candidates']} "
        f"retained={counts['retained']} purged={counts['purged']} "
        f"unprovable_missing_run={counts['unprovable_missing_run']} "
        f"candidate_bytes={counts['candidate_bytes']}"
    )
    lines.append("-- candidates (人工逐笔确认后方可回收) --")
    if not report["candidates"]:
        lines.append("  (none)")
    for item in report["candidates"]:
        lines.append(
            f"  r{item['rerun_id']} run={item['plan_run_id']}/{item['platform']} "
            f"bytes={item['bytes']} files={item['files']} "
            f"key={item['request_key']} dv={item['source_delivery_version']['no']} "
            f"completed={item['completed_at']}"
        )
        lines.append(f"      dir={item['artifact_dir']}")
        lines.append(
            f"      对拍: xls_present={item['merge_xls_present']} "
            f"manifest_present={item['manifest_present']} "
            f"evidence={item['evidence']} "
            f"shared_sources={len(item['shared_sources'])} "
            f"missing_events={len(item['missing_items'])}"
        )
    lines.append("-- retained (逐条理由，不合并) --")
    if not report["retained"]:
        lines.append("  (none)")
    for item in report["retained"]:
        lines.append(
            f"  r{item['rerun_id']} run={item['plan_run_id']}/{item['platform']} "
            f"status={item['status']} reasons={','.join(item['reasons'])}"
        )
    lines.append("-- purged (已回收，不再判定) --")
    if not report["purged"]:
        lines.append("  (none)")
    for item in report["purged"]:
        lines.append(
            f"  r{item['rerun_id']} run={item['plan_run_id']}/{item['platform']} "
            f"purged_at={item['purged_at']}"
        )
    return "\n".join(lines)


def format_purge(result: dict[str, Any]) -> str:
    lines = [
        f"anchor: {result['anchor_days']}d → cutoff {result['cutoff']}",
        f"requested={len(result['requested'])} removed={len(result['removed'])} "
        f"skipped={len(result['skipped'])} failed={len(result['failed'])}",
    ]
    for item in result["removed"]:
        lines.append(
            f"  removed r{item['rerun_id']} run={item['plan_run_id']}/{item['platform']} "
            f"bytes={item['bytes']} files={item['files']} dir={item['artifact_dir']}"
        )
    for item in result["skipped"]:
        lines.append(f"  skipped r{item['rerun_id']} reasons={','.join(item['reasons'])}")
    for item in result["failed"]:
        note = " (目录已删、标记未落，下一次 Dry-run 会以 artifact_absent_or_unsafe 呈现)" \
            if item.get("already_removed") else ""
        lines.append(f"  FAILED r{item['rerun_id']} error={item['error']}{note}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.purge_ids and not args.operator.strip():
        print("refusing to purge: --operator is required (audit needs a human)", file=sys.stderr)
        return 2

    with SessionLocal() as db:
        try:
            if args.purge_ids:
                result = retention.purge_compat_rerun_artifacts(
                    db, rerun_ids=args.purge_ids, operator=args.operator.strip(),
                    retention_days=args.retention_days, log_root=args.log_root,
                )
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
                      if args.json else format_purge(result))
                if result["failed"]:
                    return 1
                reason_hist = Counter(
                    reason for item in result["skipped"] for reason in item["reasons"]
                )
                if reason_hist:
                    print(f"skipped reasons: {dict(reason_hist)}", file=sys.stderr)
                return 0
            report = retention.evaluate_compat_rerun_retention(
                db, retention_days=args.retention_days, log_root=args.log_root,
            )
        except CompatRerunRetentionError as exc:
            print(f"refusing to run: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
              if args.json else format_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
