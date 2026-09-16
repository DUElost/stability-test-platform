# PlanRun 事件描述截断回归 + 错误面 AA 对比度（#2027）

Status: implemented
Class: bug-fix

## Decision

**1. `line-clamp-2` 被 `block` 压掉（回归自 `1e339766`）——冲突从构造上消除。**
`1e339766` 把描述容器从 `<div>` 换成 `<button>` 并加了 `block`；`line-clamp-2` 自带
`display:-webkit-box`，与 `block` **特异性相同**，胜负只由构建产物里规则先后决定。
本机 `npm run build` 实测（本次修复前的产物）：`.line-clamp-2` 偏移 12756、
`.block{` 偏移 12855——**`.block` 在后**，截断被静默压掉，点「展开」也没有视觉变化。

修法不是在两个 display 之间赌顺序，而是**把截断移到没有 display 工具类的内层
`<span>` 上**：外层 `<button>` 保留 `block w-full text-left`（点击面与左对齐不变），
内层 span 只挂 `line-clamp-2`（或展开态的 `whitespace-pre-wrap break-words`），
`-webkit-box` 不再有竞争者。原 `<div>` 时代能生效，正是因为当时也没有 display 工具类。

**2. 错误面改走 AA 变体令牌。** 同一组件错误分支原先用 `text-destructive/60`（图标）
与 `text-destructive/70`（正文）：`--destructive` 亮色是 `0 84.2% 60.2%`（#ef4444），
白底 3.76:1，叠 `/70` 后 **2.62:1**、`/60` 后 2.28:1——均低于 AA 的 4.5:1。

新增令牌 `--destructive-text`（**只降亮度不改色相**，与 `--muted-foreground`
46.9%→42%、`--primary` 60%→53.3% 同一修法）：

| 主题 | 值 | 白底/深底实测 |
|---|---|---|
| `:root` | `0 74% 42%`（#ba1c1c） | 白底 **6.42:1**、卡片底 6.42:1、bg-muted 上 **5.86:1** |
| `.dark` | `0 84% 70%` | 深色卡片 **6.24:1**、深背景 6.65:1 |

`--destructive` **保持不变**（按钮底、`/10` 底色这类填充用途亮度合适，且改全局会牵动
全站视觉）；两者用途分离，由用例断言「亮色文字令牌更暗、深色文字令牌更亮」钉住。
类名 `text-destructive-text` 已确认出现在构建产物中：
`.text-destructive-text{color:hsl(var(--destructive-text))}`。

**3. 两条守卫，分别针对「类名在、样式失效」的两种形态**（这是本单的核心教训——
组件测试原本只断言 `toHaveClass('line-clamp-2')`，回归发生时它一直是绿的）：

- `PlanRunEventStream.test.tsx`：新增「截断类不得与任何 display 工具类同元素」——
  jsdom 不算样式，但**回归的机制**（display 争夺）可测；另加一条错误面必须整条使用
  `text-destructive-text`、且不得出现 `text-destructive/<数字>` 的断言。
- 新增 `src/design-system/contrast.test.ts`：直接读 `index.css`，按 WCAG 2.x 相对亮度
  公式**算**对比度，把 8 组「已裁决达标的取值」钉住（destructive-text × 亮/暗 ×
  白底/卡片/bg-muted，外加 muted-foreground、primary 的历史裁决值）。改亮度会让它红，
  而不是等用户在界面上发觉。

## Alternatives

- **A. 按 issue 建议 1 直接删 `block`**：可行但脆弱——`w-full` 在 inline-block 的
  `<button>` 上仍成立，可是 `-webkit-box` 作用在 `<button>` 上的表现依赖浏览器实现，
  本机无浏览器可验；且下次谁再加一个 `flex`/`grid` 就复发。内层 span 是**结构性**
  消除（截断目标没有 display 工具类），不是再一次选边站。
- **B. 用 `[display:-webkit-box]` 任意值类压过 `block`**：否决。仍是在同元素上堆
  display 声明赌顺序，只是把赌注换了一边；可读性也差。
- **C. 只把透明度去掉（`text-destructive` 全量）**：**不够**。`--destructive` 白底
  3.76:1，正文与标题仍不达 AA（本单验收项 3 要的就是 AA）。
- **D. 全局调暗 `--destructive`**：否决（本单不做）。它同时是按钮底/`/10` 底色/
  边框的来源，全局改是设计决策，影响面远超本单；且 `--primary` 那次是**用户裁决**
  （2026-09-14）。文字专用令牌是加法的、零外溢。
- **E. 把对比度检查做成快照/pixel 测试**：否决。需要真实渲染与浏览器，PR 路径跑不了；
  读令牌算 WCAG 比值是纯离线、秒级、且判据与设计规范同源。

## Verification

- **红绿差分（先证伪再实现）**：
  - clamp：新守卫 + 改写后的展开用例在 `git checkout` 回的旧组件上 **3 failed**
    （`keeps the clamp class off any element…` / `expands a long event description…` /
    `renders the whole error face with the AA token…`），换回新实现 **27 passed**。
  - 对比度：把 `--destructive-text`（亮色）临时改回旧值 `0 84.2% 60.2%` → 守卫报
    **3.76:1（白底）/ 3.44:1（bg-muted）** 两个数值失败 + 令牌分离断言失败，与 issue
    给出的数字一致；换回新值全绿。
- **构建产物实测**（`npm run build`）：`.text-destructive-text{color:hsl(var(--destructive-text))}`
  已生成；`.text-destructive/70` 在产物中消失；`.line-clamp-2`(12756) 早于 `.block{`(12855)
  的偏移顺序复现了 issue 的前提。
- **测试**（`frontend/` 下运行）：`npx vitest run`（全量）→ **919 例：917 passed /
  2 failed**。两例失败为**本机存量环境差**（Node v24 vs CI Node 22 的
  `PlanRunDetailPage` socket/抽屉两例），已在未改动基线上复现，与本单无关。
  改动面单跑 `PlanRunEventStream.test.tsx` + `design-system/` → 27 passed。
- **类型与 lint**：`npx tsc --noEmit`、`npx eslint --max-warnings 0`（3 个文件）通过。
- **仓库门禁**：`python scripts/run_gates.py check:quick` → `[OK] check:quick (10 gates)`。
- **未做**：未做浏览器/像素级验证（本机无隔离前端环境）——jsdom 不算样式，所以
  「实际截断为两行」由**机制层**断言（无 display 竞争者）+ 构建产物类名存在性共同覆盖，
  而非计算样式断言；这点在 Revisit 里显式留了口子。

## Revisit

- **前端样式回归没有合入门禁**：`frontend-check`（跑 vitest）条件是
  `github.event_name != 'pull_request'`，只在每日 main 全量里跑——本单这类「类名在、
  样式失效」的回归在 PR 路径上**零覆盖**（issue 也点到这条）。要不要把 vitest 纳入
  PR 路径是独立决策（时间预算 vs 覆盖），本单不改。
- **如果将来真要做计算样式断言**：需要浏览器环境（Playwright/Vitest browser mode），
  与上一条是同一个决策面。
- **其它 `--destructive` 文字用法**：本单只收口了 `PlanRunEventStream` 的错误面。
  全站还有多少处以 `text-destructive`（3.76:1）或透明度变体作正文，未清点；若要做
  全站 AA，应统一迁到 `--destructive-text`（或按 `--primary` 先例由用户裁决全局调暗）——
  届时 `contrast.test.ts` 的 CASE 表就是现成的验收位。同理 `--warning`/`--success`
  作正文时的对比度也未清点。
- **`TEXT.destructive` 令牌**（`tokens.ts`）仍指向 `text-destructive`：语义上它现在
  更像「错误**填充/图标**色」。要不要在 tokens.ts 增补 `destructiveText` 供组件引用，
  等有第二处使用场景再定（当前仅一处调用点，直接写类名 + 注释更省一层间接）。
