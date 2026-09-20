# sticky 表头回到 /95 不变量：4 处一起收，并加静态守卫防漂移（#2850）

Status: implemented
Class: bug-fix

## Decision

**先纠一处引用**：#2850 说不变量出处是 `DeviceTablePanel.tsx`，没给路径。实际在
`frontend/src/components/execution/plan-execute/DeviceTablePanel.tsx:80-81`，原话：

> `bg-muted/95` 是 sticky 表头的必要条件：行要从表头下面滚过去，近乎不透明才不透行；
> **勿当底色漂移改成 /50**

所以这不是新立的审美，而是**已有明文的口径被四处违反**（含 #2850 点名的 1 处 + 同类 3 处）：

| 位置 | 原值 |
|---|---|
| `plan-run/DeviceOverview.tsx:289` | `bg-muted/50 hover:bg-muted/50`（#2850 点名，且是 #83 虚拟化表头的同一行） |
| `device/ExpandableDeviceTable.tsx:385` | `sticky top-0 z-10 bg-muted/50 hover:bg-muted/50` |
| `network/ExpandableHostTable.tsx:345` | 同上 |
| `plan-run/LogEventsCard.tsx:190` | `<thead className="sticky top-0 bg-muted/50">` |

改法就一处一值：`/50 → /95`（含 hover 态，hover 时同样会有内容从下面滚过）。**不改** body 行、
展开行、按钮 hover 的 `/50`——那些不是「内容从其下方滚过」的表面，改了才是真漂移。

**为什么连同类 3 处一起收**：只修被点名的 1 处，守卫就要为剩下 3 处开豁免；而豁免表一旦
存在，下一个漂移会先进豁免表而不是先被修（#2286「恒真豁免」那条账）。这里 4 处同形、
零行为风险，收干净比留着更便宜。

## Alternatives

- **只修 #2850 点名的 1 处，其余开豁免**：否决，理由同上。
- **抽一个 `STICKY_HEADER_ROW` 常量到 design-system 再各处引用**：不在本单。它要动 4 个组件的
  class 组织方式（有 `cn()`、有裸串两种），且**当前真正缺的不是复用点而是"漂回来有人拦"**；
  先加判据，若日后出现第 5 处漂移再谈抽常量（那时有第二份真值的证据）。
- **`bg-muted`（完全不透明）**：偏离口径来源写的 `/95`；且 `SURFACE.header` 用的就是
  `bg-card/80 + backdrop-blur-sm` 这种「半透明+磨砂」的合法组合——说明本仓允许半透明，
  条件是它带 backdrop-blur。表头那 4 处都没有 blur，故按 /95 走。
- **写 jsdom 用例证明不透印**：做不到。jsdom 无布局引擎、不做合成，「滚过去看不透」
  在此结构上不可测（`testing.md` §4），硬写只会得到一条恒真断言。

## Verification

- **静态守卫** `tests/test_frontend_sticky_header_opacity.py` → **7 passed**：
  ① 四处逐个单独钉（漏改任一处即红，不靠聚合断言碰运气）；② 全局无 `/50` 级 sticky 表头；
  ③ **口径出处必须还在**——`DeviceTablePanel.tsx:80-81` 那句「勿当底色漂移改成 /50」被删或
  改写即红（守卫引用它，就得跟着它一起同步，不能悄悄守着一条已废弃的规则）；
  ④ 判据红侧自证：合成 `/50` sticky 表头被认出、`/95` 放行、`bg-card/80` 的 AppShell 顶栏
  （非表头、带磨砂）**不被误伤**
- **扫描面**用 `frontend/src` **子树**扫描而非仓库根（#2870 刚立的口径：从仓库根做文件系统
  扫描才会把 `.wt/*` 整仓副本读进来；本分支基线还没有 `tests/repo_scan.py`，故就地剪掉
  `node_modules`/构建产物并解释为什么不犯同一错）
- **判据下界的真实边界（不当作更强的能力）**：下界断言按「含 sticky 表头的组件数 ≥5」写。
  实测它能挡「标记形态失配导致扫到 0 个」（把 `_HEADER_OPEN` 改坏 → 立刻红并打印数量），
  但**挡不住**「窗口行数从 8 改到 1、只认出 4 个组件」这种退化（实测仍绿）——
  因为部分站点 sticky 与标记同行。这条天花板如实写在这里，别把下界当万能哨兵
- 前端：`vitest run` → **1046 passed（129 文件）**；`tsc --noEmit` exit 0；
  `eslint src --max-warnings 0` exit 0；仓库级 `pytest tests/ -q` → **1629 passed**；
  `ruff check` 通过；治理面 S1–S15 全绿
- **pending（不当作通过）**：**视觉本身没有终证**。「行滚过表头是否还透印」属合成/几何结论，
  按 `testing.md` §4 需真实浏览器（本仓 rig 已有先例：#2821 的 200 台 A/B）。本单只保证
  「值不再漂移」与「四处口径一致」，不宣称已看到屏幕。

## Revisit

- 若出现第 5 处 sticky 表头需要不同的 alpha（例如带 `backdrop-blur` 的浮层式表头），
  再考虑把 class 收成 design-system 常量并给判据加「有 blur 则允许 ≤/80」的分支；
  现在加这条分支是给尚未发生的需求让路。
- `ExpandableDeviceTable` / `ExpandableHostTable` 的 body 行仍是 `hover:bg-muted/50`，
  且 `isExpanded && 'bg-muted/50'`——如果哪天展开行需要压在 sticky 表头上方，那要按
  z-index 分层表来定，不该顺手改 alpha（#2614 的避让规格同族）。
