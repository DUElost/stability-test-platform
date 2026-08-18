# 前端大组件补测试 + 设计文档校漂移

- **状态**：已实施
- **类别**：testing
- **日期**：2026-08-18
- **关联**：前端体检 F4 / F5（延续 PR #292 的 F1–F3）

---

## 决定了什么

### F4：给三个「行数最多且零测试」的组件补上测试

体检时这三个文件合计 1734 行、0 个测试用例，且都在编辑/配置主路径上——改错了不会崩，只会静默写出畸形 payload：

| 文件 | 行数 | 新增用例 | 锁住的是什么 |
|------|------|---------|-------------|
| `components/pipeline/PlanCanvas.tsx` | 532 | 32 | 回吐给父级的 `PipelineDef` 形状 |
| `components/pipeline/PlanStepInspector.tsx` | 576 | 31 | 回吐的 `PipelineStep.params` |
| `pages/notifications/NotificationsPage.tsx` | 626 | 30 | 发给后端的 channel/rule payload |

断言一律打在**回调入参**上，不打 DOM 顺序。这三个组件都是纯受控的，DOM 只能证明渲染了什么，证明不了写回去的对象长什么样，而后端 422 恰恰只看后者。三处最典型的：

- `PlanCanvas` 删掉最后一个 patrol 步骤时必须**摘掉整个 `patrol` 键**——留一个 `{interval_seconds, steps: []}` 空壳会被 pipeline schema 拒掉。
- `PlanCanvas` 复制步骤时副本号要**扫全部 phase**再递增。只扫本 phase 的话，另一个 phase 里的同名副本会撞 ID。
- `PlanStepInspector` 的参数取值是 `step.params` → `script.default_params` → `schema.default` 三层，且**改回默认值就删键**。这条只在 payload 里看得见。

顺带锁住几个反直觉的数值分支（都加了注释说明为什么是这个值）：patrol 间隔填 `0` 落回 60 而不是夹到下限 5；步骤超时填 `0` 落回 30，想要「不限」必须清空输入框。它们都源自 `parseInt(raw) || default` 的 falsy 短路，看代码容易读反。

### 附带修掉一个死分支

写告警测试时发现 `PlanStepInspector.tsx` 的「版本已停用」提示不可达：`matchedScript` 不过滤 `is_active`，停用版本照样匹配上 → `isUnknown` 恒为 false → 它的严格子集 `deactivatedMatch` 永远算不到。结果是引用了停用版本的步骤**一句告警都不给**，用户要到派发期 `script_verify_failed` 才知道。

改法是只动判定、不动 `matchedScript`：

```ts
const isUnknown = !!scriptName && (!matchedScript || !matchedScript.is_active);
```

保留 `matchedScript` 匹配停用版本是有意的——参数表单仍要靠它的 schema 把已填的值显示出来，否则用户连改都没法改。

### F5：`docs/design/03-frontend.md` 校漂移

八处与代码对不上的地方：技术栈版本、路由表（多了已删的 `/…/matrix`，缺 `/results` 和 `/account/password`，admin 段不全）、API 模块表缺 5 个模块、PlanRun 组件表按 `components/plan-run/` 现状重写、Tailwind 4 的 dark variant 声明位置、测试文件计数。另加一节「分包」，把 `vendor-cn` 与 `src/fonts.ts` 两处首屏体积硬约束写进设计文档——PR #292 只把理由写在代码注释里，设计文档一句没有，下一个人重排 `manualChunks` 时看不到。

删掉 §9「方案 C 前端债（跟踪 #32）」：#32 与 #16 都已关闭。

## 放弃的备选

- **给 `PlanExecutePage.tsx`（1357 行）补测试** —— 它已有 1312 行测试覆盖，不属于「零测试」。
- **拆分这三个组件再测** —— 先测后拆才安全。拆分本身对用户无感知，属开发者收益，排在补测试之后。
- **改 `matchedScript` 为只匹配激活版本** —— 会让停用版本的参数表单整个消失，用户连改都改不了，比不告警更糟。
- **顺手修那两个 `parseInt(raw) || default` 的反直觉分支** —— 它们是既有行为，可能有人已经在依赖；本次只用测试把语义钉住并写清楚，改不改另议。

## 如何验证

```bash
cd frontend
npx vitest run          # 77 files / 561 tests passed
npx tsc --noEmit        # 0
npx eslint src --max-warnings 0   # 0
```

新增 93 个用例，全套 561 个用例无回归。三个新测试文件均不连网络：`PlanCanvas` / `PlanStepInspector` 是纯受控组件直接渲染，`NotificationsPage` 只 mock `api.notifications` 与两个 hook，`toApiError` 保留真实实现（要验后端 `detail` 能原样透到 toast）。

## 顺带记一个未修的显示问题

契约里 step `timeout_seconds` 是 required integer / minimum 0，**「不限」编码为 0**
（`backend/schemas/pipeline_schema.json` §step，且 0 额外要求 `stall_seconds ≥ 1`）。
但 `StepRow` 判的是 `!= null`，于是配成「不限」的步骤在画布上显示 `0s` 而不是 `∞`——
`∞` 那条分支只有 schema 收紧前落库的老 Plan（`pipeline_def` 是 JSONB，历史行不回填）
才走得到。本次只用测试把两条分支都钉住，显示语义要不要改另议：改成把 0 也渲染成 `∞`
会影响所有 Plan 的读法，不该搭在补测试的 PR 里。

同理，**不要**把 `types.ts` 的 `timeout_seconds` 放宽成 `number | null`。编辑器一旦
能产出 null，保存时会被后端 schema 直接拒收——测试里那个 `LEGACY_NULL_TIMEOUT`
是为覆盖老数据显式构造的，不是契约。

## 何时重议

- 三个组件任一被拆分时——测试断言的是行为不是结构，拆分后应当原样通过；通不过说明拆分改了行为。
- `PipelineStep` / `PipelineDef` 类型变更时，`PlanCanvas` 的 patrol 键删除断言与 `PlanStepInspector` 的三层取值断言需要跟着走。
- 若决定统一 `parseInt(raw) || default` 这类 falsy 短路的语义（当前 patrol 间隔、步骤超时各有一处），对应的两个用例要一起改。
