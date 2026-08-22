# PlanEditPage 页头移进 AppShell 顶栏槽位（UI 审查 A8）

- **状态**：已实施
- **类别**：feature
- **日期**：2026-08-21
- **关联**：`docs/design/2026-08-21-frontend-page-shell-spec.md` §2 页头三形态、
  `docs/reviews/FRONTEND_UI_REVIEW_2026-08-19.md` §6 第 15 位（A8）

---

## 决定了什么

### 1. 页头三形态规范（补进页面外壳规范 §2）

| 形态 | 特征 | 写法 | 现例 |
|------|------|------|------|
| 列表页 | 标题 + 副标题 + 主操作 | `PageHeader`（页内） | 20 页 |
| 详情页 | 返回 + 页签/状态 + 刷新 | `useHeaderSlot` | PlanRunDetail/Logs |
| 编辑页 | 返回 + 面包屑 + 保存/执行 | `useHeaderSlot` | PlanEditPage |

判定一句话：**页面有横贯全宽的操作栏/页签 → slot；只有标题行 → `PageHeader`。**

### 2. PlanEditPage 成为第三页 slot 使用者

`usePlanEditHeaderSlot`（`pages/orchestration/`，与 `usePlanEditForm.ts` 同目录就近放）：
返回按钮 + 「测试计划 › 名称」面包屑 + 已保存/未保存 chip + 查看 JSON/发起测试/保存。
页面主体从 `h-full flex flex-col` 自绘结构改为 `PageContainer width="bleed"`
（三栏自管布局 → 判定树①），`bg-muted/40` 经 className 传入。

双头栏消除：AppShell 顶栏左侧恢复为页面内容空间（原整条留白）。

### 3. slot 注入的依赖策略：回调必须走 ref

`usePlanEditForm` 的回调（`handleSave` / `handleExecute`）是**普通函数、引用不稳定**。
若直接进 effect 依赖数组：每次重注入 → `setHeaderSlot` 换新节点 → Provider 重渲染 →
页面重渲染 → form 重建 → 回调引用又变 → **无限重注入循环**。

解法：显示值（`name` / `isDirty` / `saving` / `isNew`）直接列依赖（随输入重注入，
闭包随 effect 重跑保持新鲜）；回调经 `formRef` 取最新。ref 同步用独立 effect
（**渲染期写 ref 被 `react-hooks/refs` 禁止**，正是 PlanRunHero #358 事故的同一规则）；
同步 effect 先声明、注入 effect 后声明，React 按声明顺序执行 → 注入时必拿到最新。

另一个防循环的隐形保障：`HeaderSlotProvider` 的 `setHeaderSlot` 是 useState setter，
引用稳定；若它不稳定，`usePlanRunHeaderSlot` 现有的写法一样会循环。

### 4. PlanRunDetailPage 的「两份返回链」零代码结案

B 轨观察到的 `usePlanRunHeaderSlot`「返回执行列表」+ `PlanRunDetailPage.tsx:187`
「返回列表」两份文案 —— 187 行位于 `runQ.isError` **错误分支**，即规范 §2 明文裁定的
「唯一例外：错误分支的逃生口」。正常态两按钮不同时可见，不做去重，裁定写回规范。

## 放弃的备选

- **把 `usePlanEditForm` 回调全部 `useCallback` 化** —— 依赖表要重排 200 行表单 hook，
  且并行会话正在同目录活跃，改动面大、冲突风险高。
- **渲染期 `formRef.current = form`** —— 被 `react-hooks/refs` 拦截
  （"Cannot update ref during render"），改 effect 内同步。
- **加载/错误分支也注入 slot** —— 与其余页面不一致（它们加载时顶栏留空）；
  改为 hook 收 `ready` 参数，不 ready 不注入，错误分支保留页内逃生口。

## 如何验证

```bash
npx vitest run                     # 86 files / 635 tests passed
npx tsc --noEmit                   # 0
npx eslint src --max-warnings 0    # 0
```

`PlanEditPage.test.tsx` 按 `PlanRunDetailPage.test.tsx` 的先例加
`HeaderSlotProvider` + `HeaderSlotOutlet`（模拟 AppShell 消费 slot）包装，
back 按钮断言从脆弱的 `getAllByRole()[0]` 换成 `getByRole('button', { name: '返回 Plan 列表' })`。

**DOM 实测（Playwright，1440×900，生产数据只读）6/6**：
main 内无页面级自绘头栏（原特征：≥60px 高 + backdrop-blur；面板内语义级
`<header>` 是合法的，首轮断言因此误报过一次，教训：断言要对准**被禁的特征**
而不是宽泛的标签名）；顶栏 slot 有返回/面包屑（MTBF-专项-冒烟-P0）/保存；
页面容器无容器内边距（bleed）。

## 何时重议

- **新增编辑/详情类页面时**：走三形态判定表，编辑页一律 slot。
- **`usePlanEditForm` 若引入不稳定大依赖**：优先考虑在 form hook 内 memo 化，
  而不是给 slot hook 再开 ref 通道。
