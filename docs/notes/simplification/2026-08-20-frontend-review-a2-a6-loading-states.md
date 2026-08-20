# 前端 UI 审查 A2 + A6：加载态收敛为 PageSkeleton 家族 + 孤儿组件清账

Status: implemented
Class: simplification

依据：`docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 第 8 位（A2 + A6），
B7 按其 §7 前置（紧凑/舒适属产品决策，钩子「60+ host 目标推进」）显式跳过。

## Decision

- **两套加载形态**（分工写进 `loading-skeleton.tsx` 抬头，与 empty-state
  家族对齐）：首屏结构已知 → `PageSkeleton` 积木家族；局部/短操作 →
  基础 `Skeleton` / `Loader2` spinner（长尾 ~18+24 处，合法形态，不动）。
- **积木**：`Block`（具名尺寸 md=h-32 筛选区 / lg=h-64 表格区）、
  `Cards count`（卡片列表，收编 CardSkeleton，替换三页 LoadingGrid）、
  `List count`（图标+双行列表项，收编 ListItemSkeleton，Notifications
  渠道卡列表）。**积木不开 className 口子**；count 必填且契约
  「= 成品同区实际条目数」。
- **删除**：`LoadingGrid`（columns/component 自由度从未被用过——三页
  全是默认组合）、`StatCardSkeleton`、`TableRowSkeleton`、`StatsGrid` +
  `StatItem`（含 layout barrel 导出）、tokens 的 `SKELETON_BLOCK`
  （复制粘贴的载体本身）。
- 七页迁移：devices/hosts/schedules（Block md+lg）、notifications
  （List 2 + 记录区 Block lg）、plan-runs/plans/scripts（Cards 2/3/2）。

## 与放行指令的偏差（须显式记录）

放行时「StatCardSkeleton/ListItemSkeleton 有落点，收」的判断引用了我的
侦察结论「Devices/Hosts 上块实为统计卡行」——**该结论错误**：两页成品
均无统计卡行（devices 是提示行+表格，hosts 是工具栏+表格）；真正有
统计卡的 Results/Dashboard 用 `DashboardStatCard` 自带 loading。逐条
核实后 `StatCardSkeleton` 无任何落点，按放行者自己的约束 2（无落点即删，
不留「以后用」）执行删除。`ListItemSkeleton` 落点（Notifications 渠道
卡列表）核实为真，收编。

## Alternatives

- **方案 A 平块式**（PageSkeleton = 两块 pulse 的具名化，孤儿全删）
  （否决）：孤儿组件零消费者恰是「有人想对了抽象没接上」的证据——
  CardSkeleton/ListItemSkeleton 的形态正确且现已有落点，删掉是把正确
  抽象连同欠账一起丢。
- **结构保真作为验收标准**（否决）：占位「看起来像」成品不可证伪。
  两条原则分立记录——**高度保真**（占位占住成品高度，防数据到达时
  下弹，可测量）vs **结构保真**（让用户预期即将出现什么，弱原则，
  服务预期不设验收）。本轮验收走测量不走目视。
- **保留 LoadingGrid 与 PageSkeleton 并存**（否决）：三页 LoadingGrid
  也是页面级加载，并存 = A2 收完仍两套页面级词汇；七页一套，
  CardSkeleton 降为模块私有积木，LoadingGrid 名字退役。

## Verification

- 门禁：tsc / eslint（11 改动文件 `--max-warnings 0`）/ 全量 vitest
  **602 用例**（+5：积木尺寸/计数/容器堆叠）/ build 全绿。
- DOM 实测（`/tmp/ui-shot-rig/verify-a2-final.js`，挂起除 auth 外全部
  API 定格加载态 → 放行，1440×900）七页 × 3 断言全过：
  - **形状指纹**：pulse 计数逐页命中（Block 页 2、List 页 6、Cards 页
    6/9/6）——积木组合错一档即报警；
  - **结构地板高**：骨架内容高 337–617px，逐页下限断言；
  - **零残留**：放行后 `.animate-pulse` 清零。
- **高度保真测量的诚实结论**：骨架 vs 成品全高 Δ = 282–4271px，由列表
  长度主导（hosts 34 行 2418px、scripts 4680px），**任何固定阈值在页面级
  不可执行**——高度保真原则的可执行域是区块级（页中段）占位；成品不
  超首屏的页（schedules Δ=282、plans Δ=223）保真自然成立，佐证测量
  方法本身有效。

## Revisit

- `Block` 现只有 md/lg 两档；新档位需求出现时加具名变体，不开放
  className/任意高度。
- count 契约（=成品实际条目数）靠评审与形状指纹断言把关，无 lint 强制。
- 若未来出现**页中段区块级**骨架需求（AnomalyDashboard 那类），Δ 阈值
  断言在那个尺度才可执行，届时按双原则分立记录验收。
- B7（密度与分页）按 §7 钩子等 60+ host 目标推进，届时升第 1 位。
