# 一轮多个待审批动作：meta 保存完整集合（#1219）

Status: implemented
Class: bug-fix

## Decision

#1219（R13-F07）：轮次循环里每创建一个 action 就覆盖 `proposed_action_id`，
最终 assistant 消息 meta 只存**最后一个**——前面的 proposed 动作仍然存在
（等待审批），却没有任何操作卡可操作，成为无人能审批的孤儿。

修复（issue 两案取「保存完整动作关联集合」；「每轮限制一个执行类动作」会
改变模型编排能力，不在 bug 修复里做产品收紧）：

- 后端：循环收集 `proposed_action_ids: list[int]`（仅 proposed 模式——auto
  模式动作已内联出终态，无卡片语义），meta 写入**新增复数键
  `proposed_action_ids`**，同时保留单数键 `proposed_action_id`（指向最后一个）
  ——向后兼容：旧前端与历史消息消费方不炸；
- 前端：`AiChatMessageMeta` 增 `proposed_action_ids?: number[] | null`；
  `AssistantPage` 渲染完整集合（`proposed_action_ids ?? 单数键回落`）——
  旧消息（只有单数键）依旧渲染单卡，新消息多卡各一个 `ActionCard`。

与 ADR-0033 的关系：无直接约束（平台自研助手域）。

## Alternatives

- 每轮限制一个执行类动作（多余调用明确拒绝）：能根治多卡片 UI 复杂度，但
  收紧的是模型编排能力而非缺陷本身，属产品决策——且 issue 验收两条路均可；
- meta 存动作 id 数组之外再存快照（tool_name/params）：`ActionCard` 已按
  actionId 自查询（2s 轮询），冗余快照只会制造不一致；
- 只改前端把 meta.proposed_action_id 当数组解释：后端存的是单值，类型上就是
  假的——数据侧必须先修。

## Verification

- `pytest backend/tests/api/test_ai_assistant_endpoints.py`：44 passed，新增
  1 例端到端——假 LLM 客户端一轮两个 `reload_agent_config` 写工具：两个
  proposed 动作都创建、meta `proposed_action_ids` 与两者 id 一致、单数键
  向后兼容仍指向最后一个；
- 前端 `tsc --noEmit` 0 错误、`eslint`（AssistantPage/types）干净；
- ruff 全绿。

## Revisit

- 旧消息（只有单数键）渲染单卡由前端回落兼容；若未来要做「历史消息批量
  迁移 meta」，另立数据脚本单；
- `ActionCard` 多卡并排的视觉密度（本轮 3+ 个动作时）——展示问题交 UI 打磨；
- 「每轮执行类动作上限」的产品收紧（若需要）应走独立决策而非 bug 修复。
