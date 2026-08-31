# AI 助手权限对齐（D8）

Status: implemented
Class: architecture

## 决定了什么

**助手有效权限必须 ≤ 发起人账号在 REST API 上的权限**（ADR-0031 D8）。具体落地：

1. `backend/services/ai_assistant/authz.py`：`user_may_invoke_tool` / `assert_user_may_invoke_tool` 为唯一裁决入口。
2. `scan_script_catalog`、`test_notification_channel` 标 `admin_only=True`，镜像 `POST /scripts/scan` 与 `POST /notifications/channels/{id}/test` 的 `require_admin`。
3. `_decide_execution_mode` 在 T1 自动与 `auto_approve` 前复检发起人；无权限则不得 `auto`。
4. `execute_action` 对 `requested_by_user_id` 复检——即使 `status=approved`，发起人无权仍 `failed`（堵住 admin 批准后放大普通用户权限的缝，以及 `auto_approve` 越权）。

## 放弃的备选

- **仅靠 admin 审批兜底**：拒绝。普通用户可提案 + `auto_approve` 可在无审批下执行 admin-only API 等价操作（`test_notification_channel` 实测缝）。
- **审批即授权升级**：拒绝。审批只表达「高风险操作可继续」，不扩大发起人 RBAC。
- **每个工具直调 HTTP 自调 API**：v1 仍直调服务层，但执行前走 `authz`；阶段三扩权时优先收敛到与路由共用的服务函数并带 `User` 上下文。

## 如何验证

- `backend/tests/services/test_ai_authz.py`（无 PG）
- `backend/tests/services/test_ai_tools.py::TestRoleFiltering`（admin-only T2 不可见）
- `backend/tests/api/test_ai_assistant_endpoints.py`：`test_execute_action_denies_admin_only_for_non_admin_requester`；`test_t2_proposal_stops_turn` 改用 `reload_agent_config`（登录用户可提案的 T2）

## 何时重议

- 阶段三接入 PlanRun 发起等核心业务写操作：**必须先**为每个新工具声明权限映射 + 越权对拍测试，再合入。
- 引入项目级 RBAC 时：扩展 `authz.py`，T0 查询与写操作共用 scope filter，禁止助手直读 DB 绕过列表 API 过滤。
