#!/usr/bin/env python3
"""中心存储 ``devices/`` 跨 run 字节相同文件改硬链接（过渡止血，按需运行）。

**过渡项** ``center-storage-interim-dedup``（``docs/governance/transitions.json``），出口 ADR-0053
Phase B：scan / merge / extract / 下载 / 回收切到对象解析边界后，本脚本随该过渡项一并删除。

**何时跑**：按需。host-storage 磁盘水位告警（``StabilityHostFilesystemFillingUp`` / ``LowSpace`` /
``Critical``）的描述指向本工具；步骤见 ``docs/operations/center-storage-hardlink-dedup.md``。

**为什么有效**：baseline 让每个 job 首轮补拉设备上的历史 AEE 转储，同一事件在多个 run 的
``devices/`` 下各存一整份。2026-09-25 首次全量：109 个已结束 run 中 74% 的字节是跨 run 重复，
改链接后释放 389.1 GB（#3308）。改的是存储方式而不是内容：每个 run 的目录与文件原样都在，只是
共用磁盘块；retention 删除某个 run 只去掉它那一个链接，最后一个链接删除时才释放空间——per-run
语义不变。

**安全约束**（与过渡项登记一致）：

- 只处理已终态（SUCCESS / PARTIAL_SUCCESS / FAILED）且结束超过 ``--min-run-age-hours`` 的 run，
  运行中的 run 永不触碰；
- 只处理 mtime 早于 ``--min-file-age-hours``、不小于 ``--min-size`` 的普通文件；不跟随软链；
  只在同一文件系统内；
- 大小分组 → 首尾 64 KiB 快速哈希 → 全量 SHA-256，三级都相同才视为重复；
- 替换前重新 ``lstat``（inode / 大小 / mtime 未变），``os.link`` 到同目录临时名再 ``os.replace``
  原子替换，任一步失败即放弃该文件；
- 缺省 dry-run，``--execute`` 才改动；中途中断可重跑（已同 inode 的跳过，残留临时链接先清理）；
- 根目录必须叫 ``devices``：不触碰 ``jira/``（厂商 Jira 工具是否原地改文件未证实，ADR-0053 D5）。

**成立前提**：上送路径是「复制 → 校验 → REMOTE」，写完不再修改；重传先 ``rmtree``；回收与
unassigned 归属只做 unlink / rename——共享 inode 因而不会被原地改写。这靠约定而非机制保证，与
ADR-0053 D2「不能仅靠约定保护共享 inode」存在张力，这正是它登记为过渡项的原因。

用法（发布根下、env 已注入；``--eligible-from-db`` 只读查库）::

    venv/bin/python -m backend.scripts.center_storage_hardlink_dedup --eligible-from-db            # dry-run
    venv/bin/python -m backend.scripts.center_storage_hardlink_dedup --eligible-from-db --execute  # 改动

逐文件 ``LINKED`` 行写 stdout，汇总行写 stderr。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, NamedTuple, Optional

from sqlalchemy import bindparam, create_engine, text

from backend.core.database import normalize_sync_database_url

DEFAULT_ROOT = "/mnt/stp-aee/devices"
TMP_SUFFIX = ".stp-dedup-tmp"
EDGE = 64 * 1024
#: 已终态的 PlanRun（``backend.models.enums.PlanRunStatus``）；RUNNING / QUEUED / PRECHECK 均不在内。
TERMINAL_PLAN_RUN_STATUSES = ("SUCCESS", "PARTIAL_SUCCESS", "FAILED")
# 列是枚举类型：转成文本再比，免得字符串参数与枚举比较时依赖驱动的类型推断。
_ELIGIBLE_SQL = (
    "SELECT id FROM plan_run WHERE CAST(status AS TEXT) IN :statuses "
    "AND ended_at IS NOT NULL AND ended_at < :cutoff ORDER BY id"
)


class Candidate(NamedTuple):
    run: int
    path: str
    st: os.stat_result


@dataclass
class DedupSummary:
    runs: int = 0
    dup_groups: int = 0
    linked: int = 0
    freed_bytes: int = 0
    skipped: int = 0
    stale_tmp: int = 0

    def line(self, execute: bool) -> str:
        mode = "EXECUTED" if execute else "DRY-RUN"
        return (
            f"== {mode}: runs={self.runs} dup_groups={self.dup_groups} files_linked={self.linked} "
            f"freed={self.freed_bytes / 1e9:.1f} GB skipped={self.skipped} stale_tmp={self.stale_tmp}"
        )


def quick_hash(path: str, size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read(EDGE))
        if size > 2 * EDGE:
            f.seek(size - EDGE)
            h.update(f.read(EDGE))
    return h.hexdigest()


def full_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_candidates(
    root: Path, runs: Iterable[int], *, min_size: int, mtime_cutoff: float, execute: bool,
) -> tuple[dict[int, list[Candidate]], int]:
    """按大小分桶收集候选文件；顺带清理上次中断残留的临时链接（dry-run 只计数）。"""
    root_dev = root.stat().st_dev
    by_size: dict[int, list[Candidate]] = defaultdict(list)
    stale_tmp = 0
    for run in sorted(set(runs)):
        base = root / str(run)
        if base.is_symlink() or not base.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(base, followlinks=False):
            for name in filenames:
                p = os.path.join(dirpath, name)
                if name.endswith(TMP_SUFFIX):
                    if execute:
                        os.unlink(p)
                    stale_tmp += 1
                    continue
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode):  # 软链、设备文件等一律不碰
                    continue
                if st.st_dev != root_dev or st.st_size < min_size or st.st_mtime > mtime_cutoff:
                    continue
                by_size[st.st_size].append(Candidate(run, p, st))
    return by_size, stale_tmp


def _distinct_inodes(items: Iterable[Candidate]) -> int:
    return len({it.st.st_ino for it in items})


def find_duplicate_groups(by_size: dict[int, list[Candidate]]) -> list[list[Candidate]]:
    """大小 → 快速哈希 → 全量 SHA-256 三级分组；只对仍跨多个 inode 的组继续往下算。"""
    groups: list[list[Candidate]] = []
    for size, items in by_size.items():
        if _distinct_inodes(items) < 2:
            continue
        by_quick: dict[str, list[Candidate]] = defaultdict(list)
        for it in items:
            try:
                by_quick[quick_hash(it.path, size)].append(it)
            except OSError:
                continue
        for q_items in by_quick.values():
            if _distinct_inodes(q_items) < 2:
                continue
            by_full: dict[str, list[Candidate]] = defaultdict(list)
            for it in q_items:
                try:
                    by_full[full_hash(it.path)].append(it)
                except OSError:
                    continue
            groups.extend(g for g in by_full.values() if _distinct_inodes(g) >= 2)
    return groups


def link_groups(
    groups: list[list[Candidate]], *, execute: bool, out: Callable[[str], None] = print,
) -> DedupSummary:
    """每组以 (run, path) 最小者为 canonical，其余不同 inode 的文件改为指向它的硬链接。"""
    summary = DedupSummary(dup_groups=len(groups))
    links_left: dict[int, int] = {}  # dry-run 模拟各 inode 剩余链接数，估算真正释放的字节
    for group in groups:
        group = sorted(group, key=lambda it: (it.run, it.path))
        canon = group[0]
        for it in group[1:]:
            if it.st.st_ino == canon.st.st_ino:
                continue
            if not execute:
                left = links_left.get(it.st.st_ino, it.st.st_nlink) - 1
                links_left[it.st.st_ino] = left
                summary.linked += 1
                summary.freed_bytes += it.st.st_size if left == 0 else 0
                continue
            try:
                now_st = os.lstat(it.path)
                c_st = os.lstat(canon.path)
            except OSError:
                summary.skipped += 1
                continue
            # #3385：两侧同档复核——canonical 缺 mtime 时，「哈希完成后、链接执行前」
            # 被同尺寸原地改写的 canonical 会带着旧内容链满全组（不可恢复的内容丢失）。
            if (now_st.st_ino, now_st.st_size, now_st.st_mtime) != (it.st.st_ino, it.st.st_size, it.st.st_mtime) \
                    or (c_st.st_ino, c_st.st_size, c_st.st_mtime) != (canon.st.st_ino, canon.st.st_size, canon.st.st_mtime):
                summary.skipped += 1  # 扫描之后被改动过：放弃，不冒险
                continue
            tmp = it.path + TMP_SUFFIX
            try:
                os.link(canon.path, tmp)
                os.replace(tmp, it.path)
            except OSError as exc:
                summary.skipped += 1
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                print(f"SKIP\t{it.path}\t{exc}", file=sys.stderr)
                continue
            summary.linked += 1
            summary.freed_bytes += it.st.st_size if now_st.st_nlink == 1 else 0
            out(f"LINKED\t{it.path}\t<=\t{canon.path}\t{it.st.st_size}")
    return summary


def dedup(
    root: Path,
    runs: Iterable[int],
    *,
    min_size: int = EDGE,
    min_file_age_hours: float = 24.0,
    execute: bool = False,
    now: Optional[float] = None,
    out: Callable[[str], None] = print,
) -> DedupSummary:
    if root.name != "devices":
        raise ValueError(f"根目录必须是 devices/（本过渡项只覆盖 devices 族，不碰 jira/）：{root}")
    run_ids = sorted({int(r) for r in runs})
    cutoff = (time.time() if now is None else now) - min_file_age_hours * 3600
    by_size, stale_tmp = collect_candidates(
        root, run_ids, min_size=min_size, mtime_cutoff=cutoff, execute=execute,
    )
    summary = link_groups(find_duplicate_groups(by_size), execute=execute, out=out)
    summary.runs = len(run_ids)
    summary.stale_tmp = stale_tmp
    return summary


def eligible_runs_from_db(conn, *, min_run_age_hours: float, now: Optional[datetime] = None) -> list[int]:
    """已终态且结束超过 ``min_run_age_hours`` 的 run id（只读 SELECT）。"""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=min_run_age_hours)
    stmt = text(_ELIGIBLE_SQL).bindparams(bindparam("statuses", expanding=True))
    rows = conn.execute(stmt, {"statuses": list(TERMINAL_PLAN_RUN_STATUSES), "cutoff": cutoff})
    return [int(r[0]) for r in rows]


def open_readonly_engine(database_url: str):
    """整条连接只读（``default_transaction_read_only``），查询写错也改不了库。"""
    return create_engine(
        normalize_sync_database_url(database_url),
        connect_args={"options": "-c default_transaction_read_only=on"},
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--root", default=DEFAULT_ROOT, help="devices 族根目录")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--runs-file", help="JSON 数组：允许处理的 run id（调用方保证均已终态）")
    src.add_argument("--eligible-from-db", action="store_true",
                     help="从 DATABASE_URL 只读查询已终态且结束超过 --min-run-age-hours 的 run")
    ap.add_argument("--min-run-age-hours", type=float, default=24.0)
    ap.add_argument("--min-size", type=int, default=EDGE, help="小于此字节数的文件不处理")
    ap.add_argument("--min-file-age-hours", type=float, default=24.0)
    ap.add_argument("--execute", action="store_true", help="实际改为硬链接（缺省只统计）")
    args = ap.parse_args(argv)

    root = Path(args.root)
    if args.eligible_from_db:
        database_url = os.environ.get("DATABASE_URL", "")
        if not database_url:
            print("DATABASE_URL 未设置：先注入发布根的 .env.backend", file=sys.stderr)
            return 2
        engine = open_readonly_engine(database_url)
        try:
            with engine.connect() as conn:
                runs = eligible_runs_from_db(conn, min_run_age_hours=args.min_run_age_hours)
        finally:
            engine.dispose()
    else:
        runs = [int(r) for r in json.loads(Path(args.runs_file).read_text(encoding="utf-8"))]

    try:
        summary = dedup(
            root, runs, min_size=args.min_size, min_file_age_hours=args.min_file_age_hours,
            execute=args.execute,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(summary.line(args.execute), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
