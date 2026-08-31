# ADR-0031 附录 A：阶段三核心业务写操作（Proposed）

- 状态：**Proposed**（依赖 D8 已落地；实现 PR 未开）
- 优先级：**P1**
- 日期：2026-08-31
- 父 ADR：[ADR-0031](./ADR-0031-platform-ai-assistant.md)（Accepted v1.6+）
- 前置：[D8 权限对齐 Agent Note](../notes/architecture/2026-08-31-ai-assistant-permission-parity-d8.md)

## 0. 目标

在 **不破坏「助手权限 ⊆ 账号 API 权限」（D8）** 的前提下，把平台使用者/管理员日常依赖的 **Plan 执行链路写操作** 纳入助手工具面，解决 v1「能查不能派」的体验断层（如 GPU 专项 PlanRun 发起）。

**非目标**：Plan/脚本/用户 CRUD、hot-update、任意 shell、SDLC 自动化——仍属 UI/API 或 T3/SDLC 域。

## 1. 风险子级 T2b（业务写）

在 D1 的 T0–T3 框架内新增 **T2b 业务写**（命名可并入 T2，文档中单列便于评审）：

| 子级 | 含义 | 默认自治 | 白名单 |
|------|------|----------|--------|
| **T2b** | 改变 PlanRun/Job 运行态（派发、中止、人工干预、归档触发） | **需审批**（操作卡 + 参数预览） | 可按 `plan_id` + `max_devices` 配置自动派发（仅发起人本身有 API 权限时生效，D8） |
| T2a（既有） | reload、扫脚本、测通知 | 需审批 / 低危 auto | `auto_approve_tools`（仅 whitelistable） |

T2b 自动白名单落库键（建议新增 JSONB 列或扩展现有 config）：

```json
{
  "plan_id": 42,
  "max_devices": 3,
  "tools": ["dispatch_plan_run"]
}
```

校验：发起人须对该 `plan_id` 具备与 `POST /plans/{id}/run` 同等权限；设备数 ≤ `max_devices`；Plan `is_active`。

## 2. 工具清单（拟新增）

### 2.1 P0 — 与「发起 / 止血」直接相关

| 工具名 | tier | kind | 镜像 API | `admin_only` | 说明 |
|--------|------|------|----------|--------------|------|
| `list_plans` | T0 | query | `GET /api/v1/plans` | false | 派发前选 Plan；参数：`project_id?`, `specialty?`, `limit≤20` |
| `get_plan_detail` | T0 | query | `GET /api/v1/plans/{id}` | false | 含 PlanStep 摘要、专项、脚本引用 |
| `preview_plan_dispatch` | T0 | query | `POST /api/v1/plans/{id}/run/preview` | false | 只读预检：设备可用性、准入冲突（**不创建** PlanRun） |
| `dispatch_plan_run` | **T2b** | service | `POST /api/v1/plans/{id}/run` | false | 参数：`plan_id`, `device_ids[]`, `note?`, `wifi_pool_id?`；内部调用 `prepare_plan_run`（与路由同一函数） |
| `abort_plan_run` | **T2b** | service | `POST /api/v1/plan-runs/{id}/abort` | false | 参数：`run_id`, `reason?`；须校验 PlanRun 非终态 |
| `get_plan_run_jobs` | T0 | query | `GET /api/v1/plan-runs/{id}/jobs` | false | Job 列表与状态（派发后跟踪） |

### 2.2 P1 — 运维跟进

| 工具名 | tier | kind | 镜像 API | `admin_only` | 说明 |
|--------|------|------|----------|--------------|------|
| `get_plan_run_watcher_summary` | T0 | query | `GET .../watcher-summary` | false | 运行中崩溃信号 / link_stats |
| `get_plan_run_log_events` | T0 | query | `GET .../log-events` | false | 终态 DLE 视图 |
| `get_plan_run_timeline` | T0 | query | `GET .../timeline` | false | 时间线 |
| `retry_plan_run_dispatch` | **T2b** | service | `POST .../retry-dispatch` | false | precheck 失败后重入队 |
| `manual_retry_job` | **T2b** | service | `POST .../jobs/{id}/manual-retry` | false | 清除 backoff |
| `manual_exit_job` | **T2b** | service | `POST .../jobs/{id}/manual-exit` | false | 请求 Agent 退出 patrol |
| `trigger_plan_run_archive` | **T2b** | service | `POST .../archive` | false | 归档 + scan_now 下发（异步） |

### 2.3 P2 — 延后

| 工具名 | 理由 |
|--------|------|
| `export_plan_run_report` | 只读导出，T0 即可；优先级低于执行链路 |
| Plan/脚本/项目 CRUD | 配置变更，保留 UI + 变更评审 |
| dedup merge / jira submit | 控制面长任务，另评 RunConsole 封装 |

### 2.4 仍不提供（T3 / 域外）

host hot-update、install、生产库任意写、设备租约 SQL 逃生、force_rebaseline、用户管理、任意 shell。

## 3. 实现约束（D8 + D4 延伸）

### 3.1 授权

1. **唯一入口**：`authz.user_may_invoke_tool(user, spec)`；未来 RBAC 在此扩展 `plan_id` / `project_id` scope。
2. **服务层带 User**：写工具必须调用与 REST 路由**相同**的 service 函数，签名含 `triggered_by` / `user_id`，禁止 orchestrator 内复制派发逻辑。
3. **执行前复检发起人**：`execute_action` 已有；T2b 自动白名单同样只认发起人权限。
4. **审批不升级权限**：admin 批准普通用户的 `dispatch_plan_run` 仅在发起人 **本就可** `POST /plans/{id}/run` 时执行；否则 `failed` + 审计。

### 3.2 参数校验（`dispatch_plan_run` 示例）

| 参数 | 校验 |
|------|------|
| `plan_id` | 存在、`Plan.is_active` |
| `device_ids` | 非空、≤ 配置上限（默认 20，可 env）、整数、设备存在 |
| 设备状态 | ONLINE、无 ACTIVE lease（或 preview 同源规则） |
| `wifi_pool_id` | 若 Plan 含 `connect_wifi` 步骤则必填；须 `_require_active_wifi_pool` + `_require_wifi_pool_matches_plan` |
| host 一致性 | 可选：单次派发限制同一 `host_id`（产品可配置） |

校验失败 → `ToolValidationError` 回填模型（占轮次），不静默改参。

### 3.3 操作卡预览（前端）

T2b 提案须展示：**Plan 名称、专项、设备 SN 列表、主机、wifi_pool、发起人**——与 UI「执行 Plan」确认框信息等价，供 admin 审批。

### 3.4 审计

`record_audit` 动作建议：`ai_assistant_dispatch_plan_run` 等；details 含 `plan_id`、`device_ids`、`plan_run_id`（创建后）、`requested_by`、`decided_by`。

## 4. 验收矩阵（实现 PR 必过）

| # | 场景 | 期望 |
|---|------|------|
| 1 | 登录用户助手 `dispatch_plan_run` 合法参数 | 与 API 同用户 `POST /plans/{id}/run` 均成功；PlanRun id 一致语义 |
| 2 | 普通用户助手派发 admin 专属 Plan（若未来 RBAC 限制） | 助手与 API 均 403/失败 |
| 3 | 普通用户 + T2b 自动白名单含他人 `plan_id` | 不自动执行；或 `failed` 权限不足 |
| 4 | admin 批准普通用户提案，但发起人无权 | `execute_action` → `failed`，不创建 PlanRun |
| 5 | `preview` 工具 | 不写入 DB；与 preview API 结果一致 |
| 6 | `abort` 终态 PlanRun | 与 API 同 409 |
| 7 | 幻觉 `device_id` / 跨 host 混选 | 校验拒绝，不占槽位 |
| 8 | 越权回归 | 现有 D8 用例仍绿 |

## 5. 交付切分（建议 PR 顺序）

| PR | 内容 | 依赖 |
|----|------|------|
| **PR-A** | T0 深读：`list_plans`, `get_plan_detail`, `preview_plan_dispatch`, `get_plan_run_jobs`, watcher/log-events | ✅ 本分支 |
| **PR-B** | T2b：`dispatch_plan_run` + 操作卡预览 + 审计 + 验收 1–4 | ✅ 本分支 |
| **PR-C** | T2b：`abort_plan_run`, `manual_*`, `retry_dispatch`, `trigger_archive` | PR-B |
| **PR-D** | config：`t2b_auto_dispatch_allowlist` + 设置页 | PR-B |

## 6. 与父 ADR 的关系

- 不修改 T3 硬排除与 SDLC 划界。
- D6 表格增补：T2b 提案规则同 T2；镜像 API 为 `get_current_active_user` 的工具对登录用户可见。
- 触发父 ADR 复议 #3（hot-update 代执行）**不**被本附录覆盖。

## 7. 开放问题（评审时裁定）

1. **中止权限**：是否限制为「仅发起人 or admin 可 abort」？（API 当前登录即可——助手先对齐 API，收紧另开 ADR。）
2. **单次设备上限**：全局 20 是否足够；GPU 三机场景默认 3 是否写入白名单模板。
3. **wifi_pool_id**：是否允许助手从自然语言推断 pool，还是必须显式 ID（建议 **必须 ID**，助手先 T0 查 pools 再派发——P2 可加 `list_wifi_pools`）。
