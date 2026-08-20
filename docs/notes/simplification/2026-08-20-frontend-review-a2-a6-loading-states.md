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
  全是默认组合）、`TableRowSkeleton`、`StatsGrid` + `StatItem`（含
  layout barrel 导出）、tokens 的 `SKELETON_BLOCK`（复制粘贴的载体本身）。
- **收编三个孤儿**：`CardSkeleton` → Cards 积木；`ListItemSkeleton` →
  List 积木（Notifications 渠道卡列表）；`StatCardSkeleton` → Stats 积木
  （**落点在子组件**：`ExpandableDeviceTable` / `ExpandableHostTable`
  首屏的常驻筛选统计卡行——设备 5 卡、主机 4 卡，页面 loading 分支提前
  return 时它们尚未挂载）。devices/hosts 加载态用 `Stats count + Block lg`，
  `Block md` 仅保留给无统计行的上块（schedules）。
- 七页迁移：devices/hosts（Stats 5/4 + Block lg）、schedules（Block
  md+lg）、notifications（List 2 + 记录区 Block lg）、
  plan-runs/plans/scripts（Cards 2/3/2）。

## 侦察方法教训（本主题两次方向相反的失误，留档防再犯）

StatCardSkeleton 的落点判断反复了两次：第一次简报说「有」（未核实），
第二次实施时说「没有」（只 grep 了 pages/ 下两个页面文件）——统计卡行
渲染在 `Expandable*Table` 子组件内部，页面级结论**必须沿渲染树查到
子组件**，grep 页面文件不构成证据。同理，形状指纹断言会把当期形态锁死
为「正确」，它锁定什么取决于侦察给它什么——指纹只防回归，不辨对错。

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
- StatCardSkeleton 收编补录（#321 后续 PR）：devices/hosts 形状指纹
  更新为 Stats(5)×3+lg=**16** / Stats(4)×3+lg=**13**（schedules 仍 2），
  并以成品卡数锁定（设备页 5、主机页 4 个 `aria-label^=筛选` 按钮）
  防止骨架与子组件漂移。

## Revisit

- **两条保真原则按尺度互换**（放行方复盘修正）：高度保真（可测量、
  防下弹）只在**区块级**可执行；**页面级**高度由列表长度主导不可控，
  结构保真（形状指纹）反而是那里唯一可执行的代理。下次再想在页面级
  加高度断言时，先读这条。
- `Block` 现只有 md/lg 两档；新档位需求出现时加具名变体，不开放
  className/任意高度。
- count 契约（=成品实际条目数）靠评审与形状指纹断言把关，无 lint 强制。
- 若未来出现**页中段区块级**骨架需求（AnomalyDashboard 那类），Δ 阈值
  断言在那个尺度才可执行，届时按双原则分立记录验收。
- B7（密度与分页）按 §7 钩子等 60+ host 目标推进，届时升第 1 位。
