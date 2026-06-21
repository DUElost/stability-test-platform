# Proposal: Task Pipeline Engine + Real-time Log Streaming

## Context

### User Need
将稳定性测试平台的任务执行模块从固定生命周期（BaseTestCase 硬编码阶段）改造为 **GitHub Actions 风格的可编排 Pipeline**，同时实现 **实时日志流**（WebSocket + xterm.js）替代当前的 HTTP 心跳批量日志回传。

### Current State
- **Agent 执行**：`BaseTestCase.run()` 强制固定生命周期 `PRECHECK->PREPARE->RUN->RISK_SCAN->EXPORT->TEARDOWN`，阶段集合与进度映射硬编码于 `test_stages.py`
- **日志传输**：Agent 通过 HTTP POST 心跳每 10s 批量回传日志（200 行缓冲），后端再广播到前端 WebSocket
- **前端日志**：`LogViewer.tsx` 接收 LOG/PROGRESS 消息，1000 行缓冲，无虚拟化渲染，无步骤级折叠
- **现有 Workflow**：`Workflow/WorkflowStep` 模型用于跨 Task 编排，非 TaskRun 内子步骤编排
- **工具系统**：7 个 BaseTestCase 子类（AIMonkey, Monkey, MTBF, DDR, GPU, Standby 等），通过 `tool_discovery.py` AST 扫描注册

### Discovered Constraints

#### Hard Constraints (不可违反)
- HC-1: Agent 运行于 Linux，systemd 管理，当前依赖最小集（requests/python-dotenv），新增 WebSocket 需引入 `websockets` 库
- HC-2: 数据库为 PostgreSQL，现有 `TaskRun` 仅有 run 级进度/摘要字段，无子步骤实体
- HC-3: 设备锁基于 `lock_run_id + lock_expires_at` 的租约模型，续期线程独立于主循环
- HC-4: 后端单进程架构（FastAPI + 内置调度线程），WebSocket 连接管理为内存型
- HC-5: 前端 React 18 + Tailwind + shadcn/ui + React Query，所有 Radix 原语需保持一致
- HC-6: Vite 开发代理 `/ws` -> `ws://localhost:8000`，生产 Nginx 配置 `/ws/` upgrade
- HC-7: `tool_snapshot` 快照机制（任务创建时固化工具配置）需保留用于可追溯性
- HC-8: Nginx 已配置 WebSocket upgrade for `/ws/` path

#### Soft Constraints (惯例/偏好)
- SC-1: 前端习惯 React Query 管理服务端状态，useMutation + invalidateQueries 模式
- SC-2: Agent 配置双模式（dev: 项目根 / deploy: /opt/），`config.py` 路径解析需兼容
- SC-3: 现有测试工具通过 `setup/execute/scan_risks/collect/teardown` 约定复用框架能力
- SC-4: ADR 已定 "REST + WebSocket 混合" 通信策略
- SC-5: UI 组件使用 CVA (class-variance-authority) 变体模式

#### Dependencies (跨模块依赖)
- D-1: Pipeline 定义格式（JSON Schema）影响：后端存储、Agent 解析、前端编辑器、模板系统
- D-2: WebSocket 消息协议变更影响：Agent 发送端、后端路由/广播、前端 useWebSocket hook
- D-3: 数据库 Schema 变更影响：Alembic 迁移、API Schema、前端 TypeScript 类型

#### Risks (需要缓解的风险)
- R-1: Agent 主循环同步执行，长任务期间心跳停滞可能触发 host offline 误判
- R-2: 前端 xterm.js 为 Canvas 渲染，无法直接嵌入 React 组件作为日志行内 UI
- R-3: 全面迁移 BaseTestCase 到 step 原子化需改动所有 7 个现有工具类
- R-4: Agent WebSocket 长连接引入断线重连、认证、代理穿透等复杂性
- R-5: 多 step 并行执行需要 Agent 从同步单线程改为异步/多线程
- R-6: 新旧 Agent 协议并存期间的兼容性故障

---

## Requirements

### REQ-1: Pipeline Definition Schema
**用户可通过 JSON/YAML 定义 TaskRun 的执行管道，包含多个有序阶段（Phase），每个阶段包含可并行的步骤（Step）。**

Scenario:
- Given: 用户创建任务时定义 pipeline 配置
- When: Pipeline 包含 3 个 Phase（prepare/execute/post_process），每个 Phase 含 1-N 个 Step
- Then: 配置被存储为 Task 的 `pipeline_def` JSON 字段，Agent 按定义执行

Constraints:
- Phase 之间严格串行执行
- 同一 Phase 内的 Step 可配置为并行（`parallel: true`）或串行（默认）
- 每个 Step 包含: `name`, `type`, `params`, `timeout`, `on_failure`(stop/continue/retry)
- Step `type` 映射到可执行单元（内置 action 或 tool_id 引用）
- Pipeline 定义需有 JSON Schema 用于前后端校验

### REQ-2: Pipeline Execution Engine (Agent-side)
**Agent 解析 Pipeline 定义并按拓扑顺序执行各 Step，替代现有 BaseTestCase 固定生命周期。**

Scenario:
- Given: Agent 从后端拉取到含 `pipeline_def` 的 TaskRun
- When: Agent 启动执行
- Then: 按 Phase 顺序逐阶段执行，阶段内并行 Step 使用线程池并发，每个 Step 独立上报状态和日志

Constraints:
- 现有 7 个 BaseTestCase 子类需全面迁移为 step action（一个 Tool = 一个或多个 step action）
- 提供内置 action 集合: `check_device`, `clean_env`, `push_resources`, `start_process`, `monitor_process`, `stop_process`, `adb_pull`, `run_tool_script` 等
- Step 粒度的超时控制与失败策略
- Agent 主循环需从同步改为支持并发 step 执行（threading 或 asyncio）
- Host 心跳需独立于任务执行（独立线程），避免长任务阻塞心跳

### REQ-3: Step Status Tracking (Database)
**数据库记录每个 Step 的执行状态、时间、日志摘要，支持前端展示与历史回溯。**

Scenario:
- Given: Pipeline 执行过程中
- When: 每个 Step 开始/完成/失败
- Then: 对应 `RunStep` 记录更新状态、时间戳、错误信息；TaskRun 聚合状态同步更新

Constraints:
- 新增 `run_steps` 表: `id, run_id, phase, order, name, type, params, status, started_at, finished_at, exit_code, error_message, log_line_count`
- RunStep 状态: PENDING, RUNNING, COMPLETED, FAILED, SKIPPED, CANCELED
- TaskRun 状态由所有 Step 状态聚合推导（任一 FAILED 且 on_failure=stop -> run FAILED）
- Alembic 迁移脚本

### REQ-4: Agent-to-Backend WebSocket Log Streaming
**Agent 通过 WebSocket 长连接实时推送每行日志到后端，替代 HTTP 心跳批量回传。**

Scenario:
- Given: Agent 开始执行 TaskRun
- When: 任意 Step 产生日志输出
- Then: 日志行通过 WebSocket 实时发送到后端（毫秒级延迟），后端转发给订阅该 run 的前端客户端

Constraints:
- Agent 新增 `websockets` 依赖
- 消息格式: `{run_id, step_id, seq, level, timestamp, message}`
- 支持断线自动重连（指数退避）+ 消息序号确保不丢不重
- 后端新增 Agent WebSocket 接入端点 `WS /ws/agent/{host_id}`
- 后端作为中继：Agent WS -> 后端 -> 前端 WS（`/ws/logs/{run_id}`）
- 认证: 复用 `AGENT_SECRET` header 作为 WS 连接认证
- 保持 HTTP 心跳作为 fallback（连接失败时降级）

### REQ-5: Frontend Pipeline Visualization
**TaskDetails 页面左侧展示 GitHub Actions 风格的 Pipeline 步骤列表，支持实时状态更新和步骤切换。**

Scenario:
- Given: 用户打开某 TaskRun 的详情页
- When: Pipeline 正在执行
- Then: 左侧显示分阶段的步骤树，每个步骤显示状态图标/颜色、执行时长；当前运行步骤高亮；点击步骤切换右侧日志面板

Constraints:
- 步骤状态通过 WebSocket `STEP_UPDATE` 消息实时更新
- 阶段可折叠/展开（默认展开当前阶段）
- 步骤状态图标: pending(灰), running(蓝+旋转), success(绿勾), failed(红叉), skipped(灰划线)
- 保持 shadcn/ui 设计语言一致性

### REQ-6: xterm.js Terminal Log Viewer
**替换现有 LogViewer 为基于 xterm.js 的终端组件，支持高性能日志渲染、搜索、高亮和分步骤折叠。**

Scenario:
- Given: 用户点击某个 Step 查看日志
- When: 该 Step 正在运行或已完成
- Then: 右侧面板显示 xterm.js 终端，实时流入日志，支持 ANSI 颜色、搜索（regex）、关键词高亮（FATAL/CRASH/ANR）

Constraints:
- 动态导入 xterm.js（`React.lazy` + code splitting）避免首屏体积膨胀
- 集成 `xterm-addon-search`、`xterm-addon-fit`、`xterm-addon-web-links`
- 每个 Step 的日志流独立，切换 Step 时切换 xterm buffer 或实例
- 未激活的 Step 的 xterm 实例需销毁/复用，避免内存线性增长
- 支持折叠日志组（通过 ANSI escape 或自定义分隔标记）
- 保留现有功能: 自动滚动、下载日志、级别过滤

### REQ-7: Pipeline Template & Editor
**前端提供可视化 Pipeline 编辑器，用户可拖拽排序步骤、配置参数、保存为模板复用。**

Scenario:
- Given: 用户创建新任务
- When: 用户进入 Pipeline 编辑器
- Then: 可添加阶段和步骤，为每个步骤选择 action 类型、配置参数、设置超时和失败策略；可保存为 TaskTemplate

Constraints:
- 步骤拖拽重排序（使用 `@dnd-kit`）
- 步骤参数表单复用现有 `DynamicToolForm`（基于 JSON Schema 渲染）
- 内置 action 提供预设参数模板
- Pipeline 配置 JSON 实时预览
- 现有 TaskTemplate 模型扩展以存储 pipeline_def

---

## Success Criteria

### SC-1: Pipeline Execution
- [ ] Agent 能解析并执行包含 3 个 Phase、每个 Phase 2-3 个 Step 的 Pipeline
- [ ] Phase 内并行 Step 实际并发执行（可通过日志时间戳验证）
- [ ] Step 失败时 `on_failure` 策略正确生效（stop/continue/retry）
- [ ] 现有 7 个 BaseTestCase 工具全部迁移为 step action 且功能回归通过

### SC-2: Real-time Log Streaming
- [ ] Agent WebSocket 连接建立后，单行日志从产生到前端展示延迟 < 500ms
- [ ] WebSocket 断线后 5s 内自动重连，重连期间降级为 HTTP 心跳
- [ ] 前端 xterm.js 稳定渲染 10,000+ 行日志无卡顿（FPS > 30）

### SC-3: UI/UX
- [ ] TaskDetails 页面左侧 Pipeline 步骤树实时反映各 Step 状态
- [ ] 点击 Step 在 < 200ms 内切换右侧日志面板
- [ ] 支持 regex 搜索并高亮 FATAL/CRASH/ANR 关键词
- [ ] Pipeline 编辑器支持拖拽排序、参数配置、模板保存

### SC-4: Backward Compatibility
- [ ] 现有任务（无 pipeline_def）仍可正常创建和执行（兼容旧 Agent）
- [ ] 数据库迁移脚本可从当前 schema 安全升级
- [ ] 部署脚本（install_agent.sh）自动安装新依赖（websockets）

---

## Implementation Dependencies & Sequencing

```
Phase 1: Foundation (数据层 + 协议层)
  ├── DB Schema: run_steps 表 + Alembic 迁移
  ├── Pipeline JSON Schema 定义
  ├── Backend API: RunStep CRUD + WebSocket agent endpoint
  └── Agent WebSocket client 基础连接

Phase 2: Engine (执行引擎)
  ├── Agent Pipeline Engine (phase serial + step parallel)
  ├── 内置 action 库 (check_device, clean_env, push_resources...)
  ├── 现有 BaseTestCase 工具拆解为 step actions
  └── Host 心跳独立线程

Phase 3: Frontend (UI 层)
  ├── xterm.js 集成 + 动态导入
  ├── Pipeline 步骤树组件
  ├── TaskDetails 页面重构（左侧步骤树 + 右侧终端）
  └── Pipeline 编辑器 + 模板保存

Phase 4: Polish (打磨)
  ├── 断线重连 + HTTP fallback
  ├── 日志折叠组
  ├── 性能优化（xterm 实例复用、Web Worker 日志解析）
  └── 全量回归测试
```

---

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| R-1: Agent 同步主循环阻塞心跳 | Host 误判 offline | 心跳拆为独立守护线程，不受任务执行阻塞 |
| R-2: xterm.js Canvas 渲染限制 | 无法嵌入 React 行内组件 | 使用 ANSI escape 序列实现颜色/高亮，不依赖 DOM |
| R-3: 7 个工具全面迁移工作量 | 回归风险 | 分批迁移 + 每工具回归测试套件 |
| R-4: WebSocket 断线丢日志 | 日志不完整 | 消息序号 + HTTP fallback + 服务端缓冲重放 |
| R-5: 多 step 并行 Agent 复杂度 | 资源竞争/死锁 | 统一线程池 + step 级超时 + 取消信号 |
| R-6: 新旧协议并存 | 兼容性故障 | 后端同时接受 HTTP 心跳和 WS 日志，按 Agent 版本路由 |
