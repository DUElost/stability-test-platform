# 页面宽度预设从六档收成四档

- **状态**：已实施
- **类别**：simplification
- **日期**：2026-08-21
- **关联**：`docs/design/2026-08-21-frontend-page-shell-spec.md`（规范本体）、
  `docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 第 14 位（A7）

---

## 决定了什么

### 1. 分档依据从像素改为内容类型

旧的六档 `narrow`(768) / `list`(1024) / `default`(1152) / `wide`(1280) /
`logs`(1480) / `full`，其中中间三档每档只差 128px。没有任何页面是
「在 1152 成立、在 1024 崩掉」的 —— 这三档承载的不是设计意图，
是十五个页面各自随手挑的结果。

新四档按**内容类型**分，判定树只问三个问题、命中即停：

```
① 自管布局的面板/控制台 → bleed
② 宽数据表（≥8 列或需横向滚动）→ wide
③ 单列表单 → form
否则 → content（默认档）
```

**收益不在合并了三个数值，在于宽度选择从"挑一个数"变成"判一次类型"** ——
后者有确定答案，前者没有。默认值也从 `wide` 改成 `content`：拿不准时
落到默认档应该是安全的多数派，而不是最宽的那档。

### 2. `bleed` 收进宽度枚举，不再是布尔开关

旧 API 的 `fullBleed` 是独立布尔，且在 `PageContainer` 内**优先于 `width`**。
`PlanRunLogsPage` 因此写了 `width="logs" fullBleed` —— `width` 被静默忽略，
`logs`(1480px) 那一档**从未生效过，也没有人发现**。

本轮实测证实了这一点：该页迁移前后实宽都是 1696px（1920 视口减侧栏），
padding 0 —— 一直就是贴边全宽。改档位对它零视觉变化。

收进枚举后，「两个参数互相覆盖、错的那个不报错」这类 bug 在类型层面不成立。

### 3. 规范先行，不是边改边定

A7 在审查里排第 14 位，用户可感知伤害只有"切页时内容区宽度跳动"，不大。
**做它的理由不在现有 24 个页面，在下一批**：ADR-0029 P2 新增的
`ProjectsPage` / `ProjectDetailPage` 都选了 `list`，而当时没有任何规则
告诉它们该选什么 —— 选对是运气。

所以先出规范文档、再迁代码，而不是边改边定。规范里留了一条守卫：
**想加第五档，先说清它承载什么类型差异而非像素差异** ——
旧的六档就是靠像素差异繁殖出来的。

## 放弃的备选

- **保留六档、只写选择规则** —— 128px 三档的区分依然需要逐页拿主意，
  规则写了也执行不了。
- **收成两档（content / bleed）** —— `settings` / `change-password` 会变宽，
  单列表单拉长到读不到标签；`devices`(12 列) 会被 1152 截断。
- **给 `wide` 设上限（如 1920）** —— `devices`(547 行 × 12 列) 与 `hosts` 的
  列数应当决定宽度。留作重议钩子：出现超宽屏不适用的证据时再加。
- **把 `FileServerPage` / `PlanExecutePage` 直接改 `content`** —— 见下。

## 与规范原表的一处偏差

规范 §3 初稿把 `FileServerPage` 归 `content`、`PlanExecutePage` 归 `wide`。
实施时发现两页原本用 `fullBleed` **却都自带 `className` 内边距**
（`p-4 lg:p-6` / `p-4`，实测 24px / 16px）—— 它们要的不是"贴边"，
是"内边距比 `lg:p-8` 小"。**这不是宽度档位能表达的诉求。**

本轮先原样落 `bleed` 保持渲染不变，归属留待目视决策，规范 §3 末已改写记录。
硬按初稿归类会改变两页的实际渲染，且解决不了真正的诉求。

## 如何验证

```bash
cd frontend
npx vitest run                     # 86 files / 635 tests passed
npx tsc --noEmit                   # 0
npx eslint src --max-warnings 0    # 0
```

`PageContainer.test.tsx` 改写为四档判据：默认落 `content`、四档各自映射、
只有 `bleed` 去掉容器内边距、**枚举保持四档**（加第五档会红）。

**DOM 实测（Playwright，1920×1080，computed style）8/8**：
`form`=768px / `content`=1152px / `wide`·`bleed`=`none`；`bleed` 页面容器不施加
`lg:p-8`(32px)。`Dashboard` 收窄 128px 后目视无回退。

> 量的是 computed style 而非类名 —— `#351` 的 `cn()` 参数顺序覆写陷阱证明过
> 「磁盘是新代码、渲染是旧值」，宽度由 `cn()` 拼出，属同类风险。
>
> 首轮断言写错过一次：给 `bleed` 断言 `padding-left === 0`，
> 而两个 bleed 页自带 className 内边距。改为「容器不施加 32px」后 8/8。
> 教训：断言要对着**容器的职责**写，不是对着最终像素写。

## 何时重议

- **新增页面时**：走判定树。判不出就是 `content`，不要新增第五档。
- **`PlanEditPage` / `PlanRunDetailPage` 纳管时**：两页目前无 `PageContainer`，
  是本规范唯一未覆盖的路由页（`PlanEditPage` 同时是 A8 的唯一页头违例）。
- **`FileServerPage` / `PlanExecutePage` 归属定下来时**：若确认贴边正确，
  它们自带的 padding 应下沉为规范的一部分，而不是页面各写各的。
- **出现超宽屏（≥2560px）下 `wide` 不适用的证据时**：考虑给它加上限。
