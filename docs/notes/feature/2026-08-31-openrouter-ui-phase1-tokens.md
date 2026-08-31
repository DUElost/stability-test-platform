# OpenRouter UI Phase 1：design tokens

Status: implemented
Class: feature

## Decision

按 #464「tokens → 框架 → 逐页」节奏，本 PR 只落地视觉基线换皮，不改外壳接线与业务页结构。

- 双主题主色统一葡萄紫 `263.7 90.4% 54.9%`；`--ring` 跟随（dark 略提亮 `62%`）。
- 浅色画布 / 半透明 border（fg `/ .078`）；浅色 `--input` **实色**（`197.1 25% 92%`），与 border 分开，避免表单描边近乎消失。
- 深色用锌灰套 `224 7% 4%` + 实色 border/input；**不用**导出里冷黑 + 荧光 primary 那套。
- `--info` 族保持蓝，与 primary 语义分离。
- `--radius: 0.5rem`：仅 `rounded-sm/md/lg` 的 var 派生变化；硬编码 `rounded-xl/2xl/[Npx]`（~59 处）不动 → 视觉预期是「控件变紧、多数卡片圆角未变」。
- 字体：Plus Jakarta Sans Variable + Noto Sans SC（CJK）；mono 前置 Geist Mono。均经 `fonts.ts` 动态 chunk。
- body `font-weight: 450`；标题 tracking `-0.02em`（PageHeader / CardTitle / DialogTitle / PlanCanvas 四处齐改）。
- 声明 `--sidebar*` 并挂 `@theme`，**接线留给 Phase 2**。
- `CHART_COLORS`：primary/destructive 对齐；palette **6 色**——前 5 来自 OR **stats 页实测**（`#0088fe` / `#00c49f` / `#ffbb28` / `#ff8042` / tomato），**不是** shadcn `chart-1..5` oklch；第 6 `#8b5cf6` 保留因 `DeviceMetricsChart` 直取 `palette[5]`（CPU）。`XTerminal` 主题蓝不动。

涉及：`frontend/src/index.css`、`fonts.ts`、`design-system/colors.ts`、`PageHeader` / `card` / `dialog` / `PlanCanvas`、`package.json`（字体依赖）。

## Alternatives

- Dark 跟荧光黄绿 primary / 冷黑画布：全站主色爆炸，放弃。
- 浅色 input 跟 `.078` border：表单描边不可读，放弃。
- palette 缩到 5 色：`DeviceMetricsChart` 静默丢 CPU 色，放弃；也不改该组件索引（守 tokens 边界）。
- 一次改完 Sidebar/卡片阴影/逐页：#464 要求渐进，留给 Phase 2–4。

## Verification

```bash
cd frontend && npm run type-check && npm run lint && npx vitest run && npm run build
```

PR required checks 不含 vitest/build；本地必须跑。build 后确认阻塞 CSS gzip 仍大体 < 30KB（Noto 动态）。

## Revisit

- Phase 2：✅ `docs/notes/feature/2026-08-31-openrouter-ui-phase2-shell.md`（Sidebar / 阴影 / destructive AA）。
- Phase 3：逐页；若收敛卡片圆角，~59 处硬编码是待办来源。
- Phase 4：`docs/design/mockups/plan-execute-v2/styles.css` 同步。
- body `450`：Noto 动态加载前 CJK 回落字体可能短暂命中 700（窗口极小）；若首屏「闪粗」投诉再议字体加载策略。
