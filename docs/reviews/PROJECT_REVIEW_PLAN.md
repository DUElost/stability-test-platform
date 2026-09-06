# 全面只读审查总纲

> **最后更新**：2026-09-06
>
> **定位**：全面审查指引（Living 导航与覆盖清单），不是架构规范、缺陷报告、验收结论或某次审查结果。
>
> **正式进度**：见第 5 节「当前登记」；总纲建立不计作任何区域的审查完成。

## 1. 使用范围与安全边界

本总纲供后续逐区审查复用。按业务链路和风险边界划分区域，而不是把目录分配为
所有权；每区都需要追踪相关 API、服务、模型、Agent、前端和测试。下列路径是
**导航入口，不是穷尽清单**，开始审查时须重新核对实际代码与 scoped 指引。

- 权威来源由 [DOC-MAP](../DOC-MAP.md) 定义。代码与测试优先，历史审查只作为线索；
  ADR 的 Proposed / Accepted 状态与实现状态分别核对，规划未落地不自动等于缺陷。
- 本文不改变 [仓库工作流](../development/repository-workflow.md)，不引入执行登记、
  目录所有权或新的并行规则，也不授权自动启动多 Agent、修改代码或实施修复。
- 默认只读取任务所需源码、测试和文档，不读取实际凭据、token、私钥、连接串、
  主机清单或生产数据；不调用业务接口、操控真实设备、重启服务或执行运维脚本。
- 静态审查不等于运行验证。测试、构建、浏览器联调及生产只读诊断如确有必要，
  先明确验证范围与授权；测试只能使用隔离环境，禁止复用生产配置或业务数据库。
  具体约束见 [测试指南](../development/testing.md) 和
  [生产诊断边界](../operations/production-diagnostics.md)。
- 报告写入须在授权范围内；缺陷修复、迁移、脚本发布、PR 等是后续独立工作。
  已发布脚本和已有版本的默认参数不得因审查直接修改。

## 2. 区域与推荐顺序

编号 R01–R15 保持稳定，不随审查顺序调整而重新编号。同一区域可以分多次会话，
但应累计记录覆盖范围；边界变化需更新总纲并保留旧报告的版本基线。

| 阶段 | 区域 | 衔接目标 |
|---|---|---|
| 基础边界 | R01–R03 | 明确系统契约、安全边界和持久化事实源 |
| 执行主链 | R04–R08 | 从项目与设备、执行定义，追踪到派发和 Agent 实际执行 |
| 日志结果链 | R09–R10 | 从异常发现追踪到可靠存储、归并和结果消费 |
| 通信与交互 | R11–R13 | 核对消息交付、前端状态和 AI 工具调用边界 |
| 运维与质量 | R14–R15 | 核对部署恢复能力、测试覆盖、门禁和文档治理 |
| 跨区收口 | 两条端到端链路 | 对齐区域之间的契约，消除覆盖空白与重复结论 |

### R01 总体架构与硬契约

- **范围与边界**：控制面 / Agent 职责、ASGI 组合入口、路由与生命周期；
  PostgreSQL / Redis / 本地 SQLite 的角色，以及跨模块硬不变量。
  具体表约束归 R03，执行状态转换归 R06，不在此重新定义协议。
- **代码入口**：[应用入口](../../backend/main.py)、[数据库入口](../../backend/core/database.py)、
  [实时服务](../../backend/realtime/socketio_server.py)。
- **测试入口**：[主链集成测试](../../backend/tests/integration/test_main_chain_happy_path.py)、
  [控制面测试目录](../../backend/tests/)。
- **权威文档**：[共享契约](../../AGENTS.md)、[系统总览](../design/00-system-overview.md)、
  [后端设计](../design/02-backend.md)。

### R02 认证、授权与安全边界

- **范围与边界**：Cookie、CSRF、用户角色、Agent 身份、REST / Socket.IO 鉴权；
  文件访问、SSH、命令入口和审计。AI 特有的工具审批与隔离归 R13，部署落点归 R14。
- **代码入口**：[安全模块](../../backend/core/security.py)、[CSRF](../../backend/core/csrf.py)、
  [认证路由](../../backend/api/routes/auth.py)、[SSH 安全](../../backend/core/ssh_security.py)、
  [审计](../../backend/core/audit.py)。
- **测试入口**：[会话测试](../../backend/tests/api/test_auth_cookie_session.py)、
  [CSRF 测试](../../backend/tests/test_csrf_origin_middleware.py)、
  [Dashboard 鉴权测试](../../backend/tests/realtime/test_dashboard_auth.py)。
- **权威文档**：[浏览器会话安全 ADR](../adr/ADR-0024-browser-session-security-hardening.md)、
  [后端设计](../design/02-backend.md)。

### R03 数据模型与数据库迁移

- **范围与边界**：ORM、Pydantic v2 schema、表名和关系、外键、唯一约束、索引、
  事务与删除策略；迁移演进和数据兼容性。静态检查查询形态与性能风险，不推测
  生产数据量或执行计划；业务状态正确性分别交对应领域。
- **代码入口**：[模型](../../backend/models/)、[API schema](../../backend/api/schemas/)、
  [Alembic](../../backend/alembic/)、[数据库入口](../../backend/core/database.py)。
- **测试入口**：[迁移 head 测试](../../tests/test_alembic_heads.py)、
  [空库迁移测试](../../tests/test_alembic_upgrade.py)、
  [租约唯一约束测试](../../backend/tests/services/test_device_leases_unique.py)。
- **权威文档**：[数据模型](../design/05-data-model.md)、
  [迁移治理 ADR](../adr/ADR-0008-schema-migration-governance-alembic-only.md)、
  [测试隔离](../development/testing.md)。

### R04 项目、主机、设备与资源管理

- **范围与边界**：项目 / 机型归属与映射、主机注册、设备发现、心跳在线状态、
  资源池与容量；配置同步和热更新控制。资源信息如何被派发消费归 R06，
  安装包与服务部署归 R14；相关项目、主机、设备页面一起追踪。
- **代码入口**：[项目路由](../../backend/api/routes/projects.py)、[主机路由](../../backend/api/routes/hosts.py)、
  [设备路由](../../backend/api/routes/devices.py)、[资源池](../../backend/services/resource_pool.py)、
  [Agent 心跳](../../backend/agent/heartbeat.py)、[主机更新](../../backend/services/host_updater.py)。
- **测试入口**：[项目测试](../../backend/tests/api/test_project_routes.py)、
  [资源池测试](../../backend/tests/api/test_resource_pools.py)、
  [主机页面测试](../../frontend/src/pages/hosts/HostsPage.test.tsx)。
- **权威文档**：[项目归属与参数分层 ADR](../adr/ADR-0029-project-taxonomy-and-param-layering.md)、
  [Agent 版本与热更新](../operations/agent-version-and-hot-update.md)。

### R05 Plan、Suite 与参数编排

- **范围与边界**：Plan / PlanStep 编辑、MTBF 用例集与绑定、参数分层和校验、
  默认值选择、快照生成与可追溯性；覆盖编排和用例集页面。
  重点是执行前定义，运行时快照物化归 R06，脚本版本内容归 R08。
- **代码入口**：[Plan 路由](../../backend/api/routes/plans.py)、[Suite 路由](../../backend/api/routes/suites.py)、
  [Suite 绑定](../../backend/services/suite_binding.py)、[运行上下文](../../backend/services/plan_run_context.py)、
  [编排页面](../../frontend/src/pages/orchestration/)。
- **测试入口**：[绑定门禁测试](../../backend/tests/services/test_suite_binding_gate.py)、
  [Suite API 测试](../../backend/tests/api/test_mtbf_suite_routes.py)、
  [Plan 编辑测试](../../frontend/src/pages/orchestration/PlanEditPage.test.tsx)。
- **权威文档**：[参数分层 ADR](../adr/ADR-0029-project-taxonomy-and-param-layering.md)、
  [多用例管理 ADR](../adr/ADR-0030-multi-case-suite-management.md)、
  [MTBF Suite 设计](../design/2026-08-mtbf-p1-suite-management.md)。

### R06 调度、派发与执行状态闭环

- **范围与边界**：Precheck、排队准入、快照物化、设备扇出、租约 / fencing、
  claim / 续租 / complete、abort / UNKNOWN、超时回收、聚合、Cron 与 Plan 链补偿。
  本区负责控制面状态权威；实际停止与恢复由 R07 对证，消息交付机制归 R11。
- **代码入口**：[派发器](../../backend/services/plan_dispatcher_sync.py)、
  [状态机](../../backend/services/state_machine.py)、[租约管理](../../backend/services/lease_manager.py)、
  [调度与回收](../../backend/scheduler/)、[Agent API](../../backend/api/routes/agent_api.py)。
- **测试入口**：[派发测试](../../backend/tests/services/test_plan_dispatcher.py)、
  [abort / 聚合竞态测试](../../backend/tests/services/test_plan_run_abort_aggregator_race.py)、
  [终态处理测试](../../backend/tests/services/test_job_terminalization.py)。
- **权威文档**：[执行协议](../design/07-execution-protocol.md)、
  [执行主链](../design/01-execution-pipeline.md)、[扩容 ADR](../adr/ADR-0026-plan-execution-scaling.md)。

### R07 Agent 执行引擎与运行可靠性

- **范围与边界**：Pipeline / JobSession、ADB、进程组、并发限制、取消与清理、
  断线恢复、checkpoint、patrol、本地持久化和终态重试。
  控制面终态规则归 R06，脚本业务实现归 R08，异常事件的业务状态归 R09。
- **代码入口**：[Agent 主入口](../../backend/agent/main.py)、[执行引擎](../../backend/agent/pipeline_engine.py)、
  [JobSession](../../backend/agent/job_session.py)、[本地注册与状态](../../backend/agent/registry/)、
  [outbox 重试](../../backend/agent/outbox_drainer.py)。
- **测试入口**：[JobSession 端到端测试](../../backend/agent/tests/test_job_session_e2e.py)、
  [进程组测试](../../backend/agent/tests/test_pipeline_engine_process_group.py)、
  [checkpoint 测试](../../backend/agent/tests/test_pipeline_engine_checkpoint.py)。
- **权威文档**：[Agent 设计](../design/04-agent.md)、[执行协议](../design/07-execution-protocol.md)、
  [Agent scoped 指引](../../backend/agent/CLAUDE.md)。

### R08 脚本库、版本与外部工具接入

- **范围与边界**：已发布脚本与默认参数不可变性、注册与哈希校验、参数契约、
  脚本管理页面；刷机、Monkey、MTBF 等工具调用、包与资源依赖。
  明确区分现有调用链和 ADR-0033 规划；引擎执行机制归 R07，日志处理算法归 R10。
- **代码入口**：[版本化脚本](../../backend/agent/scripts/)、[脚本校验](../../backend/agent/script_verifier.py)、
  [本地脚本注册](../../backend/agent/registry/script_registry.py)、
  [控制面脚本目录](../../backend/services/script_catalog.py)。
- **测试入口**：[脚本校验测试](../../backend/agent/tests/test_script_verifier.py)、
  [脚本注册测试](../../backend/agent/tests/test_script_registry.py)、
  [版本不可变门禁测试](../../tests/test_script_version_immutability_gate.py)。
- **权威文档**：[脚本版本约定](../development/script-versioning.md)、
  [工具接入 ADR](../adr/ADR-0033-tool-kit-ecosystem-integration.md)、
  [工具接入设计](../design/2026-09-external-tools-integration-and-package-architecture.md)。

### R09 设备日志采集与异常事件

- **范围与边界**：Watcher、AEE、MTK / UNISOC 等平台路由、崩溃发现、事件采集、
  DeviceLogEvent / signal / outbox、补采、去重与归属。
  追踪至控制面事件事实可靠落库；事件目录上传、中心存储和结果归并归 R10。
- **代码入口**：[Watcher](../../backend/agent/watcher/)、[AEE](../../backend/agent/aee/)、
  [设备日志事件](../../backend/services/device_log_event.py)、[日志信号](../../backend/services/job_log_signal.py)。
- **测试入口**：[AEE Reconciler 测试](../../backend/agent/tests/test_aee_reconciler.py)、
  [事件 API 测试](../../backend/tests/api/test_agent_device_log_events.py)、
  [outbox 死信测试](../../backend/agent/tests/test_outbox_drainer_dead_letter.py)。
- **权威文档**：[事件与持续上传 ADR](../adr/ADR-0028-device-log-event-and-continuous-upload.md)、
  [DeviceLogEvent 规格](../design/2026-device-log-event-implementation-spec.md)、
  [AEE scoped 指引](../../backend/agent/aee/CLAUDE.md)。

### R10 扫描、上传、存储与结果后处理

- **范围与边界**：scan → upload → merge → extract、完成屏障、重试、SSD / HDD /
  中心存储、归档与清理；去重结果、风险汇总、报告导出、JIRA 及相关页面。
  事件发现归 R09，SAQ 通用交付机制归 R11；存储不可用时不能把链路视为已完成。
- **代码入口**：[扫描](../../backend/agent/scan_runner.py)、[展锐扫描](../../backend/agent/unisoc_scan_runner.py)、
  [事件上传](../../backend/agent/event_uploader.py)、[扫描产物上传](../../backend/agent/upload_manager.py)、
  [归并](../../backend/services/dedup_scan.py)、[提取](../../backend/services/dedup_extract.py)、
  [完成后处理](../../backend/services/post_completion.py)。
- **测试入口**：[事件上传测试](../../backend/agent/tests/test_event_uploader.py)、
  [扫描归并测试](../../backend/tests/services/test_dedup_scan_merge.py)、
  [提取测试](../../backend/tests/services/test_dedup_extract.py)。
- **权威文档**：[跨进程契约](../design/2026-scan-upload-merge-contract.md)、
  [存储与访问](../design/2026-plan-c-storage-and-access.md)、
  [双平台去重 ADR](../adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md)。

### R11 实时通信与异步任务基础设施

- **范围与边界**：Socket.IO 连接、订阅、重连、多进程路由、实时日志、RunConsole；
  SAQ 投递、幂等、恢复、积压及通知交付。状态转换归 R06，日志业务处理归 R09–R10，
  前端缓存消费归 R12；Redis 不成为业务事实存储。
- **代码入口**：[实时模块](../../backend/realtime/)、[后台任务](../../backend/tasks/)、
  [RunConsole](../../backend/services/run_console.py)、[通知服务](../../backend/services/notification_service.py)、
  [leader 选举](../../backend/core/leader_election.py)。
- **测试入口**：[超时实时推送测试](../../backend/tests/integration/test_pending_timeout_socketio.py)、
  [SAQ 测试](../../backend/tests/tasks/test_saq_tasks.py)、
  [RunConsole 测试](../../backend/tests/services/test_run_console.py)。
- **权威文档**：[实时与后台设计](../design/06-realtime-and-background.md)、
  [控制面横向扩展 ADR](../adr/ADR-0027-control-plane-horizontal-scaling.md)。

### R12 前端架构与交互体验

- **范围与边界**：路由、鉴权状态、API 类型入口、React Query 缓存与实时同步、
  错误 / 空态、操作反馈、可访问性、视觉一致性及大列表性能。
  页面业务正确性与对应领域联审，避免仅按页面数宣称覆盖；目视或运行时未验证要明示。
- **代码入口**：[路由](../../frontend/src/router/)、[API 类型](../../frontend/src/utils/api/types.ts)、
  [hooks](../../frontend/src/hooks/)、[组件](../../frontend/src/components/)、
  [页面](../../frontend/src/pages/)、[设计令牌](../../frontend/src/design-system/)。
- **测试入口**：[API 客户端测试](../../frontend/src/utils/client_strict.test.ts)、
  [Socket.IO hook 测试](../../frontend/src/hooks/useSocketIO.test.ts)、
  [QueryProvider 测试](../../frontend/src/components/QueryProvider.test.ts) 及各页面相邻测试。
- **权威文档**：[前端设计](../design/03-frontend.md)、
  [页面壳规范](../design/2026-08-21-frontend-page-shell-spec.md)。

### R13 平台 AI 助手

- **范围与边界**：会话与轮次状态、模型调用、工具注册和权限分层、写操作确认、
  执行时重新鉴权、提示注入、命令隔离、敏感信息暴露、超时取消和审计。
  复用的业务操作回到对应领域核对，不能只检查工具包装层。
- **代码入口**：[AI 服务](../../backend/services/ai_assistant/)、[AI 安全](../../backend/core/ai_security.py)、
  [AI API](../../backend/api/routes/ai_assistant.py)、[助手页面](../../frontend/src/pages/assistant/)。
- **测试入口**：[AI 权限测试](../../backend/tests/services/test_ai_authz.py)、
  [AI 安全测试](../../backend/tests/core/test_ai_security.py)、
  [AI API 测试](../../backend/tests/api/test_ai_assistant_endpoints.py)。
- **权威文档**：[AI 助手 ADR](../adr/ADR-0031-platform-ai-assistant.md)、
  [核心写工具附录](../adr/ADR-0031-appendix-phase3-core-write-tools.md)、
  [AI 助手设计](../design/2026-08-27-platform-ai-assistant.md)。

### R14 部署、运维与可观测性

- **范围与边界**：systemd、Nginx、容器与配置加载、Agent 安装升级、健康检查、
  指标告警、日志轮转、备份恢复、容量和故障恢复设计。
  只审仓库模板与实现，不读取实际主机清单或执行备份、恢复、迁移和部署。
- **代码入口**：[部署模板](../../deploy/)、[Ansible 工具目录](../../tools/ansible/)、
  [Agent 控制脚本](../../backend/agent/agentctl)、[备份脚本](../../scripts/pg_backup.sh)、
  [恢复验证脚本](../../scripts/pg_restore_test.sh)、[后端镜像定义](../../Dockerfile.backend)。
- **测试入口**：[Agent 控制契约测试](../../tests/test_agentctl_contract.py)、
  [生产配置源测试](../../tests/test_production_env_source.py)、
  [升级 playbook 测试](../../tests/test_update_agent_playbook.py)。
- **权威文档**：[运维入口](../operations/README.md)、[本地开发](../development/local-development.md)、
  [生产诊断边界](../operations/production-diagnostics.md)、
  [最小部署清单](../production-minimum-deployment-checklist.md)。

### R15 测试、CI 与工程治理

- **范围与边界**：测试隔离与 fixture 安全、单元 / 集成 / 故障路径覆盖、依赖与 lock、
  质量门禁、required checks、FIFO auto-merge、Agent Note、权威文档及 Harness 入口。
  各领域测试随 R01–R14 同步审查，本区检查整体体系，不替代领域验证。
- **代码入口**：[CI workflows](../../.github/workflows/)、[门禁入口](../../scripts/run_gates.py)、
  [根 fixture](../../conftest.py)、[控制面 fixture](../../backend/tests/conftest.py)、
  [治理检查](../../tools/dev/check_governance_surface.py)。
- **测试入口**：[仓库契约测试](../../tests/)、[Agent 测试](../../backend/agent/tests/)、
  [控制面测试](../../backend/tests/)、[前端测试配置](../../frontend/vitest.config.ts)。
- **权威文档**：[测试指南](../development/testing.md)、[依赖与门禁](../development/dependencies-and-quality.md)、
  [仓库工作流](../development/repository-workflow.md)、[Harness 适配](../development/ai/harness-adapters.md)。

## 3. 每区共同检查维度

每区至少记录以下维度的检查证据或不适用理由，不能因没有发现问题而省略覆盖记录：

1. **契约与正确性**：输入校验、状态与枚举、错误语义、边界条件、数据权威及类型同步。
2. **并发与恢复**：重入、幂等、竞态、重复 / 乱序、超时、取消、崩溃恢复与资源释放。
3. **安全与隔离**：身份、权限、数据可见性、路径 / 命令边界、敏感信息与生产隔离。
4. **性能与运维**：查询和批处理、缓存、背压、资源上限、日志指标、诊断与恢复手段。
5. **测试与验收**：现有断言实际覆盖什么，缺少哪些失败路径；只报告实际执行的验证。
6. **文档与实现**：现行设计、ADR 状态和代码是否一致；过时文档与未实施规划分别记录。

## 4. 轮次、证据与报告

### 4.1 长期总纲与每轮进度分离

- 本文件维护稳定分区与入口。首次实际开展审查时，才创建本轮总记录
  `docs/reviews/PROJECT_REVIEW_<YYYY-MM-DD>_<short-sha>.md`，并登记到第 5 节。
- 本轮总记录包含完整基线 commit、开始日期、约定范围、工作区差异是否纳入，
  以及 R01–R15 和跨区收口各自的状态、实际审查 commit、日期和报告链接。
- 开始或续审前检查实际 worktree diff；存在并发变更时记录影响，不回滚他人修改。
  审查中基线变化须补审相关区域或记录不同基线，不能把混合版本包装成同一快照结论。
- 某区域实际开展时再创建 `REVIEW_<YYYY-MM-DD>_<short-sha>_Rxx.md`，从本轮总记录关联。
  不预建空报告，不因创建文件或阅读目录而增加完成度。
- 采用 **未开始 / 进行中 / 待验证 / 已完成** 四种审查状态。
  “已完成”只表示约定范围的审查交付完成，不表示无缺陷、修复完成或生产验收通过；
  影响主要结论的关键证据不足时应保持“待验证”。

### 4.2 发现的分类与严重程度

每项发现使用稳定 ID，例如 `R06-F01`。跨区问题只有一个主要归属，其他区域引用
同一 ID；主要归属只是报告去重，不代表目录或修复所有权。

| 类型 | 证据要求 |
|---|---|
| 确定缺陷 | 当前基线存在可定位的错误路径，给出触发条件、影响和源码或测试证据 |
| 设计风险 | 说明适用前提及现有防护，不把潜在问题写成已发生故障 |
| 待验证项 | 写明缺少的证据、下一步安全验证方式及受影响结论 |

严重程度与证据类型分开记录。使用本轮审查的 P0–P3 分级，不与设备异常风险评级混用：

- **P0**：紧急安全、数据破坏或不可逆生产风险，需要优先处置。
- **P1**：主链无法完成、重复执行、关键结果丢失或重要权限边界失效。
- **P2**：条件性功能错误、恢复 / 可观测性缺口或显著交互问题。
- **P3**：低风险维护性、局部体验或文档一致性问题。

以上按实际影响判定，不按所在模块自动定级。待验证项的分级应注明暂定，历史报告
中的严重程度也必须重新核对。整改建议只描述方向，不在只读审查中直接实施。

### 4.3 区域报告模板

```markdown
# Rxx 区域名称审查

## 范围与基线
- 轮次 / 完整 commit / 审查日期 / 状态：
- 纳入的工作区差异、已读入口与范围排除项：
- 关联区域与历史线索：

## 结论概览
- 确定缺陷、设计风险、待验证项分别汇总；无发现不等于无缺陷。

## 覆盖与验证
| 场景或维度 | 源码 / 测试证据 | 实际命令与结果，或 pending 原因 |
|---|---|---|

## 发现与证据
### Rxx-F01 标题
- 类型 / 严重程度（待验证时注明暂定）：
- 位置：基线 commit 下的 path:line。
- 触发条件、调用链与实际影响：
- 现有防护、测试断言与证据局限：
- 修复建议、验证建议与关联区域：

## 后续与交接
- 未覆盖项、待验证项及跨区追踪：
- 已有 Issue / 后续报告链接（如有）：
```

命令返回成功不自动等于验证通过，必须解释其断言或检查范围。未运行的测试、
迁移、真实设备操作和目视验证均明确标为 pending 或不在本次范围，不能写“已通过”。

## 5. 当前登记

| 项目 | 状态 |
|---|---|
| 总纲建立日期 | 2026-09-06 |
| 编写时结构参考 | `6a8853a2cb8af80bf3ebc75627772679f15da3ac`，仅用于导航校准，不是正式审查结果基线 |
| 首轮正式审查基线 / 开始日期 | pending，首次开展时记录 |
| R01–R15 | 全部未开始 |
| 跨区收口 | 未开始 |
| 本轮总记录与区域报告 | 尚未建立，实际开展后再登记链接 |

历史审查不自动计入本轮进度。下一轮创建独立总记录并保留历史入口，不覆盖旧轮次
的证据和结论。本次文档建立的取舍见
[Agent Note](../notes/process/2026-09-06-project-review-plan.md)。

## 6. 跨区收口与完成标准

逐区完成后，至少对证两条端到端链路；每一跳核对生产者、消费者、持久化权威、
失败恢复、完成条件和对应测试，而不是仅确认文件或函数存在。

1. **执行链**：Plan / Suite → 快照与派发 → 租约 / claim → Agent 执行 →
   complete 终态 ACK → 聚合 → 实时通知与页面展示。
   覆盖正常完成、abort、超时、断连重启及重复 / 迟到回报。
2. **日志链**：异常发现 → 本地落盘 → 事件上报 / 文件上传 → 扫描归并 →
   归档 / 提取 → 报告 / JIRA / 页面消费。
   覆盖补采重试、存储不可用、上传与 merge 时序、跨平台隔离及清理后的可追溯性。

全面审查交付完成需同时满足：

- R01–R15 均有实际报告和覆盖记录，排除项说明理由；不存在未交接的跨区问题。
- 收口报告引用各区证据，核对基线差异；有影响的代码变更已补审或明确保留限制。
- 所有发现具备类型、严重程度、位置、触发条件、影响和建议，重复发现已关联去重。
- 未修复问题与待验证事项独立保留；关键证据仍不足时，不宣称全面审查已完成。
- 审查完成、缺陷修复完成、动态验证通过和生产验收通过分别表述，互不替代。

长期分区与导航仅随架构、模块路径或审查方法变化更新；第 5 节维护轮次入口与概况。
具体问题和详细进度留在各轮报告及已有跟踪载体中，避免总纲膨胀为第二份业务规范
或问题台账。
