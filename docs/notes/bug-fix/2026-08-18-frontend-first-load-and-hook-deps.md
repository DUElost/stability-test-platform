# 前端首屏体积与 Hook 依赖收敛

Status: implemented
Class: bug-fix

## Decision

三项改动，冷启动首屏从 362.6 KB gzip 降到 208.0 KB（-42.6%）。

**1. `clsx` / `tailwind-merge` / `class-variance-authority` 独立成 `vendor-cn` 分包**
（`frontend/vite.config.ts`）。

`manualChunks` 此前对这三个包返回 `undefined`，打包器便把 `clsx` 并进了同样依赖它的
`vendor-recharts`。而 `cn()` 被 80+ 文件引用，`design-system/tokens` 与 `button` 又在
首屏必经图里，产物中出现：

```js
// dist/assets/tokens-*.js  ← 首屏必拉
import { _ as clsx } from './vendor-recharts-*.js'
```

结果是为一个几百字节的工具函数，把 410 KB 的图表库拽进冷启动路径：`dist/index.html`
的 `modulepreload` 列表里坐实 `vendor-recharts`，+114 KB gzip，占当时首屏的 31%，
只打开 `/login` 的用户同样要付。新增规则排在所有重量级 vendor 之前。

**2. 字体声明移出阻塞样式表**（`frontend/src/fonts.ts`、`main.tsx`）。

`@fontsource-variable/noto-sans-sc` 为覆盖 CJK 拆了 101 个 `@font-face` 块（含若干
emoji 区段），仅 CSS 就 101 KB / 31.5 KB gzip，占 `index.css` 的 53.7%。原先在
`main.tsx` 静态 import，被并进 `<head>` 的阻塞样式表——但这些声明并不画像素，woff2
本就是 `font-display: swap`，先用回落字体出字再换字。改为 `void import('./fonts')`
后 Vite 单独产出一份 CSS 由 JS 注入，关键 CSS 58.6 → 14.3 KB gzip。

**3. 四处 Hook 依赖收敛**（`DispatchGateCard.tsx`、`LiveConsole.tsx`、`PlanRunHero.tsx`）。

- `DispatchGateCard`：`hostEntries` 原为裸 `Object.entries(gate.hosts)`，每渲染换引用，
  致使以它为依赖的 `totalScripts` / `allScriptsOk` 两个 `useMemo` 长期空转。改为
  `useMemo(..., [gate.hosts])` 后三个 memo 同时生效，`counts` 的抑制随之消除。
  另有一处配套前提：`precheck` 为空的 dispatchOnly 路径下 `gate` 是每渲染重建的字面量，
  内联 `hosts: {}` 每次都是新引用，会让该 memo 在这条路径上失效——`hosts` 须指向模块级
  `EMPTY_HOSTS` 常量。两处必须同时成立，只改其一等于没改。
- `LiveConsole`：`tallyIssues` 包 `useCallback([enableIssueCount])`，`replayFromStart`
  依赖改为 `[consoleRunId, tallyIssues]`。两者标识变化时机与原 `[consoleRunId,
  enableIssueCount]` 完全一致，行为不变，抑制消除。
- `PlanRunHero` 两处：`tick` 是刻意多加的每秒驱动依赖（不在函数体内使用），属 #260
  待统一的 tick 状态模型，**保留抑制并补理由注释**，不在本 PR 重构。

`PlanExecutePage.tsx` 的 `exhaustive-deps` 抑制原就带理由注释（prefill 只消费一次），
未改动。

## Alternatives

- **字体按 subset 裁剪**：该包版本未提供 per-subset CSS 入口，只能手写 `@font-face`
  块。会在每次升包时静默漂移，且丢弃区段意味着生僻字回落到系统字体、出现字形不一致。
  弃用——收益不及异步化，风险却高得多。
- **`PlanRunHero` 去掉 `useMemo`**：计算只是两次 `Date` 解析，去掉 memo 即可消除
  「多余依赖」告警且语义等价。但该组件无直接测试，且 tick 模型统一是 #260 的范围，
  在体积 PR 里顺手重构属于扩大 diff。改为补注释。
- **`vendor-recharts` 保持现状、改用 `<link rel=preload>` 抵消**：治标。图表库仍在
  关键路径上被解析执行，且掩盖了分包规则缺失这个真因。

## Verification

```bash
cd frontend
npx tsc --noEmit                 # 0 error
npx eslint src --max-warnings 0  # 0 error 0 warning
npx vitest run                   # 74 files / 468 tests passed
npx vite build
```

体积回归检测（两条都应成立，否则说明分包规则又被绕过）：

- `dist/index.html` 的 `modulepreload` 列表中**不得**出现 `vendor-recharts` /
  `vendor-xterm` / `fonts-*`；
- `<link rel="stylesheet">` 只有一份，gzip 后 < 30 KB。

首屏 gzip 实测：

| | 改前 | 改后 |
|---|---|---|
| JS + CSS 合计 | 362.6 KB | **208.0 KB** |
| 其中 recharts | 114.0 KB | 0（已移出） |
| 其中 CSS | 58.6 KB | 14.3 KB |

## Revisit

- #260 统一 tick 状态模型时，`PlanRunHero` 两处抑制应随之消除。
- 若首屏再需提速，下一块是 `vendor-radix` 37.9 KB gzip——需先确认哪些 Radix 原语真在
  首屏用到（当前疑似整包进了关键路径）。
- 字体异步化的代价是字体切换比原先稍晚。若实验室反馈首屏字形跳动明显，可改为对最常用
  的少数 subset 做 `<link rel=preload>`，而不是回退到同步加载整份声明。
