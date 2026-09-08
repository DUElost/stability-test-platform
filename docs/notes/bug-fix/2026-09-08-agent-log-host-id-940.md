# R03-F07 落地：/agent/logs host_id 契约统一为字符串（#940）

Status: implemented
Class: bug-fix

## Decision

`Host.id` 是 `String(64)` 主键（字符串主机标识），但 `AgentLogQuery` /
`AgentLogOut` 的 `host_id` 声明为 int：非数字主机 ID（如 `host-a1`）在
请求校验阶段就被 pydantic 422 拒绝，永远无法进入查询。数字形态的 id 虽
能碰巧通过 int 解析，输出回显也已丢失字符串形态。

修复：`backend/api/schemas/agent.py` 两处 `host_id: int` → `str`；route
（`db.get(Host, query.host_id)` 与 `AgentLogOut(host_id=query.host_id)`
回显）天然兼容，零改动；前端类型入口
`frontend/src/utils/api/types.ts` 的 `AgentLogOut.host_id` 与
`utils/api/logs.ts` 的 `queryAgent` 参数同步为 string（后端 schema ↔
前端类型同步硬不变量）。

## Alternatives

- **仅后端收 str、前端保留 number**——放弃：违反「前端 API 类型与后端
  schema 同步」硬不变量；且字符串 id 本就来自 `Host.id`；
- **route 内做 int→str 兼容转换**——放弃：掩盖 schema 契约错误，且无法
  修复「校验期即拒绝」的阶段性问题；
- **弃用该端点（DEPLOY.md 描述为事后取证工具）**——放弃：仍有运维取证
  用途；契约修正成本一行。

## Verification

- **反例实证**：回退 schema 保留测试 → 2/3 用例失败（非数字 422、
  roundtrip 422）；修复版全绿；
- 新增用例（`backend/tests/api/test_agent_log_query.py` 3 例）：非数字
  host_id → 404 而非 422；数字形态字符串不存在 → 404；已存在主机字符串
  id → 200 且 host_id 原样回显（error 分支无凭据提示）；
- `test_ssh_security.py`（query_agent_logs 单元路径）回归 **11 passed**；
- `check:quick`（含 tsc）与 PR 门禁：见 PR 描述。

## Revisit

- `queryAgent` 前端目前无 UI 调用点（遗留工具函数，DEPLOY.md 取证入口）——
  类型修正后未来消费方直接以 `Host.id` 传入即可；
- Agent 日志查询仅 SSH 路径走此端点；SocketIO 实时控制台路径
  （log_writer/GET /logs/query）不涉 host_id schema，未受影响。
