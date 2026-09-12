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
| [ADR-0002](./ADR-0002-single-process-with-internal-schedulers.md) | 单进程后端 + 内置后台调度线程 | Superseded | - | - | 已实现 |
| [ADR-0003](./ADR-0003-task-run-state-machine-and-device-lock-lease.md) | 任务状态机与设备锁租约机制 | Accepted | - | - | 已实现（2026-03-16 更新：统一锁服务 + 会话看门狗） |
| [ADR-0004](./ADR-0004-heartbeat-driven-host-device-liveness.md) | 心跳驱动的主机/设备在线性模型 | Accepted | - | - | 已实现（2026-03-16 更新：watchdog 接管心跳超时） |
| [ADR-0005](./ADR-0005-database-strategy-sqlite-first-postgresql-ready.md) | SQLite 起步 + PostgreSQL 兼容演进 | Deprecated | - | - | 已废弃，使用 PostgreSQL |
| [ADR-0006](./ADR-0006-realtime-communication-rest-plus-websocket.md) | REST + WebSocket 的实时通信分工 | Accepted | - | - | 已实现 |
| [ADR-0007](./ADR-0007-tool-template-workflow-extension-model.md) | 工具配置 + 任务模板 + 工作流扩展模型 | Accepted | - | - | 已实现 |
| [ADR-0008](./ADR-0008-schema-migration-governance-alembic-only.md) | 统一 Schema 迁移治理（Alembic Only） | Accepted | P0 | M1 | 预扩展/重构 |
| [ADR-0009](./ADR-0009-websocket-auth-and-endpoint-config-unification.md) | WebSocket 鉴权与端点配置统一化 | Superseded | P0 | M1 | 已实现（2026-03-24） |
| [ADR-0010](./ADR-0010-deployment-pipeline-jobification.md) | 部署能力作业化（异步、幂等、可回放） | Superseded | P1 | M2 | 已被 ADR-0020 取代（2026-06-12） |
| [ADR-0011](./ADR-0011-observability-and-alerting-evolution.md) | 可观测性与告警体系演进 | Accepted | P1 | M2 | 第一层已实现 |
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
| [ADR-0024](./ADR-0024-browser-session-security-hardening.md) | 浏览器 Web 会话安全化（HttpOnly Cookie + CSRF + refresh 黑名单 + 可观测） | Accepted | P0 | M3.2 | v1.2：internal 无 TLS 跨标签 refresh 已知限制（Web Locks 不可用，以单标签纪律缓解、随 #46 TLS 消除，#1200）；v1.1：internal 无 TLS 例外契约化（Secure 强制仅 ENV=production，边界与复议触发器见文末修订节，#909）；v1.0：已实现（2026-05-21） |
| [ADR-0025](./ADR-0025-phase4-architecture-alignment.md) | Phase 4 架构对齐（方案 C：存储三级 + Agent 归档闭环） | Accepted | P2 | M4 | 已实现（Sprint 1–4，见 [DOC-MAP](../DOC-MAP.md) / acceptance） |
| [ADR-0026](./ADR-0026-plan-execution-scaling.md) | 大规模化测试计划执行架构（PlanRun 准入队列 + 四层调度 + 控制面减负） | Accepted | P0 | M5 | P0–P2 已收口（含 Step 5b / barrier / terminalization / step_log 批量化 / 索引与指标）；待定清单 v1 已回填；P3 → ADR-0027 |
| [ADR-0027](./ADR-0027-control-plane-horizontal-scaling.md) | 控制面水平扩展（Leader Election + 多实例） | Accepted | P2 | M6 | P3-1..P3-3 已落地（opt-in 多实例）；默认单实例零变化；v1.2：清单增补 RunConsole 依赖功能单实例约束（#1114）；v1.3：「可不 sticky」加 Agent websocket-only 前提（#1121） |
| [ADR-0028](./ADR-0028-device-log-event-and-continuous-upload.md) | 设备日志事件实体 + PlanRun FAILED 触发上送 + 存储路径收敛（方案 A，2026-08-12 修订） | Accepted | P1 | 阶段 3 | 方案 A 生产生效（2026-08-13）：upload_task=控制面长期筛选者（LOCAL→UPLOAD_PENDING），EventUploader=Agent 侧唯一执行者（copytree/重试/PRUNE）；#287：CONTINUOUS 逃生阀删除，过滤模型是唯一路径；DLE 单一开关默认开 |
| [ADR-0029](./ADR-0029-project-taxonomy-and-param-layering.md) | 项目分类域（TestProject 登记簿 + facet 分类） | Accepted | P1 | M7 | v2.5：**归属派生化**（`device.project_id` 删列改 JOIN；`project_model` 为成员唯一事实源；哨兵 GENERIC/LEGACY 出表、`plan.project_id` 恢复可空；facet 减列 + jira 校验；详情页换问题）。**M1→M4 已落地**。v2.4：登记簿产品面只列人工 `USER` 项目；P1 六个回填 key 为 `SEED`，不进 `/projects`。项目模型收窄为**登记簿**（客户 / 关系 / 形态 / jira 映射）；APK 差异由**脚本端设备指纹路由**吸收（`backend=auto` 先例，路由表住工具目录 + step_trace 记 sha256）。**D1/D4/D5/D7/D8/D9 与 D6 的 `applicable` 已挂起**（原文保留、各有复议触发条件，未触发前不得重提）；生效的是 D2/D3/D6 `specialty`。落地 P1–P3 最小形态。背景分析见 [reviews](../reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md) |
| [ADR-0030](./ADR-0030-multi-case-suite-management.md) | 多用例平台化管理（test_suite / test_case + 外部管理面） | Accepted | P0+P1 | M7 | v1.9：P0 验收✅ + **P1 全部✅ + D6 真机冒烟✅**（#404）+ **P2 核心✅**（#429：套件管理 UI + `test_case_result`/`TestCaseResultsCard`）+ **mtbf 绑定翻转硬拒**（v1.8）。**未做**：JobArtifact `report` 白名单。背景：[reviews](../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) |
| [ADR-0031](./ADR-0031-platform-ai-assistant.md) | 平台 AI 助手（运维域 LLM 助手与风险分级自治边界） | Accepted | P1 | M8 | v1.5：阶段二全栈 ✅（T0-T3 四级自治 / httpx 载体 / DB+Fernet / RunConsole / 角色裁剪工具面 / 二轮审核 H1–M5）。设计见 [docs/design/2026-08-27-platform-ai-assistant.md](../design/2026-08-27-platform-ai-assistant.md) |
| [ADR-0032](./ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md) | 展锐与 MTK 并列日志链路（Watcher + 归档）（#463 / #73） | Accepted | P1 | M7 | v0.7：platform 路由；w1 Watcher + D4c 归档；`dedup/{run}/{mtk,unisoc}/` + 双 merge；TAG 共用 |
| [ADR-0033](./ADR-0033-tool-kit-ecosystem-integration.md) | 外部工具统一接入契约规范与包管理解耦模型（#745） | Accepted | P1 | M7 | v1.2：**收窄与登记**——D0/D3 权威即刻生效（不等包存储就绪）、D2 降为「新工具族准入、按族采用」、包存储改条件落地（三条触发条件）、legacy 例外（展锐三工具族 + `STP_UNISOC_*` 路径键）显式登记、§4 时间点作废为参考序；**落地状态：未落地**（Phase 2/3 零启动，2026-09-10 核验）；v1.1：D0 阻断全量入仓（分级准入）；D1 三层宿主隔离；D2 Tool Contract（退出码命名空间分层）；D3 Manifest 发布格式 × DB catalog 唯一权威；D4 防腐适配器（接口只包 vendor CLI）；与 ADR-0032 行为/结构权威分家（#1237） |
| [ADR-0034](./ADR-0034-multi-harness-execution-contract.md) | 多 Harness 并行执行契约与执行登记（#855 / #857） | Accepted | P1 | M7 | v1.12：CodeBuddy CLI/IDE 分立——附录 A 原单行实为 CLI 结论却被读作覆盖整个产品线（IDE 从未探针），2026-09-11 人工补测 IDE 得 Q1=否/Q2=是/Q3=一次（Zcode 同形态，与 CLI 相反），照 Cursor 先例拆两行、CLI 版本校正为 2.149.0、IDE 版本 4.11.3 补入（2026-09-11）；v1.11：dsh web 转正回填——Registry CLI 全周期 dogfood 通过（#1256/PR #1291，2026-09-10 合入）、0.1.5-rc.1 加载复测一致（2026-09-11）；v1.10：附录 A 增补 dsh web 实测——根级基线注入 ✅ + scoped 触碰后动态注入 ✅（typed source 实证）、静态 patch 层 disabled 与运行时矛盾记录在案、工作区原生目录选择器坑、Registry CLI 未 dogfood（2026-09-08）；v1.9：并发上限反转——移除 ≈2-3（未实测继承、被多批次 5+ 会话常态超出），瓶颈校准为集成收尾侧，守对象重锚为在窗 Execution 规模与 reconcile 负载（2026-09-08）；v1.8：role 缺省归一化（declare 缺省写 implementation）+ Role 扩展再开启条件成文（2026-09-08）；v1.7：Role 定位收敛——元数据+扩展点、默认 implementation、Role Runtime 降级 deferred（2026-09-08）；v1.6：选择权原则（Harness 由开发者决定）/ 三维状态模型 lifecycle×liveness×integration（ADR 实现选择，非冻结条款）/ Registry=visibility-only 非调度器 / `--path-format=absolute` 唯一发现方式 / effective scope=declared∪derived(diff) 并集恒成立+drift 提示 / overlap 真值表（开放 PR 恒在风险窗口）/ drift gate 非 merge queue / G2 真身+薄壳（symlink 优先）/ Antigravity=带规则的高级顾问不入 Harness 名单。执行细则权威源 `docs/development/ai/execution-contract.md`（P0a 建立）；两轮八源多 Harness 评审综合见 [reviews](../reviews/REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md) |
| [ADR-0035](./ADR-0035-agent-host-identity.md) | Agent 主机身份与凭据体系（R02-R01/#906） | Accepted | P1 | — | v1.2：**触发条件检测来源**（§6.1 四条触发各自钉到信号来源/检出方/命中后第一步——原四条全依赖外部信号，无来源映射即无人监视）；v1.1：**决策四段化**——§3 当前状态（接受共享 AGENT_SECRET + 威胁模型/冒充面收窄）/ §4 目标形态（A 每主机凭据）/ §5 迁移路径（C 注册质询 + 实施骨架）/ §6 升级触发条件；**ADR Accepted ≠ 实施已启动**，实施单另行拆分；由两份竞争提案 #1147（当前状态）+ #1163/#1170（目标形态）合并为单一权威 |
| [ADR-0036](./ADR-0036-notification-delivery-semantics.md) | 通知投递语义契约（Notification Delivery Semantics Contract） | **Accepted** | P2 | M7 | v1.0 定稿（2026-09-11，R11 #1117/#1120/#1122，台账 #1125）：定义 How delivery behaves——`ACCEPTED` = 渠道明确接受请求（≠ DELIVERED）/ 三态失败 `REJECTED_PERMANENT`·`REJECTED_TRANSIENT`·`UNKNOWN` / 网络投递必须有 deadline / 重试由 SAQ 唯一负责且**投递级幂等为成对硬约束** / at-least-once + 每通道去重键 / 投递事实必须落 DB / 同步仅限管理员连通性测试；协议状态码与 retry 参数**不入正文**；挂起端到端送达回执与入站契约。与 ADR-0011 分工 What vs How |
| [ADR-0037](./ADR-0037-agent-host-privilege-boundary.md) | Agent 主机提权边界（Privilege Boundary Wrapper） | **Proposed** | P1 | M7 | v0.1 初版（R14-F04 #1250，台账 #1266，待 R02 联审）：D1 单一提权入口 `/usr/local/sbin/stp-agent-priv`（root:root，不在 Agent 可写目录）+ sudoers 只授 wrapper 与固定 systemctl；D2 子命令白名单 + 路径/属主/内容校验（`chown -h`、`--safe-links`、mtbf exclude+protect）；D3 存量迁移由 install 链与 `update_agent.yml` bootstrap 重写 sudoers，迁移期热更新 legacy fallback + `priv_mode` 哨兵；D4 不动 Ansible 密码 become / 不引 per-host 凭据（ADR-0035 实施面） |
| [ADR-0038](./ADR-0038-host-retirement-semantics.md) | 主机退役语义（Host Retirement Semantics） | **Proposed** | P2 | M7 | v0.1 初版（#796/#937 Revisit 触发）：D1 `retired_at` 单一生命周期真源（additive nullable，不新增 HostStatus）；D2 retire/unretire 独立入口，`DELETE` 预检原样；D3 退役即终态，不提供带历史硬删；D4 生命周期与存活正交——心跳如实记录 + 保持退役 + 单次告警；D5 派发/认领/统计/列表/控制面五面收口；D6 同 IP 换机 = 同身份 unretire（ADR-0035 落地后重审） |

## Proposed 里程碑看板（2026 上半年）

| 里程碑 | 目标日期 | 包含 ADR |
|---|---|---|
| M1 | 2026-03-15 | ADR-0008, ADR-0009 |
| M2 | 2026-04-15 | ADR-0011（已 Accepted：第一层指标落地）；ADR-0010 已由 ADR-0020 取代；ADR-0013/0014/0016/0018 已落地 |
| M3 | 2026-05-15 | ADR-0012（第 2-3 层）, ADR-0019, ADR-0020, ADR-0021–0023 |
| M4 | 2026-06+ | ADR-0025（方案 C Sprint 1–4）；PRD/设计/验收见 [`docs/DOC-MAP.md`](../DOC-MAP.md) |
| M5 | 2026-07 | ADR-0026 P0–P2（规模化执行正确性 + 控制面减负） |
| M6 | 待定 | ADR-0027（控制面水平扩展；重启条件见 ADR-0025 D1） |
| M7 | 进行中 | ADR-0029（项目分类域·登记簿；v2.5 派生归属 M1–M4 **已落地**）；ADR-0030（**Accepted** v1.9：P0 ✅ / P1 ✅ / D6 ✅ / **P2 核心 ✅** #429）；ADR-0031（**Accepted**：阶段二全栈 ✅，2026-08-28）；ADR-0032（**Accepted** v0.7：展锐 Watcher+归档，#463/#73）；ADR-0033（**Accepted** v1.2：外部工具接入契约与包管理解耦；D0/D3 即刻生效、D2 按族准入、包存储条件落地；**未落地**，#745）；ADR-0034（**Accepted** v1.12：多 Harness 执行契约，#855/#857） |

## 维护约定

- 每次关键架构变化必须新增或更新 ADR，并在 MR/PR 中引用。
- 若 ADR 被替代，旧 ADR 不删除，仅将状态改为 `Superseded` 并指向新 ADR。
- AI 生成方案或修改代码时，优先检索本目录并遵循 `Accepted` ADR。
