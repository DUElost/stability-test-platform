# AI 助手 PR-B：dispatch_plan_run（阶段三附录）

Status: implemented
Class: feature

## 决定了什么

- 新增 T2b 工具 `dispatch_plan_run`：调用 `prepare_plan_run`（与 `POST /plans/{id}/run` 同源），默认 **proposed** 须 admin 审批。
- `dispatch.py` 承载参数归一化、预览文案、执行 + `ai_assistant_dispatch_plan_run` 审计。
- 操作卡 API 增 `preview_text`（Plan 名、专项、设备 serial/host）；前端 `ActionCard` 展示。
- D8：`execute_action` 仍以发起人权限复检；派发镜像登录用户 API（非 admin-only）。

## 放弃的备选

- **T2b 自动白名单（plan_id）**：留 PR-D；本 PR 仅审批路径。
- **HTTP 自调 dispatch API**：拒绝；直调 `prepare_plan_run` 避免二次鉴权与序列化开销。

## 如何验证

- `pytest backend/tests/services/test_ai_dispatch.py -q`
- `pytest backend/tests/api/test_ai_assistant_endpoints.py -q`
- 助手对话：list_plans → preview_plan_dispatch → dispatch_plan_run → admin 批准 → 查 PlanRun

## 何时重议

- PR-C（abort/manual_*）合入后串联「派发 → 盯执行 → 止血」全流程。
