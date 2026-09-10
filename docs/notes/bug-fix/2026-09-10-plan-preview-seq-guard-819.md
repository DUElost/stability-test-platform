# 预览请求代次守卫：改选后丢弃在途响应（#819，R12-F01）

Status: implemented
Class: bug-fix

## Decision

`handlePreview` 增加代次守卫：`previewGenerationRef` 在两类事件递增——
`previewResetKey`（`selectedPlanId|selectedDeviceIdsKey`）变化时的提交后
（`useLayoutEffect`，与渲染期重置分支同一次提交；ESLint `react-hooks/refs`
禁止渲染期写 ref），以及每次发起新预览请求。响应返回（成功或失败）时若代次已变，
直接丢弃，不再 `setPreview`、不弹 toast；`finally` 仍收尾 `setPreviewing(false)`。

根因：`previewResetKey` 的重置只发生在渲染期，与在途 `previewRun` 响应之间没有
因果——返回修改/改选后旧响应到达会无条件 `setPreview`，复活冻结的旧设备集；
「确认发起」按 `preview.device_ids` 派发，被移除设备照常执行（数量差 1 时视觉
几乎不可辨）。守卫同时替代了此前隐含假设（响应返回时选择必然未变）。

涉及：`frontend/src/pages/execution/PlanExecutePage.tsx`（代次 ref + 重置
effect + `handlePreview`）。不改 API 层、不动 `previewResetKey` 组成、不改变
「返回修改」保留已生成预览的既有语义。

## Alternatives

- `AbortController` 取消在途请求：需把 `signal` 透传进 `api.plans.previewRun`，
  改动扩到 API 客户端层，收益（省一次后端计算）与本缺陷无关。
- 仅按 `previewResetKey` 值比较（不引入代次）：等价于第一类递增，但无法覆盖
  「同选择下重复预览、先发后至」的顺序问题；代次一并覆盖。
- 把 `preview` 对象纳入 reset key 链：改变状态结构，影响面更大。
- 在 `handleConfirm` 侧兜底（派发前重新校验设备集）：只在提交点止血，
  预览驾驶舱仍展示被移除设备，误导未消除。

## Verification

- `cd frontend && npx vitest run src/pages/execution/PlanExecutePage.test.tsx`
  （54/54 通过）
- 新增回归用例 `discards a stale in-flight preview after the device selection
  changes (#819)`：在途预览 → 返回修改并移除 DEV-2 → 旧响应（含 DEV-2）到达 →
  断言旧预览不复活、`api.plans.run` 仅以 `[1]` 派发；已做红绿验证（未修复代码上
  该用例失败）。

## Revisit

若未来把预览请求改为可取消（AbortController）或引入请求管理层（React Query
mutation），代次 ref 可由其原生机制取代；届时保留同一回归用例。
