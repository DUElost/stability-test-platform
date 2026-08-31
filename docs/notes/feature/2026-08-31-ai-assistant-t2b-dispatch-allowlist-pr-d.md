# AI 助手 PR-D：T2b 自动派发白名单

**日期**：2026-08-31  
**关联**：ADR-0031 附录 phase-3、`dispatch_plan_run`（PR-B）

## 决定了什么

- `ai_assistant_config` 新增 JSONB 列 `t2b_auto_dispatch_allowlist`：`[{plan_id, max_devices, tools}]`。
- 保存时 `sanitize_t2b_auto_dispatch_allowlist` 丢弃无效/未激活 Plan、去重 plan_id。
- `_decide_execution_mode` 在 `dispatch_plan_run` 命中白名单且设备数 ≤ max_devices 时返回 `auto`；仍先过 D8 `user_may_invoke_tool`。
- 设置页「自治边界」卡新增 T2b 条目编辑（plan_id + max_devices）。

## 放弃的备选

- 复用 `auto_approve_tools` 存 plan scope——语义混淆，T2a/T2b 分列。
- 设置页 Plan 下拉（需额外 T0 API）——首版用 plan_id 数字输入。

## 如何验证

```bash
JWT_SECRET_KEY=test-secret python -m pytest \
  backend/tests/services/test_t2b_allowlist.py \
  backend/tests/api/test_ai_assistant_endpoints.py::TestConfigEndpoints::test_t2b_allowlist_sanitized_on_save -q

cd frontend && npm run test -- src/pages/settings/AiAssistantSettingsPage.test.tsx
```

## 何时重议

- RBAC 限制 `POST /plans/{id}/run` 时，白名单须与 `authz` plan scope 联动。
- 多工具 T2b 白名单（除 dispatch 外）再扩展 `T2B_AUTO_DISPATCH_TOOLS`。
