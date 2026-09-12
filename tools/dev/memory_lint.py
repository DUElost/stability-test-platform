#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""memory-lint：Codebuddy 记忆目录的机械化体检（#1585）。

2026-09-11/12 的三轮 memory 腐化点人工扫描（15 处免疫 / 7 类形态）中，四类
完全可机械化；本工具把它们固化，人工扫描只保留语义性检查（文件内/跨文件
矛盾、能力新增同步等）。

检查项：

- **结构（ERROR）**：非索引文件 frontmatter 完整（name/description/type），
  type ∈ user / feedback / project / reference；
- **索引（ERROR）**：`MEMORY.md` 无 frontmatter、≤200 行、每行 ≤150 字符；
  索引 ↔ 文件**双向**一致（死链 / 未索引文件都算）；
- **断链**：正文反引号引用的路径存在性——仓库相对路径缺失 = ERROR；
  `~` / 绝对路径缺失 = WARN（工具升级/环境迁移可能合法）；
- **可疑绝对断言（WARN）**：`不会跑 / 不会重跑 / 完全不跑 / 零容忍 / 永远`、
  裸计数 `存量 **N 个**` 等未限定表述——提示改为「条件自证 / 现查为准」。

只读：本工具绝不修改任何 memory 文件。

用法::

    python tools/dev/memory_lint.py                  # 本项目默认 memory 目录
    python tools/dev/memory_lint.py --path <dir>     # 指定目录
    python tools/dev/memory_lint.py --strict         # 有 WARN 也退出 1
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

INDEX_NAME = "MEMORY.md"
ALLOWED_TYPES = {"user", "feedback", "project", "reference"}
INDEX_MAX_LINES = 200
INDEX_LINE_MAX_CHARS = 150

# 可疑绝对断言（WARN 级）：未限定表述与可腐化的裸计数
SUSPICIOUS_PATTERNS: list[tuple[str, str]] = [
    (r"不会跑", "「不会跑」应改为条件自证（如：看该 job 的 if 条件）"),
    (r"不会重跑", "「不会重跑」应改为「不保证重跑 + 先等一个队列周期」"),
    (r"完全不跑", "「完全不跑」应加时间/范围限定"),
    (r"零容忍", "「零容忍」应写明具体规则集（会随配置变）"),
    (r"永远是", "「永远是」应改为纪律表述"),
    (r"存量\s*\*\*?\d+\s*个\*\*?", "裸计数应改为「以 … 现查为准（日期快照为 N）」"),
    (r"每\s*\d+\s*天", "周期数字应指向权威来源（如台账正文）"),
]

_BACKTICK_RE = re.compile(r"`([^`\n]+)`")

# 命令式反引号（如 `python tools/dev/x.py`）：拆词后只检查路径形态的部分，
# 跳过 flag（-x/--flag）与命令词本身。
def _candidate_paths(token: str) -> list[str]:
    return [
        part
        for part in token.strip().split()
        if part and not part.startswith("-") and _is_candidate_path(part)
    ]
_INDEX_LINK_RE = re.compile(r"\]\(([^)\s]+\.md)\)")
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)

# 反引号内含占位符/通配的引用跳过（无法做存在性判定）
_PLACEHOLDER_MARKERS = ("...", "<", ">", "*", "…", "$")

# 候选路径：含 "/" 且形似路径，或带已知仓库前缀
_REPO_PREFIXES = (
    "docs/", "tools/", "scripts/", "tests/", "backend/", "frontend/",
    ".github/", ".claude/", "src/",
)
_PATH_SUFFIXES = (
    ".md", ".py", ".yml", ".yaml", ".ts", ".tsx", ".sh", ".json", ".toml",
)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def default_memory_dir(repo_root: Path) -> Path:
    """本项目在 Codebuddy 下的记忆目录约定：~/.codebuddy/projects/<slug>/memory。"""
    slug = str(repo_root.resolve()).lstrip("/").replace("/", "-")
    return Path.home() / ".codebuddy" / "projects" / slug / "memory"


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """解析简易 frontmatter；无 frontmatter 返回 None。"""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    fields: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def _is_candidate_path(token: str) -> bool:
    if not token or "/" not in token:
        return False
    if any(marker in token for marker in _PLACEHOLDER_MARKERS):
        return False
    if token.startswith(("http://", "https://")):
        return False
    # 绝对/~ 路径形态无歧义，总是候选（不要求扩展名：如 .venv/bin/python）
    if token.startswith(("/", "~/")):
        return True
    if token.startswith(_REPO_PREFIXES):
        return True
    return token.endswith(_PATH_SUFFIXES)


def lint_memory_dir(memory_dir: Path, *, repo_root: Path) -> Report:
    report = Report()
    if not memory_dir.is_dir():
        report.error(f"memory 目录不存在: {memory_dir}")
        return report

    files = sorted(p for p in memory_dir.glob("*.md"))
    index_path = memory_dir / INDEX_NAME
    if index_path not in files:
        report.error(f"缺少索引文件 {INDEX_NAME}")
        return report

    # ── 结构：frontmatter ────────────────────────────────────────────────
    for path in files:
        text = path.read_text(encoding="utf-8")
        if path.name == INDEX_NAME:
            if parse_frontmatter(text) is not None:
                report.error(f"{path.name}: 索引文件不应有 frontmatter")
            continue
        fm = parse_frontmatter(text)
        if fm is None:
            report.error(f"{path.name}: 缺 frontmatter（name/description/type）")
            continue
        for key in ("name", "description", "type"):
            if not fm.get(key):
                report.error(f"{path.name}: frontmatter 缺 {key}")
        if fm.get("type") and fm["type"] not in ALLOWED_TYPES:
            report.error(
                f"{path.name}: type={fm['type']!r} 不在 {sorted(ALLOWED_TYPES)}"
            )

    # ── 索引：行数/行宽/双向一致 ─────────────────────────────────────────
    index_text = index_path.read_text(encoding="utf-8")
    index_lines = index_text.splitlines()
    if len(index_lines) > INDEX_MAX_LINES:
        report.error(
            f"{INDEX_NAME}: {len(index_lines)} 行 > {INDEX_MAX_LINES}（超出会被截断）"
        )
    for lineno, line in enumerate(index_lines, start=1):
        if len(line) > INDEX_LINE_MAX_CHARS:
            report.error(
                f"{INDEX_NAME}:{lineno}: 行宽 {len(line)} > {INDEX_LINE_MAX_CHARS}"
            )

    linked = set()
    for target in _INDEX_LINK_RE.findall(index_text):
        linked.add(target)
        if not (memory_dir / target).is_file():
            report.error(f"{INDEX_NAME}: 死链 -> {target}")
    expected = {p.name for p in files if p.name != INDEX_NAME}
    for missing in sorted(expected - linked):
        report.error(f"{INDEX_NAME}: 未索引文件 {missing}")

    # ── 断链：正文反引号路径 ─────────────────────────────────────────────
    for path in files:
        text = path.read_text(encoding="utf-8")
        for token in _BACKTICK_RE.findall(text):
            for candidate in _candidate_paths(token):
                if candidate.startswith(("~", "/")):
                    resolved = Path(candidate).expanduser()
                    if not resolved.exists():
                        report.warn(
                            f"{path.name}: 绝对路径引用不存在（环境可能迁移）: "
                            f"`{candidate}`"
                        )
                    continue
                if not (repo_root / candidate).exists():
                    report.error(
                        f"{path.name}: 断链（仓库内不存在）: `{candidate}`"
                    )

    # ── 可疑绝对断言（WARN）──────────────────────────────────────────────
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern, advice in SUSPICIOUS_PATTERNS:
            for m in re.finditer(pattern, text):
                snippet = m.group(0)
                report.warn(f"{path.name}: 可疑表述 {snippet!r} —— {advice}")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="memory 目录机械化体检（只读）")
    parser.add_argument("--path", default=None, help="memory 目录（默认本项目约定路径）")
    parser.add_argument(
        "--repo-root", default=None, help="仓库根（校验断链用，默认当前目录）",
    )
    parser.add_argument("--strict", action="store_true", help="有 WARN 也退出 1")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd()
    memory_dir = (
        Path(args.path).expanduser().resolve()
        if args.path
        else default_memory_dir(repo_root)
    )

    report = lint_memory_dir(memory_dir, repo_root=repo_root)

    print(f"memory-lint: {memory_dir}")
    for msg in report.errors:
        print(f"  [ERROR] {msg}")
    for msg in report.warnings:
        print(f"  [WARN ] {msg}")
    print(
        f"结果：{len(report.errors)} error / {len(report.warnings)} warn"
    )

    if report.errors:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
