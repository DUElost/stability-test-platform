# 设备总览虚拟层表头真钉住：消内层第二 scrollport（#83 sticky）

Status: implemented
Class: bug-fix

## Decision

虚拟化分支把 `Table` 原语自带的 `overflow-auto` 包裹层降为 `overflow-visible`：
`Table` 新增可选 `containerClassName`（默认不传、行为不变），`DeviceOverview`
虚拟化时传 `overflow-visible`（tailwind-merge 同组去重，后者胜出）。滚动视口只剩
外层 `device-table-scroll`——与 `useVirtualizer` 的 `getScrollElement` 指向外层
的既有假设一致，`thead` 的 `sticky top-0` 从此绑到真正滚动的祖先，表头钉住。

## Alternatives

- 外层 CSS 选择器（`[data-virtual] [data-slot=…] { overflow: visible }`）写进
  index.css：原语不动，但耦合藏在样式表里、与组件接线分离，守卫只能锁字符串——弃。
- 把内层包裹层做成真滚动视口（把 maxHeight 挪进去）：要重接 virtualizer 的
  scroll element 与测量，改动面大且与 #83 已交付行为交叉——弃。

## Verification

真浏览器 A/B（chromium headless，Playwright，200 台合成设备，构建态 bundle）：

| 状态 | 内层包裹 | 滚动 2000px 后 thead top | 判定 |
|---|---|---|---|
| 修复前（stash） | `overflow-auto` | **-1927px**（随内容滚走） | BROKEN |
| 修复后 | `overflow-visible` | **73px = 容器顶边（gap 0）** | OK |

- 虚拟化本身不受影响：200 台只渲染 23→32 DOM 行，滚动窗口正常推进；
- vitest 全量 129 文件 / 1046 用例通过（新增 2 条：虚拟层/静态路径的包裹层
  class 语义各锁一侧）；`tsc --noEmit`（两个 tsconfig）零错误；
- `tests/test_frontend_device_table_virtual_guard_83.py` 9 用例通过（新增
  scrollport 接线守卫）；`run_gates.py check:quick` 12 gates 全绿。

## Revisit

- 静态守卫是「接线在场」判据，几何结论依赖本次 rig 量测；若将来表头改用非
  sticky 方案（如独立表格外置），应同步删除该守卫与本注。
- `Table` 的 `containerClassName` 已是通用出口，其它「外层自管滚动」的用法
  可以复用，不必再加 CSS 特例。
