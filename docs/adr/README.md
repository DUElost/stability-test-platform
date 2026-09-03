# ADR（Architecture Decision Record）索引与规范

本目录用于沉淀 `stability-test-platform` 的架构决策，服务于长期维护与 AI 检索。

## 状态定义

- `Proposed`：已提出，待评审/待实施。
- `Accepted`：已确认并作为当前基线执行。
- `Superseded`：已被后续 ADR 替代。
- `Deprecated`：不再推荐使用，但暂未完全移除。

## 优先级定义

- `P0`：必须优先完成，直接影响系统稳定性/安全性/发布可行性。
- `P1`：应在近期里程碑内完成，显著影响效率与可维护性。
- `P2`：中期推进，偏能力增强与体验提升。

## 编号与命名

- 文件名格式：`ADR-xxxx-<slug>.md`
- 编号规则：按提交顺序递增，不复用旧编号。
- 推荐先新增 ADR，再做代码改动；若代码已先落地，需补录 ADR 并标明“补录日期”。

## 编写模板

```md
# ADR-xxxx: 标题
- 状态：Proposed | Accepted | Superseded | Deprecated
- 优先级：P0 | P1 | P2（Proposed 建议必填）
- 目标里程碑：M1 | M2 | M3（Proposed 建议必填）
- 日期：YYYY-MM-DD
- 决策者：架构组/研发组
- 标签：调度, 数据库, 安全

## 背景

## 决策

## 备选方案与权衡

## 影响

## 落地与后续动作

## 关联实现/文档
```

## 当前 ADR 清单

| 编号 | 标题 | 状态 | 优先级 | 目标里程碑 | 类型 |
|---|---|---|---|---|---|
| [ADR-0001](./ADR-0001-control-plane-and-agent-architecture.md) | 控制面 + 执行面分层架构 | Accepted | - | - | 已实现 |
| [ADR-0002](./ADR-0002-single-process-with-internal-schedulers.md) | 单进程后端 + 内置后台调度线程 | Accepted | - | - | 已实现 |
| [ADR-0003](./ADR-0003-task-run-state-machine-and-device-lock-lease.md) | 任务状态机与设备锁租约机制 | Accepted | - | - | 已实现（2026-03-16 更新：统一锁服务 + 会话看门狗） |
| [ADR-0004](./ADR-0004-heartbeat-driven-host-device-liveness.md) | 心跳驱动的主机/设备在线性模型 | Accepted | - | - | 已实现（2026-03-16 更新：watchdog 接管心跳超时） |
| [ADR-0005](./ADR-0005-database-strategy-sqlite-first-postgresql-ready.md) | SQLite 起步 + PostgreSQL 兼容演进 | Deprecated | - | - | 已废弃，使用 PostgreSQL |
| [ADR-0006](./ADR-0006-realtime-communication-rest-plus-websocket.md) | REST + WebSocket 的实时通信分工 | Accepted | - | - | 已实现 |
| [ADR-0007](./ADR-0007-tool-template-workflow-extension-model.md) | 工具配置 + 任务模板 + 工作流扩展模型 | Accepted | - | - | 已实现 |
| [ADR-0008](./ADR-0008-schema-migration-governance-alembic-only.md) | 统一 Schema 迁移治理（Alembic Only） | Accepted | P0 | M1 | 预扩展/重构 |
| [ADR-0009](./ADR-0009-websocket-auth-and-endpoint-config-unification.md) | WebSocket 鉴权与端点配置统一化 | Accepted | P0 | M1 | 已实现（2026-03-24） |
| [ADR-0010](./ADR-0010-deployment-pipeline-jobification.md) | 部署能力作业化（异步、幂等、可回放） | Superseded | P1 | M2 | 已被 ADR-0020 取代（2026-06-12） |
| [ADR-0011](./ADR-0011-observability-and-alerting-evolution.md) | 可观测性与告警体系演进 | Proposed | P1 | M2 | 预扩展/重构 |
| [ADR-0012](./ADR-0012-post-completion-pipeline-jira-automation.md) | 后处理流水线到 JIRA 自动提交演进 | Accepted | P2 | M3 | 第 1 层已实现 |
| [ADR-0013](./ADR-0013-frontend-feature-expansion.md) | 前端功能模块扩展（任务实例、问题追踪、环境资源） | Accepted | P1 | M2 | 已实现 |
| [ADR-0014](./ADR-0014-pipeline-execution-engine.md) | Pipeline 执行引擎架构 | Accepted | P1 | M2 | 已实现（2026-03-16 更新：锁验证 + 参数表单） |
| [ADR-0015](./ADR-0015-audit-log-system.md) | 审计日志系统 | Accepted | P1 | M2 | 已实现 |
| [ADR-0016](./ADR-0016-deprecate-base-test-case.md) | 废弃 BaseTestCase，以 Pipeline Action 为唯一执行模型 | Accepted | P0 | M2 | 已完成（`test_framework.py` 已删除，代码零残留） |
| [ADR-0017](./ADR-0017-phase0-state-closure.md) | Phase 0 状态闭环 | Accepted | P0 | M1 | 已实现 |
| [ADR-0018](./ADR-0018-infrastructure-layer-framework-adoption.md) | 基础设施层框架引入（SAQ / APScheduler / python-socketio） | Accepted | P0 | M2 | 已实现 |
| [ADR-0019](./ADR-0019-android-device-lease-and-capacity-scheduling.md) | Android Device Lease 与容量调度模型 | Accepted | P0 | M3 | 已实现（2026-05-04 Phase 1-6e 落地，TTL/grace 调优为持续运维项） |
| [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md) | Plan-Step 一次性切换与旧编排模型移除 | Accepted | P0 | M3 | 预扩展/重构 |
| [ADR-0021](./ADR-0021-script-content-alignment-gate.md) | 派发门禁 / PlanRun 详情 / 脚本内容对齐 | Accepted | P0 | M3 | 已实现（C5a–C6） |
| [ADR-0022](./ADR-0022-patrol-heartbeat-aggregation.md) | Patrol 周期心跳聚合与退避 | Accepted | P1 | M3 | 已实现 |
| [ADR-0023](./ADR-0023-script-traceability.md) | 脚本溯源与 sha256 契约 | Accepted | P1 | M3 | 已实现 |
| [ADR-0024](./ADR-0024-browser-session-security-hardening.md) | 浏览器 Web 会话安全化（HttpOnly Cookie + CSRF + refresh 黑名单 + 可观测） | Accepted | P0 | M3.2 | 已实现（2026-05-21） |
| [ADR-0025](./ADR-0025-phase4-architecture-alignment.md) | Phase 4 架构对齐（方案 C：存储三级 + Agent 归档闭环） | Accepted | P2 | M4 | 已实现（Sprint 1–4，见 [DOC-MAP](../DOC-MAP.md) / acceptance） |
| [ADR-0026](./ADR-0026-plan-execution-scaling.md) | 大规模化测试计划执行架构（PlanRun 准入队列 + 四层调度 + 控制面减负） | Accepted | P0 | M5 | P0–P2 已收口（含 Step 5b / barrier / terminalization / step_log 批量化 / 索引与指标）；待定清单 v1 已回填；P3 → ADR-0027 |
| [ADR-0027](./ADR-0027-control-plane-horizontal-scaling.md) | 控制面水平扩展（Leader Election + 多实例） | Accepted | P2 | M6 | P3-1..P3-3 已落地（opt-in 多实例）；默认单实例零变化 |
| [ADR-0028](./ADR-0028-device-log-event-and-continuous-upload.md) | 设备日志事件实体 + PlanRun FAILED 触发上送 + 存储路径收敛（方案 A，2026-08-12 修订） | Accepted | P1 | 阶段 3 | 方案 A 生产生效（2026-08-13）：upload_task=控制面长期筛选者（LOCAL→UPLOAD_PENDING），EventUploader=Agent 侧唯一执行者（copytree/重试/PRUNE）；#287：CONTINUOUS 逃生阀删除，过滤模型是唯一路径；DLE 单一开关默认开 |
| [ADR-0029](./ADR-0029-project-taxonomy-and-param-layering.md) | 项目分类域（TestProject 登记簿 + facet 分类） | Accepted | P1 | M7 | v2.5：**归属派生化**（`device.project_id` 删列改 JOIN；`project_model` 为成员唯一事实源；哨兵 GENERIC/LEGACY 出表、`plan.project_id` 恢复可空；facet 减列 + jira 校验；详情页换问题）。**M1→M4 已落地**。v2.4：登记簿产品面只列人工 `USER` 项目；P1 六个回填 key 为 `SEED`，不进 `/projects`。项目模型收窄为**登记簿**（客户 / 关系 / 形态 / jira 映射）；APK 差异由**脚本端设备指纹路由**吸收（`backend=auto` 先例，路由表住工具目录 + step_trace 记 sha256）。**D1/D4/D5/D7/D8/D9 与 D6 的 `applicable` 已挂起**（原文保留、各有复议触发条件，未触发前不得重提）；生效的是 D2/D3/D6 `specialty`。落地 P1–P3 最小形态。背景分析见 [reviews](../reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md) |
| [ADR-0030](./ADR-0030-multi-case-suite-management.md) | 多用例平台化管理（test_suite / test_case + 外部管理面） | Accepted | P0+P1 | M7 | v1.9：P0 验收✅ + **P1 全部✅ + D6 真机冒烟✅**（#404）+ **P2 核心✅**（#429：套件管理 UI + `test_case_result`/`TestCaseResultsCard`）+ **mtbf 绑定翻转硬拒**（v1.8）。**未做**：JobArtifact `report` 白名单。背景：[reviews](../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) |
| [ADR-0031](./ADR-0031-platform-ai-assistant.md) | 平台 AI 助手（运维域 LLM 助手与风险分级自治边界） | Accepted | P1 | M8 | v1.5：阶段二全栈 ✅（T0-T3 四级自治 / httpx 载体 / DB+Fernet / RunConsole / 角色裁剪工具面 / 二轮审核 H1–M5）。设计见 [docs/design/2026-08-27-platform-ai-assistant.md](../design/2026-08-27-platform-ai-assistant.md) |
| [ADR-0032](./ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md) | 展锐与 MTK 并列日志链路（Watcher + 归档）（#463 / #73） | Accepted | P1 | M7 | v0.6：platform 路由；w1 Watcher + D4c 归档；`dedup/{run}/{mtk,unisoc}/` + 双 merge；TAG 共用 |
| [ADR-0033](./ADR-0033-tool-kit-ecosystem-integration.md) | 外部工具统一接入契约规范与包管理解耦模型（#745） | Accepted | P1 | M7 | v1.0：D0 阻断全量入仓；D1 三层宿主隔离；D2 Tool Contract 协议；D3 Manifest+包存储解耦；D4 防腐适配器 |

## Proposed 里程碑看板（2026 上半年）

| 里程碑 | 目标日期 | 包含 ADR |
|---|---|---|
| M1 | 2026-03-15 | ADR-0008, ADR-0009 |
| M2 | 2026-04-15 | ADR-0011（仍 Proposed）；ADR-0010 已由 ADR-0020 取代；ADR-0013/0014/0016/0018 已落地 |
| M3 | 2026-05-15 | ADR-0012（第 2-3 层）, ADR-0019, ADR-0020, ADR-0021–0023 |
| M4 | 2026-06+ | ADR-0025（方案 C Sprint 1–4）；PRD/设计/验收见 [`docs/DOC-MAP.md`](../DOC-MAP.md) |
| M5 | 2026-07 | ADR-0026 P0–P2（规模化执行正确性 + 控制面减负） |
| M6 | 待定 | ADR-0027（控制面水平扩展；重启条件见 ADR-0025 D1） |
| M7 | 进行中 | ADR-0029（项目分类域·登记簿；v2.5 派生归属 M1–M4 **已落地**）；ADR-0030（**Accepted** v1.9：P0 ✅ / P1 ✅ / D6 ✅ / **P2 核心 ✅** #429）；ADR-0031（**Accepted**：阶段二全栈 ✅，2026-08-28）；ADR-0032（**Accepted** v0.6：展锐 Watcher+归档，#463/#73）；ADR-0033（**Accepted** v1.0：外部工具接入契约与包管理解耦，#745） |

## 维护约定

- 每次关键架构变化必须新增或更新 ADR，并在 MR/PR 中引用。
- 若 ADR 被替代，旧 ADR 不删除，仅将状态改为 `Superseded` 并指向新 ADR。
- AI 生成方案或修改代码时，优先检索本目录并遵循 `Accepted` ADR。
