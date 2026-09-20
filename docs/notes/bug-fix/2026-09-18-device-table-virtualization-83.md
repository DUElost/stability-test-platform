# 设备总览表格视图行虚拟化：510 行 14,056 DOM 节点 → 窗口内行数（#83）

Status: implemented
Class: bug-fix

> **本单只解 DOM 侧**（表格视图全量物化）；`GET /plan-runs/{id}/devices` 的 0.93 MB
> 传输侧仍未动，属 #83 的另一半，见 Revisit。~~真实浏览器复测亦未完成（标 pending）~~
> → **2026-09-20 回写**：几何那一半已由 #2821 做掉，并查出本单一处真缺陷（文末
> 「## 状态回写」）；510/1000 两档的节点数复测仍未做，pending 范围已缩窄。

## Decision

**先厘清本单的两笔成本，只动其中一笔。** #83 实测（09-18，510 台 run）：

| 视图（同一组件可切换） | DOM 节点 | `<tr>`/cell | 每设备节点 | 虚拟化 |
|---|---:|---:|---:|---|
| minimap（方块阵，默认） | 603 | — | ≈1.0 | 不需要 |
| 表格态 | **14,056** | **511** | **≈27.6** | **无**（`[data-index]=0`） |

- **传输侧**（`GET /plan-runs/{id}/devices` 无分页，0.93 MB/510 台）：**本单不动**。
  加分页要先定「minimap 走轻量端点」的契约形状（正文的三方向之一），并保住
  `by_status`/`by_link_status`/`by_host` 的**全集口径**——那是后端契约变更，另议。
- **渲染侧**（表格态把 510 行全量物化）：**本单解决**。外推 1000 台 ≈2.75 万节点、
  4 万像素滚动高，且这是详情页在千台 run 上同时吃传输与 DOM 两头的其中一头。

**最小方案 = 只给表格视图加行虚拟化，minimap 保持全量。**
依据是正文的约束本身：minimap 每设备 1 节点，「minimap 要求全集」并不反对分页，
它只约束方块阵那一路。实现沿本仓已验证的先例（`DeviceMatrix.tsx:2,126` 与
`SelectedMinimap.tsx:22,213` 都用 `@tanstack/react-virtual`，且已有
`DeviceMatrix.virtual.test.ts` 的纯函数用例范式）。

几何与判定的落点：

- **`deviceTableVirtual.ts` 是几何与垫片算术的唯一来源**：`DEVICE_TABLE_ROW_PX = 43`
  （#83 真机 21,715px / 511 `<tr>` ≈ 42.6 → 取 43）、`OVERSCAN = 8`、
  `VIRTUALIZE_THRESHOLD = 80`（与 `MINIMAP_VIRTUAL_THRESHOLD = 80` 同量级：**小 run 不为
  虚拟层买单**）、`VIEWPORT_MAX_PX = 640`、`shouldVirtualizeDeviceTable()`、
  `deviceTableSpacers()`（夹到 ≥0——`estimateSize` 与实测有偏差时 `last.end` 会超过
  `totalSize`，不夹就渲染出 `height: -7px` 这种脏样式）。
- 组件侧：过阈值才把 `<Table>` 包进滚动容器（`data-testid="device-table-scroll"` +
  `data-virtual="true"` + `data-row-total`），`<TableBody>` 首尾各一段 `aria-hidden` 垫片
  `<tr>`；表头 `sticky top-0 z-10`。

  ⚠️ **2026-09-20 更正**：这行当时**只在字面上成立**。`Table` 原语自带一层
  `overflow-auto` 包裹（`table-scroll-container`），于是 `sticky` 的最近滚动祖先是那层
  **内层**包裹（其 `scrollTop` 恒 0），外层滚动时整棵子树平移 ⇒ 表头随内容滚走。由
  **#2821** 修（虚拟化时内层降为 `overflow-visible`，滚动视口唯一化到外层）。
  结论不变、实现要改：**「钉住」是接线关系，不是 class 在场。**

- **高亮行的滚动改法**：虚拟化后目标行可能根本没挂载，`scrollIntoView` 无从下手 →
  走 `rowVirtualizer.scrollToIndex(index, {align:'center'})`（`DeviceMatrix` 同一手法）；
  静态路径保留原 `scrollIntoView` 行为不变。

## Alternatives

- **后端加分页 + 轻量 minimap 端点**（正文方案 1/2 的另一半）：不在本单。契约变更要连带
  facets 全集语义、`types.ts` 同步与 `add-api-endpoint` 全链路，且正文的先决条件
  （1000 台量级实测）尚未取得——DOM 这一头是纯前端、零契约风险的。
- **精简字段 / 详情按需拉取**（正文方案 3）：否决为「本单的解」——它治传输，不治 DOM；
  表格每行 11 列本就是设计，砍列是产品取舍。
- **不虚拟化，直接限制表格视图最多渲染 N 行 + 「显示更多」按钮**：否决。把成本转嫁成
  用户操作，且千台 run 下「查看更多」要点 10 次。
- **自己写窗口算术（不用 tanstack）**：否决。等于把滚动/测量/resize 的第二份实现引进来，
  而仓内已有两处用同一套库的既有先例。

## Verification

- **jsdom（结构与语义，不声称证明几何）**：`vitest run src/components/plan-run/` → 137 passed，
  其中本单新增 5 条：过阈值才进虚拟层（4 行仍走静态表格、无滚动容器）、500 行时
  `device-row-*` < 60 且垫片覆盖 400×43px 以上、**facets chip 仍显示全集 500**（后端全集
  口径不被前端二次统计）、**minimap 仍渲染 500 个格子**（约束有守卫）、虚拟层内点行仍回传设备。
  另 `deviceTableVirtual.test.ts` 5 条纯函数用例（阈值边界 / 垫片夹 0 / 空窗口）。
- **全量前端**：`node_modules/.bin/vitest run` → **129 文件 / 1046 passed**；
  `tsc --noEmit` → exit 0；`eslint src --max-warnings 0` → exit 0
  （顺带摘掉一条因本次接线变更而变为 unused 的 `react-hooks/purity` 指令——
  留着它反而会让 `--max-warnings 0` 变红，已在代码注释里写明原因）。
- **仓库级 python 门禁**：`pytest tests/ -q` → **1575 passed**（含本单新增守卫 8 条）；
  `check_governance_surface.py --check` → S1–S14、S5x 全绿；`ruff check` → 通过；
  `check_inner_imports.py` → 606 ≤ 基线 606（未新增函数体内 import）。
- **静态守卫**：`tests/test_frontend_device_table_virtual_guard_83.py` → 8 passed。
  **变异自证**（四条，逐条红）：行高写死 `=> 43` → 红；表头去掉 `sticky top-0` → 红；
  minimap 里出现 `useVirtualizer` → 红；阈值就地写成 `devices.length > 80` → 红。
  还原后 8 passed。
- **`react-virtual` mock 不是自证**：把 `VIRTUALIZE_THRESHOLD` 调成 999999，500 行用例即红
  ——证明被测的是组件接线而非 mock。
- **2026-09-20 追加（来自 #2821 的真实浏览器 A/B）**：修复前滚动 2000px 后 `thead`
  top = **-1927px**（随内容滚走），修复后 = 容器顶边（gap 0）；虚拟化窗口不受影响
  （200 台只渲 23→32 行）。⇒ 本单的 `43px` 行高**未见滚动跳变**，但那是 200 台档。
- **仍 pending（缩窄后）**：510 台 / 1000 台两档的节点数与滚动总高一致性复测——
  原文「14,056 → 多少」那一半仍未回答。按 `testing.md` §4，
  「14,056 → 多少节点」「滚动条总高是否准确」「sticky 表头在 Safari 的表现」这类几何结论
  **在 jsdom 结构上测不到**；本单的 `43px` 行高取自 #83 的真机测量（21,715/511），
  但**改造后的**节点数与滚动一致性需要 dev 隔离栈 + headless Chromium 复测（510 台/1000 台
  两档），尚未做。落地后请按 #2614 的「坐标级 + `elementFromPoint`」口径补一次实测再关单。

## Revisit

- **传输侧仍在 #83 上**：`GET /plan-runs/{id}/devices` 依然无分页（0.93 MB/510 台，
  外推 1000 台 ≈1.8 MB）。要与本单合并收口就得先定 facets 全集语义怎么保——
  建议方向：minimap 走只含 `job_id`/两个状态维度的轻量端点，表格走分页；
  这与 #496（列表基础设施）、#703（轮询→QueuePool）、#2623（detail 内嵌 jobs）同面。
- 若真实浏览器复测发现 `43px` 与实测行高偏差导致滚动跳变，改 `deviceTableVirtual.ts`
  一处即可（几何单一定义点），并同步该文件的静态守卫。

## 状态回写（2026-09-20 · 基线 `1788749e`）

**触发**：#2821 对本 Note 所述改动做了真实浏览器复测，查出**一处真缺陷**（表头实际不钉住）并修掉。
本节点状当时与之后的事实，**不改写上文**——与本仓对 R-01 / H-03 的回写范式一致。

| 本 Note 当时的陈述 | 现在的状态 |
|---|---|
| 「表头 `sticky top-0 z-10`（只加滚动容器不加钉住 = 修一个成本、造一个缺陷）」 | 意图成立、**实现不成立**：sticky 绑在 `Table` 原语自带的内层 `overflow-auto` 包裹上；#2821 已修 |
| 「真实浏览器复测未完成」 | **部分完成**：200 台档的 sticky A/B 与窗口推进已实测；510/1000 档节点数未测，继续挂 pending |
| 静态守卫锁 `sticky top-0` / `overflow-y-auto` 字符串在场 | **判据强度不足已被证实**：字符串全在场时几何仍可完全错。#2821 补了 scrollport 接线守卫（现 9 用例）。这是 `testing.md` §4「静态守卫天花板」的新实例——它证得了「谁渲染了什么」，证不了「谁相对谁滚动」 |

**留给改这块的人两条**：

1. 判断「钉住 / 遮挡 / 命中」这类结论时，**先定「滚动祖先是哪一层」**再看 class：
   祖先链上任何一层有 `overflow`，它就会成为新的 scrollport，外面多包一层即改变结论；
2. 写静态守卫优先锁**接线关系**（谁的 ref 指向谁、哪一层被降级），而不是
   「某个 class 出现在文件里」——后者正是本 Note 踩过的形态。
