"""#2850 静态守卫：sticky 表头必须近乎不透明，`/50` 一出现即红。

**为什么是静态守卫**：「行从表头下面滚过去会不会透印」是**几何+合成**结论，
jsdom 无布局引擎测不了（`docs/development/testing.md` §4 明写这类只能走静态守卫或真实
浏览器）。能被静态证明的是这条设计口径本身，而它是仓内**已写下的不变量**：
`frontend/src/components/execution/plan-execute/DeviceTablePanel.tsx:80-81`
「bg-muted/95 是 sticky 表头的必要条件……**勿当底色漂移改成 /50**」。

漂移方式很具体：`hover:bg-muted/50` 这类值在评审里看着像「只是底色深浅」，
改起来毫无阻力——#2850 报的 4 处里 3 处就是这么漂的（同族表头各写一套 alpha）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

#: 表头元素的起始标记（sticky 一定挂在这些元素或其直接子 TableRow 上）。
_HEADER_OPEN = re.compile(r"<(TableHeader|thead|TableHead|TableRow)\b")
#: **识别**窗口：从起始标记往下找 N 行，看到 `sticky top-0` 即认定这是 sticky 表头。
#: 它只负责「这是不是表头」，不负责取透明度值（见 `_BLOCK_MAX_LINES`）。
_WINDOW_LINES = 8
#: **取值**窗口：从起始标记找到该元素的闭合标签（同名标签配对），上限行数。
#: #2986：固定 8 行的取值窗口够不到 `DeviceOverview.tsx` 的透明度（它在 `TableHeader`
#: 内第 11 行）——该文件因此贡献 0 个被测值却仍计入反空转下限，把 `/95` 改回 `/50`
#: 也全绿。取值面必须跟着**元素块**走，而不是跟着「识别用的那几行」走。
_BLOCK_MAX_LINES = 60
_ALPHA = re.compile(r"\bbg-[a-zA-Z][\w-]*/(\d{1,3})\b")
_STICKY = re.compile(r"sticky\s+top-0")
#: 不变量的口径出处（本守卫引用它；它消失了说明口径被改，守卫要同步而不是静默通过）。
SOURCE_OF_TRUTH = "frontend/src/components/execution/plan-execute/DeviceTablePanel.tsx"
TRUTH_MARK = "勿当底色漂移改成 /50"

#: 允许低于 /95 的例外（必须写理由；空表是默认要求）。
#: 现状：无——`bg-card/80 backdrop-blur-sm` 是 AppShell 顶栏（非表头，且带磨砂），
#: 不在本判据的窗口形态内，无需豁免。
ALLOW_LOW_ALPHA: dict[str, str] = {}


def _frontend_tsx() -> list[Path]:
    """子树扫描（`frontend/src`）——不是 #2870 的那类失效。

    #2870 修的是「从**仓库根**做文件系统扫描」：那才会把 `.wt/*` 里的整仓副本读进来。
    这里从 `frontend/src` 起扫，`frontend/src/.wt` 不存在，且顺带按目录名剪掉
    `node_modules`/构建产物，判定集只可能包含前端源码。
    """
    skip = {"node_modules", "__pycache__", "dist", "build", ".wt"}
    out = [
        path
        for path in FRONTEND_SRC.rglob("*.tsx")
        if not (set(path.relative_to(FRONTEND_SRC).parts) & skip)
    ]
    out += [
        path
        for path in FRONTEND_SRC.rglob("*.jsx")
        if not (set(path.relative_to(FRONTEND_SRC).parts) & skip)
    ]
    assert len(out) > 50, f"前端扫描面塌陷：{len(out)} 个组件文件"
    return sorted(out)


def _element_block(lines: list[str], start: int, tag: str) -> list[str]:
    """起始标记所在元素的整块文本：按同名标签配对找闭合，受 `_BLOCK_MAX_LINES` 上限约束。

    配对是轻量正则（`<Tag` / `</Tag>` / 自闭合 `<Tag … />`），JSX 文本里出现同名标签
    字面量不在本仓的写法范围内；配不上时退化为「起始 + 上限行」，宁可多取几行也不静默
    取空——空集正是 #2986 的失效形态。
    """
    open_re = re.compile(rf"<{tag}\b")
    close_re = re.compile(rf"</{tag}\b")
    self_close_re = re.compile(rf"<{tag}\b[^>]*/>")
    depth = 0
    end = min(len(lines), start + _BLOCK_MAX_LINES)
    for j in range(start, end):
        depth += (len(open_re.findall(lines[j])) - len(self_close_re.findall(lines[j]))) - len(
            close_re.findall(lines[j])
        )
        if depth <= 0:
            return lines[start : j + 1]
    return lines[start:end]


def _sticky_header_windows(text: str) -> list[tuple[int, str]]:
    """返回 (起始行号, 取值块文本)：表头元素且识别窗口内出现 `sticky top-0` 的那些。

    取值块 = 整个元素（可能是 `<thead>…</thead>` 或单个自闭合 `<TableRow … />`），
    透明度值在块内任意深度都算——#2986 的漏判正是「值落在识别窗口之外」。
    """
    lines = text.splitlines()
    out: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        m = _HEADER_OPEN.search(line)
        if not m:
            continue
        if not any(_STICKY.search(w) for w in lines[i : i + _WINDOW_LINES]):
            continue
        out.append((i + 1, "\n".join(_element_block(lines, i, m.group(1)))))
    return out


def test_source_of_truth_for_the_invariant_is_still_there() -> None:
    """口径出处若被改/删，本守卫的判据就失去依据——先响一地要求同步，而不是继续绿。"""
    path = REPO_ROOT / SOURCE_OF_TRUTH
    assert path.is_file(), f"{SOURCE_OF_TRUTH} 消失：#2850 的不变量口径需要重新确立"
    assert TRUTH_MARK in path.read_text(encoding="utf-8"), (
        f"{SOURCE_OF_TRUTH} 里那句「{TRUTH_MARK}」不见了——改了口径就必须同步本守卫的"
        "阈值与豁免表，别让它悄悄守着一条已废弃的规则"
    )


def test_no_sticky_header_below_the_opacity_floor() -> None:
    offenders: list[str] = []
    checked_in: set[str] = set()
    measured: set[str] = set()
    for path in _frontend_tsx():
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOW_LOW_ALPHA:
            continue
        text = path.read_text(encoding="utf-8")
        for lineno, window in _sticky_header_windows(text):
            checked_in.add(rel)
            hits = list(_ALPHA.finditer(window))
            if hits:
                measured.add(rel)
            for m in hits:
                if int(m.group(1)) < 95:
                    offenders.append(f"{rel}:{lineno} 用了 {m.group(0)}")
    # 下界：判据的窗口行数与元素标记都是**假设**，假设失配时集合会静默变小，
    # 于是上面那条聚合断言退化成「零违规」的恒真——正是 #2639/#2641 数过的失效形态。
    # #2986：下界必须建在**取出过值**的文件上。只数「被识别成表头」的文件会把
    # 「认出来了但一个值都没取到」算作覆盖（DeviceOverview 就这样贡献 0 个值却计入 5）。
    # 取 5 = 本仓现存含 sticky 表头且真取到透明度值的组件数。
    assert len(measured) >= 5, (
        f"只从 {len(measured)} 个组件取到透明度值（识别到表头的有 {len(checked_in)} 个，现存应 ≥5）"
        "——取值窗口/元素配对已失配，本守卫会在漂移重现时静默通过；先修判据，不接受空集"
    )
    assert not offenders, (
        "sticky 表头必须近乎不透明（行从它下面滚过会透印）；改回 /95："
        f"{offenders}。口径见 {SOURCE_OF_TRUTH}:80-81"
    )


def test_guard_catches_the_original_shape_and_ignores_non_headers() -> None:
    """判据自身要有红侧：/50 的 sticky 表头必须被抓，非表头的半透明工具条不得误伤。"""
    bad = (
        "          <TableHeader>\n"
        '            <TableRow className="sticky top-0 z-10 bg-muted/50 hover:bg-muted/50">\n'
        "              <TableHead>Serial</TableHead>\n"
    )
    hits = _sticky_header_windows(bad)
    assert hits, "含 sticky top-0 的表头必须被认出来"
    assert int(_ALPHA.search(hits[0][1]).group(1)) == 50

    good = (
        '          <TableHeader className="text-left">\n'
        '            <TableRow className="sticky top-0 z-10 bg-muted/95 hover:bg-muted/95">\n'
        "              <TableHead>Serial</TableHead>\n"
    )
    hits = _sticky_header_windows(good)
    assert hits and all(int(m.group(1)) >= 95 for m in _ALPHA.finditer(hits[0][1]))

    # 非表头：AppShell 顶栏（bg-card/80 + backdrop-blur）不该被这条判据吃掉
    toolbar = '        <header className="sticky top-0 z-30 bg-card/80 backdrop-blur-sm">\n'
    assert not _sticky_header_windows(toolbar), "判据扩到非表头元素会误伤，下一次就被整条注释掉"


@pytest.mark.parametrize(
    "rel",
    [
        "frontend/src/components/plan-run/DeviceOverview.tsx",
        "frontend/src/components/device/ExpandableDeviceTable.tsx",
        "frontend/src/components/network/ExpandableHostTable.tsx",
        "frontend/src/components/plan-run/LogEventsCard.tsx",
    ],
)
def test_the_four_reported_headers_are_fixed(rel: str) -> None:
    """#2850 点名的四处逐个钉：漏改一处就该红，而不是靠上面那条聚合断言碰运气。

    #2986：每个文件还必须**至少取到一个值**——「认出来了但一个值都没测」等于这条
    参数化用例恒真（DeviceOverview 原本正是如此）。
    """
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    windows = _sticky_header_windows(text)
    assert windows, f"{rel} 里没有认出 sticky 表头——判据形态与实现脱节，不是「已修」"
    measured = [m for _, window in windows for m in _ALPHA.finditer(window)]
    assert measured, f"{rel} 认出了表头但一个透明度值都没取到（#2986 的失效形态）"
    for lineno, window in windows:
        for m in _ALPHA.finditer(window):
            assert int(m.group(1)) >= 95, f"{rel}:{lineno} 仍是 {m.group(0)}"
