"""已移除配置键的引用面守卫（#2661）。

动机（本质问题）：#737 删掉的键只留在 commit message 里。两个方向都没有判据——
「文档还在教读者调一个不存在的开关」（ADR-0002/0003/0004 实测四处），
和「读者无法区分某键是故意保留还是已删」（无登记面）。#2661 于是立了台账
`docs/development/environment-variables.md` §6，本测试把台账变成可机检的承诺：

1. **台账键必须有出处**：每处引用的**同一行**必须带 `已移除 / 已删除 / 无读取点 / removed`
   之一（同行判定，换行即假绿——这是本守卫唯一容易做假的地方）；
2. **台账不可被清空**：下界键集缺席即红，否则「删掉登记表」就成了绕过手段；
3. **已移除键必须真的无读取点**：台账里的键不得再出现在 `.py` 的 `os.getenv` /
   `os.environ` 读取形态里（标记是免责说明，不是复活许可）；
4. **判别力自证**：自建临时目录验证「裸引用必红 / 带标记放行 / 超串不误伤」。

排除面：`docs/archive/**`（openspec 留档）与 `docs/notes/**`（追加式一次性 Note）
属历史陈述，按「不追改」的既有实践（同 ADR 正文的 `~~划线 + 已移除`` 勘误形态）不扫。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "development" / "environment-variables.md"
LEDGER_HEADING = "## 6. 已移除的键"

#: 引用面扫描范围（台账自身也在 `docs/**` 内，其表格行同样必须带标记）。
SCAN_ROOTS = ("docs", "backend", "deploy", "tools", "scripts")
SCAN_FILES = (Path(".github") / "workflows",)
EXCLUDE_PREFIXES = (
    Path("docs") / "archive",
    Path("docs") / "notes",
)
SCAN_SUFFIXES = {".md", ".py", ".yml", ".yaml", ".sh", ".example"}

#: 同行标记词（缺一即视为「把它当仍在生效的键在写」）。
MARKER = re.compile(r"已移除|已删除|无读取点|removed")
#: 标记词必须在**链接之外**出现。#2661 实测的假绿形态：锚点写成
#: `(#6-已移除的键)` 时 URL 里的「已移除」会把整行带绿——读者拿不到任何
#: 「该键已删」的陈述，守卫却通过。故判定前先剥 `[text](url)` 的 url 部分。
_MD_LINK_TARGET = re.compile(r"\]\([^)]*\)")

#: 台账下界：这些键的「已删除」事实必须留在登记面里（#2661）。
MIN_LEDGER_KEYS = {
    "USE_SESSION_WATCHDOG",
    "BACKPRESSURE_LAG_THRESHOLD",
    "BACKPRESSURE_RELEASE_THRESHOLD",
    "BACKPRESSURE_LOG_RATE_LIMIT",
}

_KEY_CELL = re.compile(r"^`([A-Z][A-Z0-9_]*)`$")
_READ_POINT = re.compile(r"""(?:os\.getenv|environ\s*\[|environ\.get)\s*\(?\s*["']([A-Z][A-Z0-9_]*)["']""")


def _token(key: str) -> re.Pattern[str]:
    """精确 token 匹配（子串匹配会让 `BATCH_SIZE` 之类大面积假阳性）。"""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])")


def ledger_keys(text: str) -> list[str]:
    """解析台账 §6 表格第一列的 code span（大小写敏感的全大写键名）。"""
    keys: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if not in_section:
            if stripped.startswith(LEDGER_HEADING):
                in_section = True
            continue
        if stripped.startswith("## ") and not stripped.startswith(LEDGER_HEADING):
            break
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells:
            continue
        m = _KEY_CELL.match(cells[0])
        if m:
            keys.append(m.group(1))
    return keys


def _scanned_files() -> Iterator[Path]:
    for name in SCAN_ROOTS:
        for path in (REPO / name).rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(REPO)
            if any(rel.parts[: len(p.parts)] == p.parts for p in EXCLUDE_PREFIXES if len(rel.parts) >= len(p.parts)):
                continue
            if path.suffix not in SCAN_SUFFIXES and ".env" not in path.name:
                continue
            if "__pycache__" in rel.parts:
                continue
            yield path
    for name in SCAN_FILES:
        base = REPO / name
        if not base.is_dir():
            continue
        for path in base.rglob("*.yml"):
            yield path


def bare_references(keys: list[str], files: list[Path]) -> list[str]:
    """返回「引用了台账键但同行无标记」的 `路径:行号: 内容` 列表。"""
    tokens = [(key, _token(key)) for key in keys]
    offenders: list[str] = []
    for path in files:
        rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:  # pragma: no cover - 扫描期读不到就是环境问题
            raise AssertionError(f"无法读取 {rel}: {exc}") from exc
        for lineno, line in enumerate(lines, start=1):
            marked = _MD_LINK_TARGET.sub("](", line)
            for key, token in tokens:
                if token.search(line) and not MARKER.search(marked):
                    offenders.append(f"{rel}:{lineno}: [{key}] {line.strip()[:120]}")
    return offenders


def read_point_hits(keys: list[str], files: list[Path]) -> list[str]:
    """台账键若以 `os.getenv("KEY")` / `environ["KEY"]` 形态出现即为复活。"""
    wanted = set(keys)
    hits: list[str] = []
    for path in files:
        if path.suffix != ".py":
            continue
        rel = path.relative_to(REPO) if path.is_relative_to(REPO) else path
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        ):
            for name in _READ_POINT.findall(line):
                if name in wanted:
                    hits.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    return hits


def test_ledger_keeps_known_removed_keys_registered() -> None:
    """守卫的下界：台账缺席下界键 = 判据空转（比不写守卫更糟）。"""
    keys = set(ledger_keys(LEDGER.read_text(encoding="utf-8")))
    assert keys, "台账 §6 未解析出任何键——本守卫已退化为空检查"
    missing = sorted(MIN_LEDGER_KEYS - keys)
    assert not missing, (
        f"environment-variables.md §6 丢了已登记键：{missing}"
        "——「已移除」的事实必须有单一登记面，删表不等于删事实"
    )


def test_removed_keys_are_never_referenced_bare() -> None:
    """主判据：全仓（排除历史留档面）不得有裸引用。"""
    keys = ledger_keys(LEDGER.read_text(encoding="utf-8"))
    offenders = bare_references(keys, list(_scanned_files()))
    assert not offenders, (
        "引用了已移除键却无同行标记（把已删配置当生效开关写）：\n"
        + "\n".join(offenders)
        + "\n标记口径：同一行内出现 已移除 / 已删除 / 无读取点 / removed 之一"
    )


def test_removed_keys_have_no_read_points() -> None:
    """标记不是复活许可：台账键不得再有环境读取点。"""
    keys = ledger_keys(LEDGER.read_text(encoding="utf-8"))
    py_files = [p for p in _scanned_files() if p.suffix == ".py"]
    hits = read_point_hits(keys, py_files)
    assert not hits, "已移除键重新出现读取点：\n" + "\n".join(hits)


def test_guard_discriminates_bare_from_annotated(tmp_path: Path) -> None:
    """判别力自证：裸引用红、同行标记绿、超串不误伤、跨行标记不算。"""
    key = "USE_SESSION_WATCHDOG"
    bare = tmp_path / "bare.md"
    bare.write_text(f"先启用 {key} 再重启。\n", encoding="utf-8")
    bare_rel = Path("docs") / "bare.md"

    annotated = tmp_path / "annotated.md"
    annotated.write_text(
        f"`{key}` 已移除（#737），无读取点。\n", encoding="utf-8"
    )
    stale = tmp_path / "stale.md"
    stale.write_text(
        f"上一行说它已移除。\n下一行仍当生效开关用：{key}\n", encoding="utf-8"
    )
    superstring = tmp_path / "super.md"
    superstring.write_text(f"{key}_EXTRA=1\n", encoding="utf-8")

    def hits(path: Path) -> list[str]:
        return bare_references([key], [path])

    assert hits(bare), "裸引用未被判红——守卫失去判别力"
    assert not hits(annotated), "同行带标记仍被判红（误伤正当写法）"
    assert hits(stale), "标记与引用不同行时未被判红（跨行假绿）"
    assert not hits(superstring), "子串/超串被误判为引用该键"
    smuggled = tmp_path / "smuggled.md"
    smuggled.write_text(
        f"仍当生效开关写：{key}，详见 [台账](../environment-variables.md#6-已移除的键)。",
        encoding="utf-8",
    )
    assert hits(smuggled), "标记词只藏在链接锚点里也被放行（锚点夹带假绿）"
    assert not bare_references(["BATCH_SIZE"], [bare]), "无关键名不应命中"
    assert bare_rel.parts[0] == "docs"  # 排除面前缀形状自证


def test_history_exclusion_is_load_bearing(tmp_path) -> None:
    """排除面必须**承重**：留档里确有裸引用，若不排除就会追改历史。

    取具体文件而不是「目录名不在扫描集合里」——`tools/archive/` 与
    `docs/archive/` 同名不同义，按目录名断言会被它撞出假红（实测）。
    """
    from_path = REPO / "docs" / "archive" / "openspec" / "specs" / "session-lifecycle" / "spec.md"
    note = REPO / "docs" / "notes" / "bug-fix" / "2026-09-13-ghost-configs-dead-metrics-737.md"
    assert from_path.is_file() and note.is_file()

    scanned = set(_scanned_files())
    assert from_path not in scanned and note not in scanned, "历史留档面未被排除"
    # 排除是必要的：这两处若进扫描面就会红（键在、同行无标记）
    for path in (from_path, note):
        assert bare_references(["USE_SESSION_WATCHDOG"], [path]), (
            f"{path} 已不含裸引用——排除面不再承重，应复核是否可缩小排除范围"
        )
    # 台账自身必须在扫描面内，否则「登记即免责」（表格行不再被同行标记规则检查）
    assert LEDGER in scanned, "台账文件未被扫描"


def test_scan_universe_covers_the_faces_that_actually_drifted() -> None:
    """扫描面必须覆盖 #2661 真实漂移的那几处——否则「全绿」只是没扫到。

    收窄 `SCAN_ROOTS`（或把某目录塞进排除面）是这条守卫最容易被绕过去的形态，
    且不会有任何其它信号：故直接把**当初出问题的那几个文件**钉进断言。
    """
    scanned = set(_scanned_files())
    for rel in (
        "docs/adr/ADR-0002-single-process-with-internal-schedulers.md",
        "docs/adr/ADR-0003-task-run-state-machine-and-device-lock-lease.md",
        "docs/adr/ADR-0004-heartbeat-driven-host-device-liveness.md",
        "docs/development/environment-variables.md",
        "backend/.env.example",
        "backend/main.py",
    ):
        path = REPO / rel
        assert path.is_file(), f"样例文件消失：{rel}"
        assert path in scanned, f"扫描面漏掉 #2661 漂移面：{rel}"
    assert len(scanned) > 500, f"扫描面过小（{len(scanned)}），判据已退化"
