# Plan 编辑脏状态完整化 + 跨端刷新草稿守卫（#966/#967）

Status: implemented
Class: bug-fix

## Decision

**#966（R12-F02 / R05-F03）**：`isDirty` 的草稿快照此前只含
`name/description/failureThreshold/nextPlanId/lifecycle`，漏掉归属项目/专项/套件
三个绑定字段——只改绑定仍显示「已保存」、保存按钮禁用、「发起测试」不要求先保存
（`handleExecute` 仅 dirty 时确认），实际用旧定义派发。修正：

- 抽出统一快照 `draftSnapshot` / `planDraftSnapshot`（`planEditUtils.ts`），orig
  与 current 走同一函数，键序一致、脏检查稳定；
- 空表单基线 `EMPTY_DRAFT_SNAPSHOT` 取代原先 `useState('')`——空串与任何 JSON
  都不相等，远端守卫引入后必须显式基线，否则首帧即被误判为「有草稿」。

**#967（R12-F03 / R05-F04）**：`plan` 引用变化（跨端 `PLAN_CHANGED` 失效重取、
visibilitychange 全量 invalidate）时编辑 hook 无条件回填全部字段与 orig 快照，
抹掉本地草稿；后端乐观锁只保护提交冲突，保护不了已被刷新抹掉的编辑。修正：

- dirty 时不再回填（本地草稿优先）；页面出现提示条「该 Plan 已在其他会话更新；
  本地草稿已保留，保存时将按乐观锁校验」，提供 [重新加载远端版本] / [继续编辑]；
- 「继续编辑」记录 dismissed 远端快照：同一远端版本的重复刷新不再反复提示，
  远端内容再变则重新提示；
- 新增 `baseUpdatedAt`（草稿基准版本）作为乐观锁令牌：远端变更被挂起时不推进，
  「继续编辑」后保存仍以旧基准提交、由后端 409 拒绝互相覆盖；加载/重载/保存
  成功时推进；
- 非 dirty 时保持原语义：静默应用远端版本。

涉及：`frontend/src/pages/orchestration/{planEditUtils.ts,usePlanEditForm.ts,
PlanEditPage.tsx}` + 对应测试。

## Alternatives

- #967 只在 hook 内静默保留草稿、不加提示：用户不知远端已变，保存时突然 409
  无法理解；且验收要求「用户可选择」。
- 提示条只给 [重新加载]（不可关闭）：持续编辑的用户每次刷新都被打扰。
- dismissed 用 boolean 记录：远端内容再变时无法重新提示。
- 乐观锁令牌继续取最新 `plan.updated_at`：会让「继续编辑」后的保存绕过锁、
  静默覆盖远端修改（违背验收「走乐观锁」）。
- AbortController/阻断 query 刷新：失效来自全局跨端同步（Plan 列表与详情同时
  失效），单页阻断会破坏其它缓存一致性。

## Verification

- `npx vitest run src/pages/orchestration/` → 27/27 通过（新增 4 个页面用例 +
  2 个 utils 用例：绑定量变更标脏、远端变更保草稿+重载、继续编辑+锁基准不推进、
  非 dirty 静默应用）
- 红绿验证：未修复源码上 `#966`、`#967 重载`、`#967 继续编辑+锁基准` 三个用例
  失败；修复后通过
- `npm run type-check`、`eslint src/pages/orchestration --max-warnings 0` 通过

## Revisit

- 若未来 Plan 编辑改为自动保存或引入字段级合并，提示条与 `baseUpdatedAt` 需一并
  重设计；
- 若后端 409 响应携带最新版本内容，可把「继续编辑」升级为字段级冲突展示。
