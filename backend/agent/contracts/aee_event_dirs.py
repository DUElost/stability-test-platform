"""Event directory naming and discovery (ADR-0025 Sprint 4).

Supports ISO-style (``2026-06-23_14-30-00_db.01``) and compact MTK-style
(``2026_0629_174940_206_db.74.ANR``) basenames.

契约模块（ADR-0054 D1/D2）：控制面与 Agent 共用**同一实现**——Agent 侧经
``from ..contracts.aee_event_dirs import …`` 相对导入，控制面（dedup/extract 链）
经 ``backend.agent.contracts.aee_event_dirs``。目录命名识别规则是双方的匹配键
（DLE 上送标记、scan xls Path 列、watcher 落地路径），两侧判定不得漂移。
模块体只依赖标准库、import 期无 I/O。
"""

from __future__ import annotations

import re
from pathlib import Path

# ISO: 2026-06-23_14-30-00_*  or  2026_06_23_14_30_00_*
# Compact: 2026_0629_174940_206_*  (末段 3 位毫秒；\d{3,6} 兼容异常
# timestamp 走 fallback 时生成的 4-6 位微秒——P1 实证见
# docs/notes/bug-fix/2026-08-30-flash-download-succeeded-but-not-flashed.md
# 附录，正则过严会静默断 DLE 上送标记链)
_EVENT_DIR_BASENAME_RE = re.compile(
    r"^("
    r"\d{4}[-_]\d{2}[-_]\d{2}[_T]\d{2}[-_:]?\d{2}[-_:]?\d{2}"
    r"|\d{4}_\d{4}_\d{6}_\d{3,6}"
    r")_",
)

# #2822：watcher（inotifyd 兜底）经 `_compose_local_path` 落地为
# `<epoch_ms>_<原事件目录名>`——13 位毫秒前缀是**结构化命名**（防同名冲突），
# 不是事件名的一部分。标记链（upload_task→collect_upload_event_dir_names）
# 必须认得这一形态，否则 inotifyd-only 场景下 DLE 永远停在 LOCAL（静默断链，
# #389 同类：正则过严的断链不长嘴）。剥离只用于**识别**；
# `event_dir_basename_from_path` 仍返回带前缀全名——它与 DLE.remote_path 及
# scan xls 的 Path 列同形，是标记匹配键，剥离反而会引入新的不匹配。
_WATCHER_EPOCH_MS_PREFIX_RE = re.compile(r"^\d{13}_")


def is_event_dir_basename(name: str) -> bool:
    """Return True if ``name`` looks like a timestamp-prefixed event directory.

    两种落地形态都算（#2822）：reconciler 原名（`2026_..._db.01.ANR`）与
    watcher 加毫秒前缀（`1789826505754_2026_..._db.01.ANR`）。剥前缀后**剩余段
    必须自己过原判据**——不是「任何 13 位数打头」都放行，避免误收无关目录。
    """
    if not name or name.startswith("."):
        return False
    if _EVENT_DIR_BASENAME_RE.match(name):
        return True
    stripped = _WATCHER_EPOCH_MS_PREFIX_RE.sub("", name, count=1)
    return stripped != name and bool(_EVENT_DIR_BASENAME_RE.match(stripped))


def event_dir_basename_from_path(path: str) -> str | None:
    """Extract event directory basename from a filesystem or device path."""
    cleaned = (path or "").strip().replace("\\", "/")
    if not cleaned:
        return None
    parts = [p for p in cleaned.split("/") if p]
    for part in reversed(parts):
        if part in ("__exp_main.txt", "main.dbg", "ZZ_INTERNAL"):
            continue
        if is_event_dir_basename(part):
            return part
    return None


def is_valid_event_dir(path: Path) -> bool:
    """Heuristic: directory contains AEE event markers."""
    return (
        (path / "ZZ_INTERNAL").is_file()
        or (path / "__exp_main.txt").is_file()
        or (path / "main.dbg").is_file()
    )


def find_event_dir_under_root(
    root: Path,
    dirname: str,
    *,
    max_depth: int = 8,
) -> Path | None:
    """Locate ``{root}/**/{dirname}`` when events live under folder/serial/."""
    if not dirname:
        return None
    direct = root / dirname
    if direct.is_dir() and is_valid_event_dir(direct):
        return direct

    base_depth = len(root.parts)
    matches: list[Path] = []
    try:
        for candidate in root.rglob(dirname):
            if not candidate.is_dir() or candidate.name != dirname:
                continue
            if len(candidate.parts) - base_depth > max_depth:
                continue
            if is_valid_event_dir(candidate):
                matches.append(candidate)
    except OSError:
        return None

    if not matches:
        return None
    return sorted(matches)[0]


__all__ = [
    "event_dir_basename_from_path",
    "find_event_dir_under_root",
    "is_event_dir_basename",
    "is_valid_event_dir",
]
