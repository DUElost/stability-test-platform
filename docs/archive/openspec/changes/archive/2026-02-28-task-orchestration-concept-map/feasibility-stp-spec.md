# 可行性研究：stp-spec 架构方案评估

**关联 Change**: task-orchestration-concept-map
**Date**: 2026-02-27
**Status**: Research Complete
**Type**: Feasibility Assessment

---

## 一句话结论

**技术上完全可行，但属于基础性重构，不是增量迭代。**
大约 60-70% 的现有代码需要重写或根本性改造，新增 Redis 一个基础设施依赖。

---

## 逐维度对比

### 1. 实体模型 — 需要全面重构

| stp-spec 实体 | 当前代码实体 | 差异类型 |
|---|---|---|
| `WorkflowDefinition` | 不存在（当前 `Workflow` 混合了定义+运行） | 拆分 |
| `TaskTemplate`（含 pipeline_def，FK→WorkflowDefinition） | `Task`（独立存在，不隶属于 Workflow） | 概念重构 |
| `WorkflowRun` | `TaskRun`（单任务粒度） | 升维（从单任务到多任务集合） |
| `JobInstance` | `TaskRun` | 重命名 + 字段变更 |
| `StepTrace`（string step_id，唯一约束：job_id+step_id+event_type） | `RunStep`（integer PK，延迟创建） | 重设计（解决了 _db_step_id 问题） |
| `host.id: VARCHAR(64)` | `host.id: INTEGER` | 类型破坏性变更 |
| `tool.version` 字段 | 不存在 | 新增 |

**结论**：现有 6 张核心业务表均需重命名+结构变更，迁移脚本复杂度高。

---

### 2. pipeline_def 格式 — 不兼容，需要迁移工具

**当前格式**（已实现）：
```json
{
  "version": 1,
  "phases": [
    { "name": "prepare", "parallel": false,
      "steps": [
        { "name": "check_device", "action": "builtin:check_device",
          "timeout": 30, "on_failure": "stop", "max_retries": 0 }
      ]
    }
  ]
}
```

**stp-spec 格式**（目标）：
```json
{
  "stages": {
    "prepare": [
      { "step_id": "check_device", "action": "builtin:check_device",
        "timeout_seconds": 30, "retry": 0 }
    ],
    "execute": [
      { "step_id": "run_monkey", "action": "tool:42", "version": "v2.1",
        "params": { "duration": 3600 }, "timeout_seconds": 7200 }
    ]
  }
}
```

| 差异点 | 当前 | stp-spec |
|---|---|---|
| 顶层结构 | `phases` 数组 | `stages` 字典（固定 key） |
| 步骤标识 | `name` (string) | `step_id` (string) |
| 超时字段 | `timeout` | `timeout_seconds` |
| 失败策略 | `on_failure: "stop/continue/retry"` | `retry: N`（整数，无 continue/stop 区分） |
| 并行控制 | `phase.parallel: bool` | 不支持（全部串行） |
| Tool 引用方式 | `builtin:run_tool_script` + 手填路径 | `tool:<id>` + `version` |
| 自定义 shell | `shell:<cmd>` 支持 | 不存在（只允许 builtin 和 tool） |

所有现有文件模板（monkey.json, mtbf.json 等）和已存 Task 的 pipeline_def 均需迁移。

---

### 3. Action 类型系统 — 与前次决策冲突

这是最关键的冲突点：

| 维度 | 当前用户决策（2026-02-26） | stp-spec 要求 |
|---|---|---|
| `tool:<id>` | **永久禁用，清理死代码** | **主要 action 类型，必须实现** |
| `builtin:run_tool_script` | 推荐路径 | **明确禁止**（"禁止出现 script_path 字段"） |
| `shell:<cmd>` | 支持 | 不存在 |

stp-spec 的 `tool:<id>` 机制要求：
1. Agent 启动时全量拉取 Tool Catalog（`ToolRegistry`）
2. 执行 step 时通过 tool_id 查找本地路径
3. 版本不匹配时触发热更新或 PENDING_TOOL 状态

这与"永久禁用"决策直接对立。**采用 stp-spec 等于撤销该决策**。

---

### 4. 通信层 — 需新增 Redis 基础设施

| 维度 | 当前实现 | stp-spec 要求 |
|---|---|---|
| Agent → Server 通信 | WebSocket (ws_client.py) | Redis Stream (`stp:status`, `stp:logs`) |
| Server → Agent 控制 | WebSocket 消息 | Redis Stream (`stp:control`) |
| 断线重播 | 无（断线即丢数据） | Agent 本地 SQLite + Reconciler |
| 背压控制 | 无 | Server 监控 lag > 5000 时下发限速指令 |
| 新增依赖 | — | **Redis 7** |

Redis Stream 带来的优势：
- 消息持久化（不依赖 WebSocket 连接状态）
- ACK + PEL 保障消息至少投递一次
- 天然支持多 Agent 并发写入

但也带来：
- 新的运维复杂度（Redis 高可用、AOF 持久化）
- Agent 需要新增本地 SQLite（WAL 模式）
- 完全重写 `ws_client.py` → `mq/producer.py`

---

### 5. Agent 架构 — 需要较大改动

| 组件 | 当前实现 | stp-spec 目标 |
|---|---|---|
| 工具加载 | `importlib` 动态 import，路径写死在 pipeline_def | `ToolRegistry` 按 tool_id 解析 → 本地路径 |
| 工具版本 | 无版本概念 | 严格版本匹配，不匹配时拉取或报 PENDING_TOOL |
| 状态上报 | HTTP fallback + WebSocket | Redis Stream `stp:status` |
| 日志上报 | WebSocket + OSC 633 | Redis Stream `stp:logs` （背压感知） |
| 本地缓存 | 无 | SQLite WAL，保障断线重播 |
| Watchdog | 基于 daemon thread | `asyncio.wait_for` + `adb reboot` |
| 资源配额 | 无 | `asyncio.Semaphore(CPU_QUOTA)` |

---

### 6. 前端 — 路由结构和核心页面需重构

| 维度 | 当前实现 | stp-spec 目标 |
|---|---|---|
| 路由结构 | `/tasks`, `/task-runs` 等 | `/orchestration/workflows`, `/execution/runs` 等 |
| 核心新页面 | 无 WorkflowRun 矩阵看板 | **矩阵看板**（每台设备一个状态方块） |
| 状态管理 | React Query + useState | **Zustand** + React Query |
| PipelineEditor action 选择 | 硬编码 builtin 列表 | 动态加载 Tool Catalog（下拉选 Tool + version） |
| WebSocket 订阅 | 单任务日志流 | 工作流级状态更新 + 单 Job 日志流（两个端点） |
| 错误格式 | 自定义 | 统一 `{ data, error: { code, message } }` 格式 |

---

## 硬约束集合（采用 stp-spec 时不可绕过）

1. **Redis 必须就绪**：无 Redis 则 Agent 无法上报状态和日志
2. **`tool:<id>` 是唯一的工具 action 类型**：不允许 `shell:` 和 `run_tool_script`
3. **所有 Tool 必须有 `version` 字段**：pipeline_def 中引用时必须同时指定 version
4. **Job 状态机必须通过 `JobStateMachine.transition()`**：禁止直接赋值
5. **`host.id` 必须为字符串**：现有整数 ID 迁移时破坏性变更
6. **`step_trace` 唯一约束 `(job_id, step_id, event_type)`**：Reconciliation 的数据库层保障
7. **Agent SQLite WAL 必须在事务内写 StepTrace**：不可简化为内存缓存
8. **`stp:status` 不受背压影响**：背压指令只能限制 `stp:logs`

---

## 可行性风险矩阵

| 风险项 | 可能性 | 影响 | 缓解方案 |
|---|---|---|---|
| 现有 Task/TaskRun 历史数据迁移 | 高 | 高 | 编写迁移脚本，旧数据标记 `legacy` |
| Redis 在 Windows 开发环境运行 | 低 | 中 | Redis Desktop 或 Docker Compose |
| 所有现有 pipeline_def 格式迁移 | 高 | 中 | 自动转换脚本（phases→stages，action 映射） |
| `tool:<id>` 实现后工具版本管理复杂度 | 中 | 中 | 从简单版本策略开始（不校验 version 字段） |
| 前端 Zustand 引入与现有 React Query 共存 | 低 | 低 | Zustand 只管 WebSocket 实时数据，其余保留 |

---

## 改造代价估算（相对工作量）

| 模块 | 改造程度 | 估算工作量 |
|---|---|---|
| 数据库 Schema | 全部重写（6 张表） | 大 |
| 后端 API 路由 | 50% 重写，50% 新增 | 大 |
| `JobStateMachine` | 新增 | 中 |
| `WorkflowAggregator` | 新增 | 中 |
| Agent MQ 层 | 全部替换（WebSocket → Redis Stream） | 大 |
| Agent ToolRegistry | 全部新增 | 中 |
| Agent Pipeline Engine | ~40% 修改（format + action 解析） | 中 |
| Agent 本地 SQLite | 全部新增 | 中 |
| 前端路由+页面 | ~70% 重构（新路由+矩阵看板） | 大 |
| 前端 PipelineEditor | ~50% 修改（动态 Tool 加载） | 中 |
| 基础设施（Redis） | 新增 | 小 |

**总体估计**：相当于重新开发 60-70% 的系统功能。

---

## 增量迁移路径建议

如果决定采用 stp-spec，建议分阶段推进（最小破坏性）：

```
Phase 1: 基础设施 + 数据模型（不动前端）
  - 添加 Redis + Docker Compose 配置
  - 数据库迁移：新增 WorkflowDefinition, TaskTemplate, WorkflowRun,
    JobInstance, StepTrace 表（保留旧表，逐步迁移）
  - 实现 JobStateMachine 和 WorkflowAggregator

Phase 2: Agent MQ 层
  - 实现 Redis Stream producer（stp:status, stp:logs）
  - 实现本地 SQLite WAL 缓存
  - 实现 ToolRegistry（tool_id → 本地路径）
  - 保留 WebSocket 作为 fallback（双写过渡期）

Phase 3: 后端 API 重构
  - 实现新的 /api/v1/workflows/ 和 /api/v1/workflow-runs/ 端点
  - 实现 dispatcher.py + reconciler.py + heartbeat_monitor.py
  - pipeline_def 格式自动转换中间件（兼容旧格式）

Phase 4: 前端重构
  - 新增 /execution/runs 矩阵看板
  - 重构 PipelineEditor 的 action 选择（Tool Catalog 下拉）
  - 迁移路由结构
```

---

## 用户决策记录（2026-02-27）

| 问题 | 决策 | 影响 |
|---|---|---|
| `tool:<id>` 与前次冲突 | **采用 stp-spec，重新启用 tool:<id>** | 撤销 2026-02-26"永久禁用"决策；实现 ToolRegistry 版本管理 |
| 是否引入 Redis | **接受 Redis，采用 MQ 架构** | Redis 7 成为必要基础设施依赖 |
| 迁移策略 | **完全重写** | 不考虑历史兼容性，历史 Task/TaskRun 数据标记 legacy |

这三项决策联合生效，意味着 **stp-spec 架构方案将完全取代现有架构**。

---

## 待用户确认的关键决策

### 决策 Q1（高优先级，直接影响所有后续规划）

stp-spec 的 `tool:<id>` action 类型与前次研究中"永久禁用，清理死代码"的决策直接冲突。

- **采用 stp-spec** = 撤销"永久禁用"决策，实现完整 ToolRegistry
- **不采用 stp-spec** = 维持"永久禁用"决策，继续用 `builtin:run_tool_script`

### 决策 Q2（基础设施选择）

是否接受将 Redis 作为必要依赖引入？
- 若接受：通信可靠性大幅提升，但运维复杂度增加
- 若不接受：Agent 通信继续走 WebSocket，stp-spec 的断线重播机制无法实现

### 决策 Q3（迁移策略）

- **完全迁移**：直接按 stp-spec 重写，放弃历史兼容性
- **渐进迁移**：保持旧格式兼容，新功能按 stp-spec 实现
- **不采用**：维持现有架构，仅参考 stp-spec 的部分设计思想
