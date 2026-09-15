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
- **索引预算（#2156，`--budget`）**：索引字节数相对三级阈值（目标 18.0 / 软触发 19.5 /
  硬墙 24.4 KB）的位置——索引是被 harness 每请求**整份注入**的热路径，超硬墙只加载一部分
  （静默丢条目），故超软触发即 exit 1；
- **索引压缩（#2156，`--fix`/`--apply`）**：行宽超限的索引行做**无损**压缩——原文逐字迁入
  目标条目文件的「原索引条目」块（幂等），索引行压成 ≤150 字符的指针；
- **断链**：正文反引号引用的路径存在性——仓库相对路径缺失 = ERROR；
  `~` / 绝对路径缺失 = WARN（工具升级/环境迁移可能合法）；
- **记忆目录**：不在仓库内，但各 harness 约定必须单点权威——`memory_dir_candidates()`
  逐个探测，找不到即显式报错（不静默指向另一个 store）：
  CodeBuddy `~/.codebuddy/projects/<path-slug>/memory`（slug = 仓库绝对路径去前导 `/`
  后把 `/` 换成 `-`）；ZCode `~/.zcode/cli/memories/projects/<project-id>/memory`
  （`<project-id>` = `<仓库目录名>-<id>`，**不是** path-slug）。**多个 harness 同时在用
  是常态**（本机 CodeBuddy 与 ZCode 两份都在写）——命中多个时同样不静默选第一个：
  报错、列出在用目录并要求 `--harness` 指定（#2065）。新增/变更约定时同步本函数与
  `docs/development/ai/harness-adapters.md` 的指针。
- **可疑绝对断言（WARN）**：`不会跑 / 不会重跑 / 完全不跑 / 零容忍 / 永远`、
  裸计数 `存量 **N 个**` 等未限定表述——提示改为「条件自证 / 现查为准」。

默认**只读**：唯一的写路径是 `--fix --apply`（#2156），且只做无损迁移——先把原索引行
**逐字**追加进目标条目文件，再压缩索引行。语义级摘要（决定哪些记忆该留）不在工具职责内。

用法::

    python tools/dev/memory_lint.py                  # 本项目默认 memory 目录
    python tools/dev/memory_lint.py --harness zcode  # 多命中时报错，指定其一
    python tools/dev/memory_lint.py --path <dir>     # 指定目录
    python tools/dev/memory_lint.py --strict         # 有 WARN 也退出 1
    python tools/dev/memory_lint.py --harness zcode --budget      # 预算判定（超软触发 exit 1）
    python tools/dev/memory_lint.py --harness zcode --fix         # 打印压缩方案（不写盘）
    python tools/dev/memory_lint.py --harness zcode --fix --apply  # 执行无损压缩
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path

INDEX_NAME = "MEMORY.md"
ALLOWED_TYPES = {"user", "feedback", "project", "reference"}
INDEX_MAX_LINES = 200
INDEX_LINE_MAX_CHARS = 150

#: 索引预算（#2156）。索引被 harness 每请求**整份注入**，超硬墙只加载一部分（静默丢条目），
#: 故三级阈值：目标（该维持的位置）/ 软触发（该归档一轮）/ 硬墙（harness 侧实测上限）。
#: 体积按 **UTF-8 字节**计（与 harness 的报数口径一致，且是保守侧）。
INDEX_BUDGET_TARGET_KB = 18.0
INDEX_BUDGET_SOFT_KB = 19.5
INDEX_BUDGET_HARD_KB = 24.4

#: 无损压缩时，原文迁入条目文件的附录块标题（与 2026-09-15 手工迁入的块同形）。
APPENDIX_MARKER = "## 原索引条目"

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
# #2065：`{a,b}` brace glob（如 `backend/agent/{,aee/}CLAUDE.md`）同样无法判定。
_PLACEHOLDER_MARKERS = ("...", "<", ">", "*", "…", "$", "{", "}")

#: 代码位置后缀（#2065）：`path:123` / `path:123-456` / `path#L12`，以及**多位置列表**
#: `path:303,331` / `path:75/134/159` / `path:14,117,122-126`（变更审计类笔记里最常见）。
#: 这些是 AGENTS.md 推荐的引用形式（`file_path:line_number`），判存在性前必须剥离——
#: 否则每条 `path:line` 都会被报成断链（实测占当时全部断链告警的 181/213）。
_LOCATION_NUM = r"\d+(?:-\d+)?"  # `12` / `12-14`
_LOCATION_ANCHOR = r"\d+(?:-L?\d+)?"  # `#L` 后允许 `12` / `12-14` / `12-L14`
_LOCATION_SEP = r"[,\/]:?"  # `,` / `/` / `/:`（审计笔记里的混写）
_LOCATION_SUFFIX_RE = re.compile(
    r"(?::" + _LOCATION_NUM + rf"(?:{_LOCATION_SEP}{_LOCATION_NUM})*"
    + r"|#L" + _LOCATION_ANCHOR + rf"(?:{_LOCATION_SEP}{_LOCATION_NUM})*)$"
)


def _strip_location_suffix(candidate: str) -> str:
    """剥掉位置后缀（`:行号`/`:行号-行号`/`#L行号` 及其 `,`/`/` 列表），
    返回仍可用于存在性判定的路径（#2065）。"""
    return _LOCATION_SUFFIX_RE.sub("", candidate)

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


def _zcode_memory_dirs(repo_root: Path) -> list[Path]:
    """ZCode 的记忆目录（#2065）：~/.zcode/cli/memories/projects/<project-id>/memory。

    ZCode 的目录名是 ``<仓库目录名>-<id>``（不是 path-slug）——与 CodeBuddy **不同根、
    不同命名规则**，故不能靠改 slug 单点覆盖（#1585 的 Revisit 只预设了改名/迁移）。
    """
    base = Path.home() / ".zcode" / "cli" / "memories" / "projects"
    if not base.is_dir():
        return []
    name = repo_root.resolve().name
    return sorted(
        p / "memory"
        for p in base.iterdir()
        if p.is_dir() and (p.name == name or p.name.startswith(f"{name}-"))
    )


def memory_dir_candidates(repo_root: Path) -> list[tuple[str, Path]]:
    """(harness, 记忆目录) 候选表——按 harness 约定逐个试（#2065）。"""
    candidates: list[tuple[str, Path]] = [("codebuddy", default_memory_dir(repo_root))]
    candidates += [("zcode", p) for p in _zcode_memory_dirs(repo_root)]
    return candidates


def existing_memory_dirs(repo_root: Path) -> list[tuple[str, Path]]:
    """**实际存在**的记忆目录（顺序同候选表；可能多个 harness 同时在用）。"""
    return [(h, p) for h, p in memory_dir_candidates(repo_root) if p.is_dir()]


def resolve_memory_dir(
    repo_root: Path, harness: str | None = None,
) -> tuple[str, Path] | None:
    """定位**唯一**在用的记忆目录；0 个或 ≥2 个命中都返回 None（调用方须显式报错）。

    ``harness`` 给定时只在同名候选里找。两个理由不静默回落（#2065）：① 默认目标指向
    CodeBuddy，而当前会话可能跑在 ZCode 上；② 多个 harness 同时在写是常态（本机
    CodeBuddy 与 ZCode 两份都在）——静默取第一个，得出的结论与「到底几个 store 在用」
    无关，比报错更难发现。
    """
    matches = [
        (name, path) for name, path in existing_memory_dirs(repo_root)
        if harness is None or name == harness
    ]
    return matches[0] if len(matches) == 1 else None


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
            for raw_candidate in _candidate_paths(token):
                candidate = _strip_location_suffix(raw_candidate)
                if not candidate:
                    continue
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


@dataclass
class IndexBudget:
    """索引体积相对三级阈值的位置（#2156）。"""

    entries: int
    size_bytes: int
    target_kb: float = INDEX_BUDGET_TARGET_KB
    soft_kb: float = INDEX_BUDGET_SOFT_KB
    hard_kb: float = INDEX_BUDGET_HARD_KB

    @property
    def size_kb(self) -> float:
        return self.size_bytes / 1024

    @property
    def level(self) -> str:
        """``hard`` / ``soft`` / ``ok``——超软触发即需归档一轮。"""
        if self.size_kb > self.hard_kb:
            return "hard"
        if self.size_kb > self.soft_kb:
            return "soft"
        return "ok"

    def describe(self) -> str:
        if self.level == "hard":
            tail = (
                f"超硬墙 {self.hard_kb} KB——超出部分不会被加载（静默丢条目），必须归档一轮"
            )
        elif self.level == "soft":
            tail = f"超软触发 {self.soft_kb} KB——该归档一轮"
        else:
            tail = f"预算内（目标 {self.target_kb} KB）"
        return f"{self.entries} 条 / {self.size_kb:.1f} KB — {tail}"


def index_budget(
    memory_dir: Path, *,
    target_kb: float = INDEX_BUDGET_TARGET_KB,
    soft_kb: float = INDEX_BUDGET_SOFT_KB,
    hard_kb: float = INDEX_BUDGET_HARD_KB,
) -> IndexBudget:
    """量索引体积与条数（只读）。体积口径＝ UTF-8 字节，与 harness 报数一致。"""
    text = (memory_dir / INDEX_NAME).read_text(encoding="utf-8")
    entries = sum(1 for line in text.splitlines() if line.startswith("- ["))
    return IndexBudget(
        entries=entries, size_bytes=len(text.encode("utf-8")),
        target_kb=target_kb, soft_kb=soft_kb, hard_kb=hard_kb,
    )


@dataclass
class IndexLineFix:
    """一条待压缩的索引行；``skipped`` 非空＝不动它（原因如实记录）。"""

    lineno: int
    target: str
    before: str
    after: str
    skipped: str = ""

    def describe(self) -> str:
        if self.skipped:
            return f"[SKIP] {INDEX_NAME}:{self.lineno}: {self.skipped}"
        return (
            f"[FIX ] {INDEX_NAME}:{self.lineno}: 行宽 {len(self.before)} → "
            f"{len(self.after)}（原文迁入 {self.target}）"
        )


#: 索引行形态：`- [标题](file.md) — hook`
_INDEX_LINE_RE = re.compile(
    r"^- \[(?P<title>[^\]]+)\]\((?P<target>[^)\s]+)\)(?P<sep>\s*—\s*|\s*-\s*)?(?P<hook>.*)$"
)


def compress_index_line(
    line: str, *, width: int = INDEX_LINE_MAX_CHARS,
) -> tuple[str, str]:
    """把超宽索引行压成 ≤``width`` 的指针：保留前缀 + 首个分句，截断以 ``…`` 收尾。

    返回 ``(压缩后的行, 跳过原因)``——两者互斥，跳过原因非空时第一个元素为空串。
    只做**机械截断**，不做语义摘要：不判断哪句重要，原文一律先迁入条目文件。
    """
    m = _INDEX_LINE_RE.match(line)
    if m is None:
        return "", "不是 `- [标题](file.md) — hook` 形态，无法定位归属文件"
    prefix = f"- [{m.group('title')}]({m.group('target')}) — "
    hook = m.group("hook").strip()
    if not hook:
        return "", "索引行没有 hook 文本（只有链接，没有可压缩内容）"
    room = width - len(prefix) - 1  # 留 1 字符给省略号
    if room < 1:
        return "", f"前缀已占 {len(prefix)} 字符，没有压缩空间（须手工处置标题/文件名）"
    compact = hook[:room].rstrip()
    for sep in ("；", "。"):  # 优先在分句边界断开，读起来仍是一句话
        idx = hook.find(sep)
        if 0 < idx <= room:
            compact = hook[:idx]
            break
    return prefix + compact + ("…" if compact != hook else ""), ""


def plan_index_fixes(
    memory_dir: Path, *, width: int = INDEX_LINE_MAX_CHARS,
) -> list[IndexLineFix]:
    """列出所有行宽超限的索引行及其压缩方案（只读，不写盘）。"""
    index_path = memory_dir / INDEX_NAME
    plans: list[IndexLineFix] = []
    for lineno, line in enumerate(
        index_path.read_text(encoding="utf-8").splitlines(), start=1,
    ):
        if len(line) <= width:
            continue
        m = _INDEX_LINE_RE.match(line)
        target = m.group("target") if m else ""
        after, why = compress_index_line(line, width=width)
        if not why and not (memory_dir / target).is_file():
            why = f"目标文件不存在：{target}（原文无处可迁，须手工处置）"
        plans.append(
            IndexLineFix(lineno=lineno, target=target, before=line, after=after, skipped=why)
        )
    return plans


def apply_index_fixes(
    memory_dir: Path, plans: list[IndexLineFix], *, today: str | None = None,
) -> tuple[int, list[str]]:
    """执行无损压缩：**先**把原索引行逐字迁入目标文件附录块，**再**改写索引行。

    幂等：原文已在目标文件里就不再重复追加（二次运行只补索引行）。返回 (改写行数, 说明)。
    """
    index_path = memory_dir / INDEX_NAME
    lines = index_path.read_text(encoding="utf-8").splitlines()
    day = today or dt.date.today().isoformat()
    applied: list[IndexLineFix] = []
    notes: list[str] = []
    for plan in plans:
        if plan.skipped:
            continue
        path = memory_dir / plan.target
        text = path.read_text(encoding="utf-8")
        if plan.before not in text:
            block = (
                f"\n<!-- {day} 索引精简（#2156）：以下为本条目原在 {INDEX_NAME} 索引中的原文，"
                f"因索引行宽超限被迁入本文件（索引已压缩为一行指针）。正文若已覆盖，可删除本块。 -->\n"
                f"{APPENDIX_MARKER}（{day} 由 {INDEX_NAME} 迁入）\n\n{plan.before}\n"
            )
            path.write_text(text.rstrip("\n") + "\n" + block, encoding="utf-8")
        lines[plan.lineno - 1] = plan.after
        applied.append(plan)
        notes.append(f"{plan.target}: 行宽 {len(plan.before)} → {len(plan.after)}，原文已迁入")
    if applied:
        index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(applied), notes


def _report_no_unique_dir(repo_root: Path, *, harness: str | None) -> int:
    """没有**唯一**在用目录时显式报错（#2065）——不静默拿一个 store 的结论当全局。"""
    in_use = existing_memory_dirs(repo_root)
    if not in_use:
        print("memory-lint: 找不到本项目的记忆目录（下列候选都不存在）：")
        for name, candidate in memory_dir_candidates(repo_root):
            print(f"  - {name}: {candidate}")
        print("请用 --path 显式指定（不静默指向另一个 harness 的 store）——见 #2065")
        return 2
    if harness is None:
        print(
            f"memory-lint: 有 {len(in_use)} 个 harness 的记忆目录在用，"
            "须用 --harness 指定其一（不静默选第一个）："
        )
    else:
        print(f"memory-lint: --harness={harness} 没有对应的在用目录，在用：")
    for name, candidate in in_use:
        print(f"  - {name}: {candidate}")
    print("——见 #2065（多个 store 同时在写时不静默选第一个）")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="memory 目录机械化体检（只读）")
    parser.add_argument("--path", default=None, help="memory 目录（默认本项目约定路径）")
    parser.add_argument(
        "--harness", default=None,
        help="多个 harness 在用时的选择（候选见 memory_dir_candidates）",
    )
    parser.add_argument(
        "--repo-root", default=None, help="仓库根（校验断链用，默认当前目录）",
    )
    parser.add_argument("--strict", action="store_true", help="有 WARN 也退出 1")
    parser.add_argument(
        "--budget", action="store_true",
        help="索引预算判定（目标/软触发/硬墙；超软触发即 exit 1）",
    )
    parser.add_argument(
        "--target-kb", type=float, default=INDEX_BUDGET_TARGET_KB,
        help=f"预算目标 KB（默认 {INDEX_BUDGET_TARGET_KB}）",
    )
    parser.add_argument(
        "--soft-kb", type=float, default=INDEX_BUDGET_SOFT_KB,
        help=f"软触发 KB（默认 {INDEX_BUDGET_SOFT_KB}）",
    )
    parser.add_argument(
        "--hard-kb", type=float, default=INDEX_BUDGET_HARD_KB,
        help=f"硬墙 KB（默认 {INDEX_BUDGET_HARD_KB}）",
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="列出（行宽超限索引行的）无损压缩方案；不加 --apply 时不写盘",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="配合 --fix 执行压缩（唯一的写路径：原文先逐字迁入目标文件，再改写索引行）",
    )
    args = parser.parse_args(argv)

    if args.apply and not args.fix:
        parser.error("--apply 只与 --fix 搭配使用（本工具的唯一写路径）")

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd()
    if args.path:
        harness = "explicit --path"
        memory_dir = Path(args.path).expanduser().resolve()
    else:
        resolved = resolve_memory_dir(repo_root, harness=args.harness)
        if resolved is None:
            return _report_no_unique_dir(repo_root, harness=args.harness)
        harness, memory_dir = resolved

    report = lint_memory_dir(memory_dir, repo_root=repo_root)

    print(f"memory-lint: {memory_dir}（harness={harness}）")
    for msg in report.errors:
        print(f"  [ERROR] {msg}")
    for msg in report.warnings:
        print(f"  [WARN ] {msg}")
    print(
        f"结果：{len(report.errors)} error / {len(report.warnings)} warn"
    )

    budget: IndexBudget | None = None
    if args.budget:
        budget = index_budget(
            memory_dir, target_kb=args.target_kb,
            soft_kb=args.soft_kb, hard_kb=args.hard_kb,
        )
        print(f"索引预算：{budget.describe()}")

    if args.fix:
        plans = plan_index_fixes(memory_dir)
        if not plans:
            print(f"索引压缩：没有行宽 > {INDEX_LINE_MAX_CHARS} 的行")
        elif not args.apply:
            print(f"索引压缩：{len(plans)} 条待处理（只打印方案；加 --apply 执行）")
            for plan in plans:
                print(f"  {plan.describe()}")
            print("  压缩后预览：" + " / ".join(
                p.after for p in plans if not p.skipped
            )[:200])
        else:
            changed, notes = apply_index_fixes(memory_dir, plans)
            print(f"索引压缩：改写 {changed} 行（原文已逐字迁入各条目文件）")
            for note in notes:
                print(f"  {note}")
            for plan in (p for p in plans if p.skipped):
                print(f"  {plan.describe()}")
            if changed:
                # 复检：退出码与报数要反映**压缩后**的状态，不是压缩前的
                report = lint_memory_dir(memory_dir, repo_root=repo_root)
                print(f"复检（压缩后）：{len(report.errors)} error / "
                      f"{len(report.warnings)} warn")
                if args.budget:
                    budget = index_budget(
                        memory_dir, target_kb=args.target_kb,
                        soft_kb=args.soft_kb, hard_kb=args.hard_kb,
                    )
                    print(f"索引预算（压缩后）：{budget.describe()}")

    if report.errors:
        return 1
    if budget is not None and budget.level != "ok":
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
