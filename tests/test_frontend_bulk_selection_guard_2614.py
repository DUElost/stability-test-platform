"""前端「悬浮批量条 ⇒ 选中态避让」静态守卫（#2614，离线、秒级）。

背景：`be51f2c6` 给主机页补了「全选后底部悬浮条挡住最后一行」的占位，但**没留约定**
（既没建单也没测试断言规格来源），于是设备页以同一形状复发——#2614 实测：1280/1366 宽下
「下一页」的坐标点击被 `DeviceBulkActionBar` 的内层实条吞掉，最坏情况命中「取消选择」，
刚建立的整页选中被静默清空。jsdom 没有布局引擎，E2E 的常规 `locator.click()` 又会先
`scrollIntoViewIfNeeded` 重算落点，**长期绿灯掩盖**（取证只能用坐标级点击，见 issue 正文）。

本守卫把两件事变成门禁：

1. **几何只有一个来源**：悬浮条的覆盖带类名不得被逐页抄成字面量（`#360` 里
   `max-w-5xl` vs `max-w-4xl`、断点不一致是同一件事的漂移）；
2. **有批量条的页面，其可选中表格必须渲染共享占位**：新页面复用这条模式而忘了避让 ⇒ 红。

判据用「导入图的显式配对」而不是「全库有 selectedIds 的文件都得有占位」——后者会把
`SchedulesPage`/`DeviceTablePanel` 这类**没有**悬浮条的选中表格判成误报（无覆盖层即无遮挡）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SRC = REPO_ROOT / "frontend/src"

# 悬浮条覆盖层的几何字面量（只允许出现在共享模块里）
GEOMETRY_LITERALS = (
    "pointer-events-none fixed bottom-4",
    "pointer-events-auto flex w-full max-w-5xl",
    "h-40 shrink-0",
)
_SHARED_MODULE = FRONTEND_SRC / "components/ui/bulk-action-bar.tsx"
_IMPORT_RE = re.compile(r"from\s+'([^']+)'")
# 表格的「可选中」形状：props 里有 selectedIds
_SELECTABLE_RE = re.compile(r"selectedIds\s*\??:")


def _source_files() -> list[Path]:
    return [
        path
        for path in sorted(FRONTEND_SRC.rglob("*.tsx"))
        if not path.name.endswith(".test.tsx")
    ]


def _resolve(spec: str, importer: Path) -> Path | None:
    """把 `@/x/y` 与相对导入解析成仓内源文件；外部包返回 None。"""
    if spec.startswith("@/"):
        base = FRONTEND_SRC / spec[2:]
    elif spec.startswith("."):
        base = (importer.parent / spec).resolve()
    else:
        return None
    for suffix in ("", ".tsx", ".ts", "/index.tsx"):
        cand = Path(str(base) + suffix)
        if cand.is_file():
            return cand
    return None


def _imports(path: Path) -> set[Path]:
    text = path.read_text(encoding="utf-8")
    resolved = {_resolve(spec, path) for spec in _IMPORT_RE.findall(text)}
    return {mod for mod in resolved if mod is not None}


def _bulk_bar_modules() -> set[Path]:
    """使用共享覆盖层规格的批量条组件。"""
    return {
        path
        for path in _source_files()
        if "BULK_BAR_OUTER_CLASS" in path.read_text(encoding="utf-8")
        and path != _SHARED_MODULE
    }


def test_geometry_has_a_single_source() -> None:
    """覆盖层几何不得在共享模块之外被抄成字面量。"""
    offenders: dict[str, list[str]] = {}
    for path in _source_files():
        if path == _SHARED_MODULE:
            continue
        text = path.read_text(encoding="utf-8")
        hit = [lit for lit in GEOMETRY_LITERALS if lit in text]
        if hit:
            offenders[path.relative_to(REPO_ROOT).as_posix()] = hit
    assert not offenders, (
        "悬浮批量条的几何规格漂移（#2614/#360）——请改用 "
        "frontend/src/components/ui/bulk-action-bar.tsx 的导出常量："
        f"{offenders}"
    )
    for lit in GEOMETRY_LITERALS:
        assert lit in _SHARED_MODULE.read_text(encoding="utf-8"), (
            f"共享模块缺少规格 {lit!r}，守卫的判据需要随之更新"
        )


def test_bulk_bar_pages_compensate_their_selectable_tables() -> None:
    """渲染了悬浮批量条的页面，其可选中表格必须用共享占位避让覆盖带。"""
    bars = _bulk_bar_modules()
    assert bars, "未找到任何批量条组件——解析器或目录结构已变，守卫失效"

    pages = [
        path
        for path in _source_files()
        if path.relative_to(FRONTEND_SRC).parts[0] == "pages"
        and (bars & _imports(path))
    ]
    assert len(pages) >= 2, f"批量条页面解析异常（只找到 {len(pages)} 个）：{pages}"

    checked = 0
    offenders: list[str] = []
    for page in pages:
        for module in sorted(_imports(page)):
            if module not in _source_files_set():
                continue
            text = module.read_text(encoding="utf-8")
            if not _SELECTABLE_RE.search(text):
                continue  # 非可选中表格：没有选中态就没有悬浮条
            checked += 1
            # 判据取 **JSX 里的渲染**，不是「文件里出现过这个标识符」——
            # 只留 import 而删掉渲染，是这条补偿最可能的静默退化形状（N1 实测）。
            if "<BulkBarSpacer" not in text:
                offenders.append(
                    f"{page.relative_to(REPO_ROOT).as_posix()} → "
                    f"{module.relative_to(REPO_ROOT).as_posix()}"
                )
    assert checked >= 2, f"应至少覆盖主机/设备两张表，实际 {checked}"
    assert not offenders, (
        "这些可选中表格所在页面有底部悬浮批量条，却没有渲染 "
        "`BulkBarSpacer`——覆盖带会吞掉最后一行（分页行）的点击（#2614）："
        f"{offenders}"
    )


def _source_files_set() -> set[Path]:
    return set(_source_files())
