# 窄屏下定时任务/审计日志页表格区被压至 0 高（#750）

Status: implemented
Class: bug-fix

## Decision

`SchedulesPage` / `AuditLogPage` 在 overflow 系列修复中改为
`PageContainer scrollable={false}` + 表格区 `min-h-0 flex-1 overflow-auto`（表单/分页
固定、表格内滚）。当页头 + 筛选/新建表单折行后的高度 ≥ 视口时，flex 给表格区的剩余
空间为 0；`min-h-0` 允许其真的塌到 0，而外层 `scrollable={false}`（无 overflow）+
`AppShell main` 的 `overflow-hidden` 让溢出的分页/表格**不可达**——窄屏边缘下整页
无滚动保底。

修复取「高度下限 + 外层兜底」两件套（两页同型）：

1. 表格区 `min-h-0` → `min-h-[240px]`：保留内层滚动语义（显式 min-height 不阻止
   收缩到 240px 以上可用空间），同时禁止塌到 0；
2. `PageContainer` className 增补 `overflow-auto`：`scrollable={false}` 的原始语义是
   「不把页面当主滚动容器」，但页面必须是**溢出兜底**——表单+分页高于视口时退化回
   页面滚动，否则被 `AppShell main` 裁掉。

两页各配结构回归用例（jsdom 无布局，断言契约类名），并新增
`AuditLogPage.test.tsx`（此前该页无测试）。

## Alternatives

- **只加 `min-h-[240px]`（issue 建议的第一选项）**：弃——无头实测（见 Verification
  ②）表明表格区不再为 0，但页面 `scrollTop` 仍恒为 0、分页不可达：`overflow: visible`
  的内容被 `AppShell main` 的 `overflow-hidden` 裁掉，高度下限单独用不够；
- **只加外层 `overflow-auto`（issue 建议的第二选项）**：弃——`min-h-0` 仍允许表格区
  被压到 0，外层滚到底也看不到表格（0 高的盒子）；
- **改 `PageContainer` 让 `scrollable={false}` 也带 overflow-auto（全局兜底）**：弃
  ——会改变 `PlanEditPage`/`PlanRunLogsPage` 等自管面板页的既有契约（属性语义被架空、
  既有 `PageContainer.test` 明确断言 false 时无 `overflow-auto`），超出本单范围；
  若日后同类反馈再现，可另开 Requirement 统一；
- **用 flex 的默认 `min-height:auto`（去掉 min-h-0）替代 240px 下限**：弃——那会让
  表格区永远撑到内容高度、内层滚动彻底失效，等于放弃「表单/分页固定」的既有设计。

## Verification

- **无头 CSS 模型实测**（`chrome-headless-shell`，1024×600，结构同 AppShell
  main(overflow-hidden) → PageContainer(flex h-full) → header 120 + 超高表单 520 +
  表格 + 分页 60；`page.scrollTop = 99999` 后读回）：

  | 方案 | 表格高 | page overflowY | scrollTop 实测 | 分页可达 |
  |---|---|---|---|---|
  | ① 现状（min-h-0，无兜底） | **0** | visible | 0 | 否 |
  | ② 仅 min-h-[240px] | 240 | visible | 0 | **否** |
  | ③ min-h-[240px] + 外层 overflow-auto（本修复） | 240 | auto | >0 | **是** |

  ②被证伪是本单选择「两件套」而非 issue 任一单选项的直接依据；
- **真实 Tailwind 编译**：用仓库 `@tailwindcss/postcss` 对 worktree `src` 扫描，
  确认 `.min-h-[240px]{min-height:240px}` 与 `.overflow-auto{overflow:auto}` 均生成
  （任意值类可被扫描器识别）；
- **结构回归用例**：`SchedulesPage.test.tsx` 新增用例 +
  新建 `AuditLogPage.test.tsx`（覆盖表格分支渲染后的容器/表格区类名契约）；
- 前端全量：`npx vitest run` → **758 passed**（103 files）；`npm run type-check` → 通过；
  `npx eslint src --max-warnings 0` → 通过；
- `python scripts/run_gates.py check:quick` → `[OK] check:quick (7 gates)`。

未做：真实浏览器内跑应用目视（1024×600 真机渲染 + 交互滚动）。上述无头实验用的是
等价结构模型与仓库真实 Tailwind 产物，不是应用整页渲染；差一个「真机上点开筛选/表单」
的目视确认，issue 本身也标注「09-02 复核时未实测触发」。

## Revisit

- **`PlanRunLogsPage` 同型未改**：该页也是 `scrollable={false}` + `min-h-0` 内滚，
  固定区（页头+过滤条+分页）在极窄视口下同样可能挤掉列表区；本单按 issue 范围只改
  两页，若复现按同法处理或统一到 PageContainer 层；
- **240px 是经验下限**：未从真实设备视口分布反推；若出现「表格区仍太小」的反馈，
  可改为与视口挂钩（如 `min-h-[40vh]`）或在 PageContainer 提供档位；
- **溢出兜底与内层滚动的滚轮链**：外层重新成为滚动容器后，理论上存在「内层到底后
  滚轮链到外层」的行为变化；overflow 系列修复针对的是 `overflow-hidden` 吞滚轮，
  `overflow-auto` 不吞，但真机手感未复核（见「未做」）。
