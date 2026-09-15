#!/usr/bin/env python3
"""中心存储结构的只读基线采集（I-12 / I-13 效果判据的前置）。

背景：`docs/notes/architecture/2026-09-15-center-storage-and-merge-locus-proposal.md`
§1.3 列了五项必须先采的基线；没有基线的「优化」不可判定（同文 §5 明确要求
「先定阈值再落地」）。本脚本负责其中可在**文件系统层**只读获得的部分。

覆盖的指标：

- **E-1 事件目录双份占用**：`devices/` 与 `jira/` 的字节/文件/目录数与重合 run 数；
- **E-1b merge 报表双份**：`dedup/{run}/merge/**` 与 `jira/{run}/merge/**`；
- **E-3 控制面本地 `merge_result/`**：子目录数、字节、最新 mtime（体积与增速代理）。

**不覆盖**（脚本内不猜，需另行采集）：

- E-2 retention 后残留：需要「应当已被清理的 run 清单」（来自控制面 DB）。本脚本只报告
  各 run 在四族中的目录分布，由运维与 DB 对账；
- E-4 merge 端到端耗时：需要日志时间差；
- E-5 历史 JIRA 外链有效率：需要 JIRA 侧采样。

只读保证（本脚本的唯一职责边界）：

- 仅 ``os.walk`` + ``os.stat``，**不创建、不修改、不删除**任何文件；
- ``followlinks=False``：不跟随 symlink，避免越出被测根；
- 不读取任何凭据文件，不打印连接串/主机清单；
- 必须显式给出 ``--center-root``（或环境变量 ``STP_AEE_NFS_ROOT``），并拒绝危险根。

用法::

    python -m backend.scripts.measure_center_storage --center-root /mnt/center
    python -m backend.scripts.measure_center_storage --center-root /mnt/center --json
    python -m backend.scripts.measure_center_storage --center-root /mnt/center --top 20
    python -m backend.scripts.measure_center_storage \
        --center-root /mnt/center --merge-result-root /mnt/tools/merge_result
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Optional

#: 中心存储的四族顶层目录（路径契约见 design/2026-scan-upload-merge-contract.md）。
CENTER_FAMILIES: tuple[str, ...] = ("devices", "dedup", "jira", "jobs")

#: `devices/` 下与 plan_run_id 同级、但不是 run 的保留目录名。
DEVICES_NON_RUN_ENTRIES: frozenset[str] = frozenset({"unassigned"})

#: 拒绝扫描的根（防手滑把整盘/整机当中心存储）。
FORBIDDEN_ROOTS: frozenset[str] = frozenset({"/", "/home", "/tmp", "/var", "/usr"})

#: merge 报表在中心与交付包内的相对子目录名。
MERGE_SUBDIR = "merge"


class UnsafeRootError(ValueError):
    """给定的根不适合作为被测目录（危险根 / 不存在 / 不是目录）。"""


@dataclass(frozen=True)
class Usage:
    """一个目录树的只读用量快照。"""

    bytes: int = 0
    files: int = 0
    dirs: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            bytes=self.bytes + other.bytes,
            files=self.files + other.files,
            dirs=self.dirs + other.dirs,
        )


def walk_usage(root: Path) -> Usage:
    """累计 *root* 下（含自身）的字节/文件/目录数。

    只读：``os.walk`` 默认 ``followlinks=False``，不跟随符号链接目录；单个条目的
    ``stat`` 失败（并发删除、权限、NFS stale handle）跳过而不抛，保证测量不因
    个别坏条目整体失败。
    """
    total_bytes = 0
    total_files = 0
    total_dirs = 0
    if not root.is_dir():
        return Usage()
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        total_dirs += len(dirnames)
        for name in filenames:
            try:
                st = os.stat(os.path.join(current, name))
            except OSError:
                continue
            total_files += 1
            total_bytes += st.st_size
    # 含 root 自身，便于与「一个 run 目录」计数对齐。
    return Usage(bytes=total_bytes, files=total_files, dirs=total_dirs + 1)


def resolve_center_root(raw: str) -> Path:
    """校验并返回中心存储根；危险/无效根抛 :class:`UnsafeRootError`。"""
    text = (raw or "").strip()
    if not text:
        raise UnsafeRootError("center root is empty (pass --center-root or STP_AEE_NFS_ROOT)")
    root = Path(text).expanduser()
    try:
        resolved = root.resolve(strict=False)
    except OSError as exc:
        raise UnsafeRootError(f"cannot resolve center root {text!r}: {exc}") from exc
    if str(resolved) in FORBIDDEN_ROOTS or resolved.parent == resolved:
        raise UnsafeRootError(
            f"refusing to walk {resolved} — pass the center-storage subtree, not the filesystem root"
        )
    if not resolved.is_dir():
        raise UnsafeRootError(f"center root is not a directory: {resolved}")
    return resolved


def _run_dirs(family_root: Path, *, skip: Iterable[str] = ()) -> dict[str, Path]:
    """``{run_id: path}``；不可读目录整体跳过（只读诊断不得因权限中断）。"""
    out: dict[str, Path] = {}
    if not family_root.is_dir():
        return out
    skip_set = {str(s) for s in skip}
    try:
        entries = sorted(family_root.iterdir())
    except OSError:
        return out
    for entry in entries:
        if entry.name in skip_set or entry.name.startswith("."):
            continue
        if entry.is_dir() and not entry.is_symlink():
            out[entry.name] = entry
    return out


def collect_family_usage(center_root: Path) -> dict[str, Usage]:
    """四族各自的顶层用量（不递归到 run 内部分解）。"""
    return {
        family: walk_usage(center_root / family)
        for family in CENTER_FAMILIES
        if (center_root / family).is_dir()
    }


def collect_run_usage(center_root: Path) -> dict[str, dict[str, Usage]]:
    """``{family: {run_id: Usage}}``；``devices/unassigned`` 单独成键。"""
    by_family: dict[str, dict[str, Usage]] = {}
    for family in CENTER_FAMILIES:
        root = center_root / family
        if not root.is_dir():
            continue
        skip = DEVICES_NON_RUN_ENTRIES if family == "devices" else ()
        by_family[family] = {
            run_id: walk_usage(path)
            for run_id, path in _run_dirs(root, skip=skip).items()
        }
    unassigned = center_root / "devices" / "unassigned"
    if unassigned.is_dir():
        by_family.setdefault("devices", {})["unassigned"] = walk_usage(unassigned)
    return by_family


def _merge_subdir_usage(run_path: Path) -> Usage:
    return walk_usage(run_path / MERGE_SUBDIR)


def collect_duplication(
    by_family: dict[str, dict[str, Usage]],
) -> dict[str, dict[str, int | float | list[str]]]:
    """E-1 / E-1b：双份占用与重合 run。

    ``event_dirs`` 统计 ``devices/`` 与 ``jira/`` 的**整目录**字节（它们是同一批事件
    的两份拷贝，见提案 §1.1 事实 1）；``merge_reports`` 统计两者 ``merge/`` 子树
    （事实 2）。重合 run 数用来回答「双份是不是普遍现象」，而不是只看总量。
    """
    devices = by_family.get("devices", {})
    jira = by_family.get("jira", {})
    dedup = by_family.get("dedup", {})

    devices_bytes = sum(u.bytes for k, u in devices.items() if k != "unassigned")
    jira_bytes = sum(u.bytes for u in jira.values())
    overlap = sorted(set(devices) & set(jira) - DEVICES_NON_RUN_ENTRIES)
    return {
        "event_dirs": {
            "devices_bytes": devices_bytes,
            "jira_bytes": jira_bytes,
            "runs_in_devices": sorted(k for k in devices if k != "unassigned"),
            "runs_in_jira": sorted(jira),
            "overlap_runs": overlap,
            "overlap_count": len(overlap),
        },
        "merge_reports": {
            "dedup_bytes": sum(u.bytes for u in dedup.values()),
            "jira_bytes": jira_bytes,
            "note": "merge 报表字节需按 merge/ 子树单独测量（见 merge_subdir_usage）",
        },
    }


def collect_merge_subdir_usage(center_root: Path) -> dict[str, dict[str, int]]:
    """E-1b：``dedup/{run}/merge/**`` 与 ``jira/{run}/merge/**`` 的字节对比。"""
    out: dict[str, dict[str, int]] = {}
    for family in ("dedup", "jira"):
        root = center_root / family
        if not root.is_dir():
            continue
        total = Usage()
        runs_with_merge = 0
        for _run_id, path in _run_dirs(root).items():
            usage = _merge_subdir_usage(path)
            if usage.files:
                runs_with_merge += 1
            total = total + usage
        out[family] = {
            "merge_bytes": total.bytes,
            "merge_files": total.files,
            "runs_with_merge": runs_with_merge,
        }
    return out


def collect_local_merge_result(merge_result_root: Optional[Path]) -> Optional[dict[str, int | str]]:
    """E-3：控制面本地 ``merge_result/`` 体积、子目录数与最新 mtime（增速代理）。"""
    if merge_result_root is None:
        return None
    if not merge_result_root.is_dir():
        return {
            "root": str(merge_result_root),
            "exists": "no",
        }
    subdirs = _run_dirs(merge_result_root, skip={".stp_merge.lock"})
    usage = walk_usage(merge_result_root)
    newest = 0.0
    for path in subdirs.values():
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return {
        "root": str(merge_result_root),
        "exists": "yes",
        "bytes": usage.bytes,
        "files": usage.files,
        "subdirs": len(subdirs),
        "newest_mtime": int(newest) if newest else 0,
    }


def derive_merge_result_root(env: Optional[dict[str, str]] = None) -> Optional[Path]:
    """从 ``STP_BACKEND_DEDUP_SCAN_SCRIPT``（控制面 scan 工具脚本）推导同级 merge_result/。"""
    source = env if env is not None else os.environ
    script = (source.get("STP_BACKEND_DEDUP_SCAN_SCRIPT") or "").strip()
    if not script:
        return None
    return Path(script).expanduser().parent / "merge_result"


def collect_baseline(
    center_root: Path,
    *,
    merge_result_root: Optional[Path] = None,
    top: int = 10,
) -> dict:
    """汇总全部可只读获得的基线指标。"""
    by_family = collect_run_usage(center_root)
    ranked: list[tuple[str, int]] = []
    for family, runs in by_family.items():
        for run_id, usage in runs.items():
            ranked.append((f"{family}/{run_id}", usage.bytes))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return {
        "center_root": str(center_root),
        "families": {
            name: asdict(usage) for name, usage in collect_family_usage(center_root).items()
        },
        "run_counts": {family: len(runs) for family, runs in by_family.items()},
        "duplication": collect_duplication(by_family),
        "merge_subdir_usage": collect_merge_subdir_usage(center_root),
        "local_merge_result": collect_local_merge_result(merge_result_root),
        "top_runs_by_bytes": [
            {"name": name, "bytes": size} for name, size in ranked[: max(0, top)]
        ],
        "not_covered": [
            "E-2 retention 残留：需控制面 DB 的『应已清理 run』清单做对账",
            "E-4 merge 端到端耗时：需日志时间差",
            "E-5 JIRA 外链有效率：需 JIRA 侧采样",
        ],
    }


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TB"


def format_report(report: dict) -> str:
    """把 :func:`collect_baseline` 结果渲染为人类可读文本。"""
    lines: list[str] = [f"center_root: {report['center_root']}"]
    lines.append("-- families --")
    for name, usage in report["families"].items():
        lines.append(
            f"  {name:<8} {_human_bytes(usage['bytes']):>10}  "
            f"files={usage['files']:<7} dirs={usage['dirs']}"
        )
    dup = report["duplication"]["event_dirs"]
    lines.append("-- E-1 event dir duplication --")
    lines.append(
        f"  devices={_human_bytes(dup['devices_bytes'])}  "
        f"jira={_human_bytes(dup['jira_bytes'])}  overlap_runs={dup['overlap_count']}"
    )
    lines.append("-- E-1b merge reports --")
    for family, usage in report["merge_subdir_usage"].items():
        lines.append(
            f"  {family}/{{{MERGE_SUBDIR}}}: {_human_bytes(usage['merge_bytes'])} "
            f"files={usage['merge_files']} runs={usage['runs_with_merge']}"
        )
    local = report["local_merge_result"]
    if local is not None:
        lines.append("-- E-3 local merge_result --")
        if local.get("exists") == "yes":
            lines.append(
                f"  {local['root']}: {_human_bytes(int(local['bytes']))} "
                f"files={local['files']} subdirs={local['subdirs']}"
            )
        else:
            lines.append(f"  {local['root']}: (absent)")
    if report["top_runs_by_bytes"]:
        lines.append(f"-- top {len(report['top_runs_by_bytes'])} runs by bytes --")
        for row in report["top_runs_by_bytes"]:
            lines.append(f"  {_human_bytes(row['bytes']):>10}  {row['name']}")
    lines.append("-- not covered by this script --")
    for item in report["not_covered"]:
        lines.append(f"  - {item}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only baseline measurement for center-storage layout (I-12/I-13).",
    )
    parser.add_argument(
        "--center-root",
        default=os.environ.get("STP_AEE_NFS_ROOT", ""),
        help="中心存储根（默认取 STP_AEE_NFS_ROOT；危险根会被拒绝）",
    )
    parser.add_argument(
        "--merge-result-root",
        default="",
        help="控制面本地 merge_result/（默认从 STP_BACKEND_DEDUP_SCAN_SCRIPT 推导）",
    )
    parser.add_argument("--top", type=int, default=10, help="按字节排行的 run 数（默认 10）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        center_root = resolve_center_root(args.center_root)
    except UnsafeRootError as exc:
        print(f"refusing to run: {exc}", file=sys.stderr)
        return 2

    merge_root = Path(args.merge_result_root).expanduser() if args.merge_result_root.strip() else derive_merge_result_root()
    report = collect_baseline(center_root, merge_result_root=merge_root, top=args.top)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
