# PlanRun 侧栏 Hero 操作条三按钮溢出修复（#3484）：flex-wrap 两行排布

Status: implemented
Class: bug-fix

## Decision

诊断（vite dev + Playwright chromium-1237 实测，1366/1440/1920 三视口一致）：`PlanRunHeroActions`
三按钮均带 Button 基类 `whitespace-nowrap` + `px-3`，min-content 为 查看快照 96 / 导出报告
124 / 中止运行 96（基类 `[&_svg]:size-4` 以特异性 0-1-1 覆盖 `h-3 w-3`，图标实渲染 16px），
加 2×gap-1.5 共 328px；而 `w-72` 侧栏 Hero 操作行只有 ~229px（288 − p-3×2 − px-4×2）。按钮
`flex-1` 但 `min-width:auto` 不可缩到内容以下 → 行溢出实测 87px，Hero 卡根节点
`overflow-hidden` 裁掉行尾「中止运行」（右缘 361 vs 卡缘 275，仅露 ~10px）。回归点
`8fcf5506`（#3350）加入第三按钮；此前行内两按钮 226px 恰好塞下。

修复（frontend/src/components/plan-run/PlanRunHero.tsx，+5/−2）：容器
`flex gap-1.5` → `flex flex-wrap gap-1.5`，快照按钮 `flex-1` → `w-full`（flex-basis 100%
独占首行），导出 + 复跑/中止自然落到第二行。JSX 结构零改动；行宽未来再涨时 wrap 兜底为
换行而非静默裁剪。

## Alternatives

- **显式两行 JSX（快照行 + 操作行容器）**：效果等价，但需把 ~90 行 JSX 移层重排缩进，
  diff 噪音大；wrap 方案语义相同、diff 最小，首选。
- **压按钮尺寸硬塞一行（px-2 / 10px 字号 / 真 12px 图标）**：被否——估算仍需 ~250px，
  余量为负不可靠，且伤可读性。
- **操作条挪出侧栏**：被否——位置已反复过一次（75142099 移出底栏 → 40d9922b 回 Hero
  原位），方向级反复不因本单重开。

## Verification

- vitest：`frontend/src/pages/execution/PlanRunDetailPage.test.tsx` 28/28 通过（按钮
  渲染、中止确认流、导出菜单行为不回退）。
- 真实浏览器 rig（jsdom 无布局引擎，几何断言按 testing.md §4 走 rig；worktree 临时入口，
  验证后已删）：RUNNING 组合快照 229px 独占首行、导出 126 + 中止 97 同行，全部
  right ≤ 卡右缘 275（余量 17px）；SUCCESS 终态组合复跑 97 替换中止同样 inbound。
  修复前中止按钮溢出 +86px 被裁（对照截图 /tmp/planrun-hero-rig-1440.png 修复前、
  /tmp/planrun-hero-fixed-1366.png 修复后）。
- `python scripts/run_gates.py check:quick`：见 PR 评论/CI。

## Revisit

- Button 基类 `[&_svg]:size-4` 使全库 Button 内所有 `h-3 w-3` 图标实渲染 16px，系统性
  错位未在本单处理（本单几何 inbound 不依赖图标缩小）；后续收口需 wrapper 级选择器或
  改基类，波及面大应另立单。
- Hero 增高一行（28px 按钮 + 6px 行距）：侧栏 KPI/链为滚动区可接受；若未来操作按钮再
  增员，考虑收敛为「更多」溢出菜单。
