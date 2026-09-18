"""#83 静态守卫：设备总览表格的虚拟化几何锁在单一来源，minimap 必须保留全量。

**为什么是静态守卫**：前端测试跑在 jsdom 上，而 jsdom 没有布局引擎
（`docs/development/testing.md` §4）——「多少行进 DOM、滚动条长度对不对」这类结论在此
结构上测不到。能被静态证明的是三件事，本文件把它们钉住：

1. 几何常量（行高 / overscan / 阈值 / 视口高）**只在** `deviceTableVirtual.ts` 定义一次，
   组件侧只引用、不自带数字（自带数字 = 下一次调优必漏改一处）；
2. 只有**表格视图**进虚拟层，**minimap 仍渲染全量**——#83 的约束写得很明确：minimap
   每设备 1 节点（510 台实测才 603 节点），本来就不该分页；一起虚拟化了方块阵会画残；
3. 虚拟层的两条不可拆项同时在场：滚动容器（`overflow-y-auto`）与**钉住的表头**
   （`sticky top-0`）。只加前者 = 滚两屏认不出列，属于「修一个成本、造一个缺陷」。

范式沿用 `tests/test_frontend_bulk_selection_guard_2614.py`（导入图配对 + 几何字面量锁
单一来源）；源扫描部分按 #2639 用 `SourceGuard` 锚点先行。
"""

from __future__ import annotations

import re
from pathlib import Path

from tools.dev.source_anchor import SourceGuard

REPO_ROOT = Path(__file__).resolve().parents[1]
TABLE_TSX = "frontend/src/components/plan-run/DeviceOverview.tsx"
GEOMETRY_TS = "frontend/src/components/plan-run/deviceTableVirtual.ts"

#: 多行 import 的收尾锚点（证明组件从正本导入，而不是自己抄一份）。
IMPORT_ANCHOR = "} from './deviceTableVirtual';"
#: 必须锁在单一来源的几何常量（名字与 deviceTableVirtual.ts 的导出一致）。
GEOMETRY_NAMES = (
    "DEVICE_TABLE_ROW_PX",
    "DEVICE_TABLE_OVERSCAN",
    "DEVICE_TABLE_VIRTUALIZE_THRESHOLD",
    "DEVICE_TABLE_VIEWPORT_MAX_PX",
)
#: 组件里必须出现的引用形态（虚拟层接线的最小骨架）。
_IMPORTABLE = GEOMETRY_NAMES + ("deviceTableSpacers", "shouldVirtualizeDeviceTable")

WIRING = (
    "useVirtualizer({",
    "estimateSize: () => DEVICE_TABLE_ROW_PX",
    "overscan: DEVICE_TABLE_OVERSCAN",
    "data-testid=\"device-table-scroll\"",
    "overflow-y-auto",
    "sticky top-0",
)


def _text(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def _guard(rel: str) -> SourceGuard:
    return SourceGuard.of_repo_path(rel)


def test_geometry_module_defines_the_constants_once() -> None:
    text = _text(GEOMETRY_TS)
    for name in GEOMETRY_NAMES:
        assert re.search(rf"^export const {name}\b", text, re.MULTILINE), (
            f"{GEOMETRY_TS} 缺少 `export const {name}`——几何正本被删或改名"
        )
    # 阈值与行高必须是正数常量（不是 0，否则虚拟化要么永不触发要么每行都重算）
    assert re.search(r"export const DEVICE_TABLE_ROW_PX = [1-9]\d*", text)
    assert re.search(r"export const DEVICE_TABLE_VIRTUALIZE_THRESHOLD = [1-9]\d*", text)


def test_component_imports_every_symbol_it_references() -> None:
    """导入图配对：组件正文里引用的每个正本符号，都必须出现在那条 import 里。"""
    text = _text(TABLE_TSX)
    assert IMPORT_ANCHOR in text, f"组件不再从 {GEOMETRY_TS} 导入——几何第二份真值"
    head = text[: text.index(IMPORT_ANCHOR)]
    imported = head[head.rindex("import {") :]
    body = text[text.index(IMPORT_ANCHOR) :]
    referenced = [name for name in _IMPORTABLE if name in body]
    assert referenced, "组件正文不再引用任何正本符号——虚拟化接线整体消失"
    for name in referenced:
        assert name in imported, f"{name} 被正文引用却未从正本导入（#83）"


def test_threshold_is_not_reimplemented_on_the_component_side() -> None:
    """阈值只在正本出现一次：组件想改判定就得改正本，两处各写一份必漏改。"""
    assert "DEVICE_TABLE_VIRTUALIZE_THRESHOLD" not in _text(TABLE_TSX)


def test_no_inline_geometry_literals_in_the_virtualizer_wiring() -> None:
    """`estimateSize: () => 43` / `overscan: 8` 这种写法即回归——数字必须引常量。"""
    text = _text(TABLE_TSX)
    guard = _guard(TABLE_TSX).anchored("useVirtualizer({")
    guard.assert_present(
        "estimateSize: () => DEVICE_TABLE_ROW_PX", why="行高走正本常量（#83）"
    )
    guard.assert_present("overscan: DEVICE_TABLE_OVERSCAN", why="overscan 走正本常量（#83）")
    # 字面量形态单列判据：上面那条 present 断言含 `estimateSize: () => `，不能用它做否定
    for pattern, label in (
        (r"estimateSize:\s*\(\)\s*=>\s*\d", "estimateSize 写了像素字面量"),
        (r"overscan:\s*\d", "overscan 写了字面量"),
        (r"maxHeight:\s*\{?`?\d", "视口高写了像素字面量"),
    ):
        assert not re.search(pattern, text), f"{label}——几何正本是 {GEOMETRY_TS}（#83）"


def test_virtual_layer_wiring_is_all_present() -> None:
    """滚动容器、钉住的表头、虚拟标记，缺一半就是换了个缺陷。"""
    guard = _guard(TABLE_TSX).anchored("data-testid=\"device-table-scroll\"")
    for needle in WIRING:
        guard.assert_present(needle, why=f"虚拟层接线缺 {needle}（#83）")
    guard.assert_present('data-virtual="true"', why="几何探针与静态守卫靠它识别虚拟层")


def test_threshold_decision_has_exactly_one_call_site() -> None:
    """阈值判定只此一处（复制两份 = 下次调阈值必漏改一处）。"""
    text = _text(TABLE_TSX)
    assert text.count("shouldVirtualizeDeviceTable(") == 1, (
        "shouldVirtualizeDeviceTable 应恰有一处调用点；多出来即判定被复制（#83）"
    )
    assert "devices.length > 80" not in text, "阈值被就地写成字面量，正本常量形同虚设"


def test_minimap_view_keeps_the_full_device_set() -> None:
    """minimap（方块阵）**不得**跟着虚拟化：每设备 1 节点，全量才画得出完整阵。"""
    text = _text(TABLE_TSX)
    grid_block = text[text.index("function DeviceGrid(") : text.index("function DeviceTable(")]
    assert "devices.map(" in grid_block, "minimap 不再逐设备渲染——违反 #83 的约束"
    assert "useVirtualizer" not in grid_block, "minimap 被虚拟化了：方块阵会画残"


def test_spacer_math_is_used_rather_than_reimplemented() -> None:
    """垫片算术走正本函数，不在组件里重算一遍（重算 = 第二份真值）。"""
    guard = _guard(TABLE_TSX).anchored("deviceTableSpacers(visible,")
    guard.assert_present("padTopPx", why="上垫片由正本算术给出（#83）")
    guard.assert_present("padBottomPx", why="下垫片由正本算术给出（#83）")
    guard.assert_absent(
        "padBottomPx: Math.max(", why="夹 0 的逻辑不得在组件里再写一遍（正本已夹）"
    )
