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
- 附录规则（#1523）：ADR 的附录沿用父编号 + 大写字母后缀（`ADR-xxxx-A-<slug>.md`），不占独立编号、不参与编号递增；主文档引用附录用子编号指向。
- 推荐先新增 ADR，再做代码改动；若代码已先落地，需补录 ADR 并标明“补录日期”。

## 编写模板

```md
# ADR-xxxx: 标题
- 状态：Proposed | Accepted | Superseded | Deprecated
- 优先级：P0 | P1 | P2（Proposed 建议必填）
- 目标里程碑：M1 | M2 | M3 | M4 | M5 | M6 | M7（Proposed 建议必填）
- 日期：YYYY-MM-DD
- 决策者：架构组/研发组
- 标签：调度, 数据库, 安全
- 归属域：semantic-ownership <key>（新建 ADR 必填，无对应概念写 `n/a（理由）`；存量触碰时补。key 见 docs/design/2026-semantic-ownership.md）

## 背景

## 决策

## 备选方案与权衡

## 影响

## 落地与后续动作

## 关联实现/文档
```

> `归属域` 字段：**新建 ADR 必填**（S15⑦ 按头部日期 ≥ 2026-09-22 判，`n/a（理由）` 为合法逃生值），
> 存量**触碰即补**，不做一次性全库补齐（#3014 案 1A）。该字段指向的是
> [语义归属索引](../design/2026-semantic-ownership.md) 的表行 key，**不是**把内容裁决权交给索引。
> **修订既有 ADR 时不要把 `- 日期：` 改成修订日**：S15⑦ 以该日期判「是否新建」，把旧 ADR 的日期
> 刷到 cutoff 之后会让它突然需要 `归属域` 字段；修订一律走 `- 版本记录：`（#3014 案 1A 使用边界）。

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
| [ADR-0012](./ADR-0012-post-completion-pipeline-jira-automation.md) | 后处理流水线到 JIRA 自动提交演进 | Accepted | P2 | M3 | 第 1 层已实现；2026-09-25 裁决第 2–3 层：第 2 层收敛为「S/A 级 PlanRun 自动生成提单清单（`upload_list`，只出清单不建单）」待实施，第 3 层无人值守建单不采纳（建单保持人工触发；按发生去重归 ADR-0053 D7） |
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
| [ADR-0023](./ADR-0023-script-traceability.md) | 脚本溯源与 sha256 契约 | Accepted | P1 | M3 | D1 已实现；2026-09-25 裁决 D2–D8：D2/D3/D4（观测面脚本身份 + 快照浏览面）Accepted 待实施；D6 改判为源头守卫（catalog `retired: true` 路径补引用检查）待实施；D5/D7/D8 撤销（被 `ResourceAllocation` / `/scripts/{id}/usage` / 退役 SOP 取代） |
| [ADR-0024](./ADR-0024-browser-session-security-hardening.md) | 浏览器 Web 会话安全化（HttpOnly Cookie + CSRF + refresh 黑名单 + 可观测） | Accepted | P0 | M3.2 | v1.2：internal 无 TLS 跨标签 refresh 已知限制（Web Locks 不可用，以单标签纪律缓解、随 #46 TLS 消除，#1200）；v1.1：internal 无 TLS 例外契约化（Secure 强制仅 ENV=production，边界与复议触发器见文末修订节，#909）；v1.0：已实现（2026-05-21） |
| [ADR-0025](./ADR-0025-phase4-architecture-alignment.md) | Phase 4 架构对齐（方案 C：存储三级 + Agent 归档闭环） | Accepted | P2 | M4 | 已实现（Sprint 1–4，见 [DOC-MAP](../DOC-MAP.md) / acceptance） |
| [ADR-0026](./ADR-0026-plan-execution-scaling.md) | 大规模化测试计划执行架构（PlanRun 准入队列 + 四层调度 + 控制面减负；观测面 #2324 / #2369） | Accepted | P0 | M5 | P0–P2 已收口（含 Step 5b / barrier / terminalization / step_log 批量化 / 索引与指标 / Dashboard 观测面 #2324 + PlanRun/visibility/fleet room #2369）；待定清单 v1 已回填；P3 → ADR-0027 |
| [ADR-0027](./ADR-0027-control-plane-horizontal-scaling.md) | 控制面水平扩展（Leader Election + 多实例） | Accepted | P2 | M6 | P3-1..P3-3 已落地（opt-in 多实例）；默认单实例零变化；v1.2：清单增补 RunConsole 依赖功能单实例约束（#1114）；v1.3：「可不 sticky」加 Agent websocket-only 前提（#1121）；v1.4：P3-4 RunConsole 归属注册表 P1（全局 run_key 互斥 + owner 登记）；v1.5：P3-4 P2 状态快照（跨实例 status/订阅校验生效）；v1.6：P3-4 P3 cancel 转发；v1.7：P3-4 P4 跨实例 replay 收口（不再要求单实例，#1737）；v1.8：清单增补第 7 条 merge 实例绑定（本机 flock + 本机工具目录；缺跨实例互斥，启动 WARN，#2189） |
| [ADR-0028](./ADR-0028-device-log-event-and-continuous-upload.md) | 设备日志事件实体 + PlanRun FAILED 触发上送 + 存储路径收敛（方案 A，2026-08-12 修订） | Accepted | P1 | 阶段 3 | 方案 A 生产生效（2026-08-13）：upload_task=控制面长期筛选者（LOCAL→UPLOAD_PENDING），EventUploader=Agent 侧唯一执行者（copytree/重试/PRUNE）；#287：CONTINUOUS 逃生阀删除，过滤模型是唯一路径；DLE 单一开关默认开；2026-09-25 裁决：`PRUNE_LOCAL` **不在 fleet 开启**（HddSpill 兜磁盘安全、scan/baseline 依赖本地证据、终态归 ADR-0053 D3） |
| [ADR-0029](./ADR-0029-project-taxonomy-and-param-layering.md) | 项目分类域（TestProject 登记簿 + facet 分类） | Accepted | P1 | M7 | v2.5：**归属派生化**（`device.project_id` 删列改 JOIN；`project_model` 为成员唯一事实源；哨兵 GENERIC/LEGACY 出表、`plan.project_id` 恢复可空；facet 减列 + jira 校验；详情页换问题）。**M1→M4 已落地**。v2.4：登记簿产品面只列人工 `USER` 项目；P1 六个回填 key 为 `SEED`，不进 `/projects`。项目模型收窄为**登记簿**（客户 / 关系 / 形态 / jira 映射）；APK 差异由**脚本端设备指纹路由**吸收（`backend=auto` 先例，路由表住工具目录 + step_trace 记 sha256）。**D1/D4/D5/D7/D8/D9 与 D6 的 `applicable` 已挂起**（原文保留、各有复议触发条件，未触发前不得重提）；生效的是 D2/D3/D6 `specialty`。落地 P1–P3 最小形态。背景分析见 [reviews](../reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md) |
| [ADR-0030](./ADR-0030-multi-case-suite-management.md) | 多用例平台化管理（test_suite / test_case + 外部管理面） | Accepted | P0+P1 | M7 | v1.10：开放问题 3 裁定——`X-Agent-Secret` 对套件/用例管理面零授权，外部调用一律用户 token（写 = admin）。v1.9：P0 验收✅ + **P1 全部✅ + D6 真机冒烟✅**（#404）+ **P2 核心✅**（#429：套件管理 UI + `test_case_result`/`TestCaseResultsCard`）+ **mtbf 绑定翻转硬拒**（v1.8）。**未做**：JobArtifact `report` 白名单。背景：[reviews](../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) |
| [ADR-0031](./ADR-0031-platform-ai-assistant.md) | 平台 AI 助手（运维域 LLM 助手与风险分级自治边界） | Accepted | P1 | M8 | v1.7：阶段三落地（附录 A **Accepted** #658；工具面扩至 T0×14 / T1×3 / T2a×3 / T2b×6；新增 `t2b_auto_dispatch_allowlist`）。v1.6：权限对齐 **D8**（助手权限 ⊆ 账号 API 权限——`admin_only` 镜像 `require_admin`、`auto_approve` 与执行面复检发起人、`scan_script_catalog`/`test_notification_channel` 标 admin-only）。v1.5：阶段二全栈 ✅（T0-T3 四级自治 / httpx 载体 / DB+Fernet / RunConsole / 角色裁剪工具面 / 二轮审核 H1–M5）。设计见 [docs/design/2026-08-27-platform-ai-assistant.md](../design/2026-08-27-platform-ai-assistant.md) |
| [ADR-0031-A](./ADR-0031-A-appendix-phase3-core-write-tools.md) | ADR-0031 附录 A：阶段三核心业务写操作（Plan 执行链路工具面，#658） | Accepted | P1 | M8 | v1.1（2026-09-25）：§7 三项开放问题裁定——中止权限与 API 对齐不收紧、审批路径不设助手专属设备上限（T2b 白名单 `max_devices` 默认 20 / 硬顶 50）、`wifi_pool_id` 必须显式 ID 不推断。v1.0（2026-08-31 合入 main）：在不破坏「助手权限 ⊆ 账号 API 权限」（D8）前提下把 Plan 执行链路写操作纳入助手工具面 |
| [ADR-0032](./ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md) | 展锐与 MTK 并列日志链路（Watcher + 归档）（#463 / #73） | Accepted | P1 | M7 | v1.1：**D8 增补展锐 inotifyd 实时唤醒层**（opt-in 默认关；UNIVIEW 事件只作 reconciler.wake 唤醒、无双写；采纳前置=真机探测清单 #1998）/ v1.0：**DLE 终态语义两平台同构**（§D10，#463 裁决——`ARCHIVED` 同义同判、extract 归档链无平台分支、否决「上送即归档」）/ v0.9：**平台路由收口**（R1 完备性按 (host, platform) 期望集、R2 逐平台 merge 结果落 `run_context.merge_platforms`、R3 条件裁决由 v0.8 达成——D3 维持「同一 merge 工具」、R4 未支持态与死接口收口 a1/b1/b3，见 §D9）/ v0.8：**B3 spike 已执行**（2026-09-15，五项验收实测通过，D3「UNISOC 复用 MTK merge 工具」转已验证；第 1 项精确化为「列数同构、两列命名有差异且被工具归一」）/ v0.7：platform 路由；w1 Watcher + D4c 归档；`dedup/{run}/{mtk,unisoc}/` + 双 merge；TAG 共用 |
| [ADR-0033](./ADR-0033-tool-kit-ecosystem-integration.md) | 外部工具统一接入契约规范与包管理解耦模型（#745） | Accepted | P1 | M7 | v1.15：D3 按对象拆分回写——`kind=script` 注册为 script 行、`kind=tool` 不入 catalog（#3203，口径由 ADR-0051 v1.3 裁定）；v1.14：D0 机械门禁改由 ADR-0051 族级 `kind` 登记承担（`check_new_script_family` 退役，ADR-0051 D8）；v1.13：D3 一句措辞改采 C1 双列（`package_sha256 := tarball sha256`，`content_sha256` 仍为入口 sha；由 ADR-0051 D3 裁决）；v1.12：Phase B **第一切片已落机械面**（`tool_manifest.json` 唯一事实源+确定性打包器+lint/append-only 门禁+Agent `tools_cache` 拉取核验/env 回退，逃生阀默认关、fleet 未切换，跟踪 [#3075](https://github.com/DUElost/stability-test-platform/issues/3075)；评估 [`2026-09-22-adr0033-package-store-multisite-trigger`](../notes/architecture/2026-09-22-adr0033-package-store-multisite-trigger.md)）；v1.10：§5.6 D0 可拦对象口径；v1.9–v1.7 Phase A/A3；v1.6 历史「未触发」快照保留；**部分落地**（B5 + Phase A + A3 + Phase B 第一切片机械面；包存储 fleet 未切换；Phase 3 未做）；#745/#3075/#2546 |
| [ADR-0034](./ADR-0034-multi-harness-execution-contract.md) | 多 Harness 并行执行契约与执行登记（#855 / #857） | Accepted | P1 | M7 | v1.12：CodeBuddy CLI/IDE 分立——附录 A 原单行实为 CLI 结论却被读作覆盖整个产品线（IDE 从未探针），2026-09-11 人工补测 IDE 得 Q1=否/Q2=是/Q3=一次（Zcode 同形态，与 CLI 相反），照 Cursor 先例拆两行、CLI 版本校正为 2.149.0、IDE 版本 4.11.3 补入（2026-09-11）；v1.11：dsh web 转正回填——Registry CLI 全周期 dogfood 通过（#1256/PR #1291，2026-09-10 合入）、0.1.5-rc.1 加载复测一致（2026-09-11）；v1.10：附录 A 增补 dsh web 实测——根级基线注入 ✅ + scoped 触碰后动态注入 ✅（typed source 实证）、静态 patch 层 disabled 与运行时矛盾记录在案、工作区原生目录选择器坑、Registry CLI 未 dogfood（2026-09-08）；v1.9：并发上限反转——移除 ≈2-3（未实测继承、被多批次 5+ 会话常态超出），瓶颈校准为集成收尾侧，守对象重锚为在窗 Execution 规模与 reconcile 负载（2026-09-08）；v1.8：role 缺省归一化（declare 缺省写 implementation）+ Role 扩展再开启条件成文（2026-09-08）；v1.7：Role 定位收敛——元数据+扩展点、默认 implementation、Role Runtime 降级 deferred（2026-09-08）；v1.6：选择权原则（Harness 由开发者决定）/ 三维状态模型 lifecycle×liveness×integration（ADR 实现选择，非冻结条款）/ Registry=visibility-only 非调度器 / `--path-format=absolute` 唯一发现方式 / effective scope=declared∪derived(diff) 并集恒成立+drift 提示 / overlap 真值表（开放 PR 恒在风险窗口）/ drift gate 非 merge queue / G2 真身+薄壳（symlink 优先）/ Antigravity=带规则的高级顾问不入 Harness 名单。执行细则权威源 `docs/development/ai/execution-contract.md`（P0a 建立）；两轮八源多 Harness 评审综合见 [reviews](../reviews/REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md) |
| [ADR-0035](./ADR-0035-agent-host-identity.md) | Agent 主机身份与凭据体系（R02-R01/#906） | Accepted | P1 | — | v1.2：**触发条件检测来源**（§6.1 四条触发各自钉到信号来源/检出方/命中后第一步——原四条全依赖外部信号，无来源映射即无人监视）；v1.1：**决策四段化**——§3 当前状态（接受共享 AGENT_SECRET + 威胁模型/冒充面收窄）/ §4 目标形态（A 每主机凭据）/ §5 迁移路径（C 注册质询 + 实施骨架）/ §6 升级触发条件；**ADR Accepted ≠ 实施已启动**，实施单另行拆分；由两份竞争提案 #1147（当前状态）+ #1163/#1170（目标形态）合并为单一权威 |
| [ADR-0036](./ADR-0036-notification-delivery-semantics.md) | 通知投递语义契约（Notification Delivery Semantics Contract） | **Accepted** | P2 | M7 | v1.0：定稿（2026-09-11，R11 #1117/#1120/#1122，台账 #1125）：定义 How delivery behaves——`ACCEPTED` = 渠道明确接受请求（≠ DELIVERED）/ 三态失败 `REJECTED_PERMANENT`·`REJECTED_TRANSIENT`·`UNKNOWN` / 网络投递必须有 deadline / 重试由 SAQ 唯一负责且**投递级幂等为成对硬约束** / at-least-once + 每通道去重键 / 投递事实必须落 DB / 同步仅限管理员连通性测试；协议状态码与 retry 参数**不入正文**；挂起端到端送达回执与入站契约。与 ADR-0011 分工 What vs How |
| [ADR-0037](./ADR-0037-agent-host-privilege-boundary.md) | Agent 主机提权边界（Privilege Boundary Wrapper） | Accepted | P1 | M7 | v0.5：**`selftest` 之外的 `capabilities` 能力前置判据**（#2319：自指判据不覆盖版本新鲜度）；v0.4：**转 Accepted**——R02 安全联审一稿已交付（#2206 / PR #2209，结论「建议接受、无阻断」）；采纳 S1/O1/O2（§2 D6 边界承担声明、D3 信任边界、§4 强控制与 parser/边界测试列为不变量），S2/S3/O4 待排期。此前：v0.1 初版（R14-F04 #1250，台账 #1266，待 R02 联审）：D1 单一提权入口 `/usr/local/sbin/stp-agent-priv`（root:root，不在 Agent 可写目录）+ sudoers 只授 wrapper 与固定 systemctl；D2 子命令白名单 + 路径/属主/内容校验（`chown -h`、`--safe-links`、mtbf exclude+protect）；D3 存量迁移由 install 链与 `update_agent.yml` bootstrap 重写 sudoers，迁移期热更新 legacy fallback + `priv_mode` 哨兵；D4 不动 Ansible 密码 become / 不引 per-host 凭据（ADR-0035 实施面）；v0.2（2026-09-15，#2133）：§1.2 勘误（存量宽文件 `/etc/sudoers.d/android` 与 flash 修复分支均未消失，48/48 台实测在网）+ D5 flash 链运行时提权收敛（下线前置）+ §5 退役判据改为「fleet wrapper **且** flash 链无宽文件验收通过」；v0.3（2026-09-15，#2180）：§5 Revisit #1 执行完毕——legacy 分支/哨兵/`resources_priv_fallback` 删除，远端脚本改 selftest 前置 fail-closed，§4 回滚路径更新；v0.4（2026-09-15）：**转 Accepted**——R02 安全联审一稿已交付（#2206 / PR #2209，结论「建议接受、无阻断」）；采纳 S1/O1/O2（§2 D6 边界承担声明、D3 信任边界、§4 强控制与 parser/边界测试列为不变量），S2/S3/O4 待排期 |
| [ADR-0038](./ADR-0038-host-retirement-semantics.md) | 主机退役语义（Host Retirement Semantics） | **Accepted** | P2 | M7 | v0.3：2026-09-25 裁决生效（owner 授权 Claude，§7.7）——D9 设备面意图（空置/人工清空：可逆、与退役正交，`emptied_at/by/reason` 三列；`StabilityHostUsbBlind` 与 `StabilityHostAdbOfflineConcentration` 规则侧 `unless` 豁免，内核证据类两条不豁免；不设自动过期；新增 D9.8 `StabilityHostDeviceIntentStale` 意图陈旧 warning；不动派发/认领）；现网实例 [#3065](https://github.com/DUElost/stability-test-platform/issues/3065)、实施单 [#3159](https://github.com/DUElost/stability-test-platform/issues/3159)（未实施）。v0.2：定稿（2026-09-13；9 稿评审 synthesis [PR #1677](https://github.com/DUElost/stability-test-platform/pull/1677) + 人工裁决 D-1～D-6，[#1557](https://github.com/DUElost/stability-test-platform/issues/1557)）：D1 三列生命周期真源（+reason 必填/审计 fail-closed）；D2 DELETE 与 retire 分家 + Cordon 前置；D3 退役即终态；D4 心跳如实记录 + 新列去重（禁 `Host.extra` 裸键）；D5 14 面收口 + 派发归位 fatal + 在飞 Run 显式收敛；D6 身份契约（id 判定、ip 可变）+ 审计承诺降级；D7 显式不做；D8 retire ≠ credential revoke。评审会话×模型归属 [errata](https://github.com/DUElost/stability-test-platform/pull/1720) |
| [ADR-0040](./ADR-0040-deployment-artifact-digest-protocol.md) | 部署摘要协议（artifact 内容寻址 + 空操作收敛） | **Accepted** | P2 | M7 | v1.1：**判据唯一性**——面向运维的动作信号唯一由 digest 产生，revision 降为纯溯源、不再驱动 drift 徽章；显式放弃「revision 单独刷新」；unknown 语义成文（2026-09-15，#2057；修订记录 §10；实施跟踪 [#2155](https://github.com/DUElost/stability-test-platform/issues/2155)）。v1.0 定稿（2026-09-13，owner 裁决 D1–D7 全采纳，裁决记录 §9；[#1900](https://github.com/DUElost/stability-test-platform/issues/1900) 触发、[#1901](https://github.com/DUElost/stability-test-platform/issues/1901) 跟踪；**P0 过渡已落地** [#1904](https://github.com/DUElost/stability-test-platform/pull/1904)）：D1 双 artifact 内容寻址（`agent-code` 1.0MB/468 文件 vs `host-resources` 130.7MB/108 文件；digest 双侧镜像实现 + 字节级等价测试，先例 `script_catalog_version`）+ D2 远端单点上报（`ARTIFACT_DIGEST` + 心跳字段 + 显式列，禁 `Host.extra` 裸键）+ D3 相等即全链路 no-op（`--force` 逃生阀）/ 变更分层传输 + D4 restart 三类判据 + D5 四入口收敛与记录语义统一 + D6 per-phase 计时 + D7 显式不做 code 差量（含复议触发器） |
| [ADR-0039](./ADR-0039-script-version-immutability-narrowing.md) | 脚本版本不可变契约收窄——「直至零引用退役」（#735 §2 P1） | Superseded | P1 | M7 | **2026-09-22 由 ADR-0051 取代**：D1 被吸收，D2/D3/D4/D5/D7 由 ADR-0051 D5 显式继承（作用域目录→包），D6 否决前提被 ADR-0051 §2 撤销。历史：v0.1 初版（2026-09-13，由 `SCRIPT_VERSION_BLOAT_ENDGAME_FEASIBILITY_2026-09-10` 的 P1 路径触发）：修订 ADR-0020 与 `AGENTS.md` 硬不变量——D1 不可变收窄为两段式（`refs>0` 绝对不可变；`refs=0` 且已退役允许物理删除）；D2 删除权不下放自动流程（仅人工 PR + 附只读证据）；D3 退役与删除间设冷却期、删除粒度按版本非按族；D4 删除后不可重新派发是显式代价、不设代码级逃生；D5 重跑需求走发新版本；D6 P2 仍不做 / P3 仍归 ADR-0033；D7 门禁判据与 DB 引用证明解耦（门禁不连生产库）。**只读实测基线**：136 版本行 / 91 零引用 / 60 零引用且活跃；行数 89,517 中零引用占 **56,394（63.0%）**；零引用版本在 `plan_step` 中出现数为 **0**（不在任何 Plan 定义中） |
| [ADR-0041](./ADR-0041-independent-site-delivery-and-management.md) | 独立站点交付与管理边界 | **Accepted** | — | 多站点 P0 | v1.1（2026-09-14 用户确认）：站点自治、本地入口/账号、轻量导航、统一交付运维及只读总览纳入分期交付；SSO 当前不考虑，跨站点写操作不在范围；功能未实施 |
| [ADR-0042](./ADR-0042-settings-convergence-and-bare-read-boundary.md) | 配置读取收敛——分域 pydantic-settings 与裸读取边界（#737 deferred） | **Accepted** | P1 | M7 | v1.3：D3 加 ADR-0033 §5.4 legacy 例外键的终态出口豁免（[ADR-0051](./ADR-0051-release-unit-and-content-addressing.md) D7 裁决、其 Phase 4 前置）；v1.2：P2 已落地四片（2026-09-18 回填 #2661：控制面 reconciler 旋钮 + 安全与会话域 + 磁盘与日志归档域 + agent 心跳/协调/注册域；回填进度而非 P2 收口，余域按 D2 逐个评估。v1.1：P1 试点完成（2026-09-14；依赖 #1970 + D6 门禁 #1971 + 调度域 21 旋钮 #1977 + agent 租约域 5 旋钮与 reload 钩子 #1984；实作约束 C1/C2 见 §P1））：D1 分域 Settings；D2 迁移判据（≥3 旋钮 / 默认值-类型转换重复 ≥2 处 / 需跨字段校验）；D3 env 名与别名不变（validation_alias）；D4 惰性 get_settings() + Agent reload_settings()；D5 排除注入型/协议键与 scripts 目录；D6 env_inventory 门禁解析 Settings 字段；D7 分阶段（P1 试点 1 控制面域 + 1 agent 域） |
| [ADR-0043](./ADR-0043-abort-grace-subject-alignment.md) | 中止宽限的请求主体同构（Abort Grace Subject Alignment） | **Accepted** | P1 | M7 | v1.1：D3 覆盖判据裁决为「host 时钟**存在**即覆盖」（§9-4，追认 PR #2165 的实现口径）；v1.0：定稿（2026-09-15，#2050 触发、#1928 删除孤儿结构时指路要求先立 ADR；owner 裁决三项全采纳，裁决记录 §9；**v1.0 实施已落地**：2026-09-15 [PR #2165](https://github.com/DUElost/stability-test-platform/pull/2165)（[#2154](https://github.com/DUElost/stability-test-platform/issues/2154) 跟踪，D1–D4/D6 全部接线 + 测试钉子见 §8.1）：D1 请求主体 ≡ 计时主体（run 级 abort 写 run 级 `at`；host 级写 `abort_requested_hosts[host].at`；reaper 按主体取时钟、并存时取更早 deadline）+ D2 自首次请求起算、后续请求不重置（消除 N×GRACE 与「最后写入者赢」）+ D3 主体内 late-claim job 自动纳入（消掉 #2050 残留窗口）+ D4 兼容退化（键缺失回退 run 级、不回填历史，绝不变成「无人回收」）+ D5 显式不做 per-job 时钟 / 不动 UNKNOWN 后续语义 / 不裁 ADR-0019 租约 grace / 不引跨实例协调；#2050 的 `requested_job_ids` 名单语义保留为**正交**的候选面判据 |
| [ADR-0044](./ADR-0044-agent-install-execution-ownership.md) | Agent 安装的执行归属——RunConsole 自持，SAQ 不再持有安装 | **Accepted** | P1 | M7 | v1.1：2026-09-20 D3 补「持久」视界定义（business 默认 90d，ADR-0050 丙案，#2789）；v1.0：2026-09-15 owner 裁决（[#2220](https://github.com/DUElost/stability-test-platform/issues/2220) 触发）：D1 安装以 RunConsole 为唯一 owner（无作业窗口）+ D2 路由只起 console 与写请求审计、删除 `install_agent_task` 与等待职责 + D3 结果审计与 `agent_installed` 由 `on_complete` 落库 + D4 `/install/status` 改 console-only（新增 `console_found`、去 `saq_key`）+ D5 保留 #2225 的「CANCELED ≠ FAILED」并补「记录消失按取消报」+ D6 不加可配窗口、不改 in-process 生命周期（跨重启存活列为终态候选） |
| [ADR-0045](./ADR-0045-risk-level-vocabulary.md) | 风险分级的对外词表——统一到 S/A/B，翻译只留在前端（#2494） | **Accepted** | P2 | M7 | v1.0：2026-09-17 owner 裁决（D1 判定源唯一 = `log_observation` 活链；D2 对外 `S/A/B/UNKNOWN`，删服务端翻译表、`RiskDistribution` 改 `s/a/b/unknown`、趋势 `NONE`→`UNKNOWN`；D3 翻译只在前端徽标表一处；D4 `UNKNOWN` 不可折叠；D5 告警 severity 属另一轴、本 ADR 不动；D6 直接改不双写，前置清点 #2485）；**已落地**（`adf9c24a`：服务端词表 + 前端徽标表），承接单 #2494 |
| [ADR-0046](./ADR-0046-control-plane-checkout-roles.md) | 控制面检出的角色分离——开发工作区 vs 部署源（#1987 / #2386） | Superseded | P1 | M7 | **2026-09-22 由 ADR-0051 D6 接管裁决**（D1 是 / D2 退役显式 / D3 同根不新增 env / D4 部署动作推进 / D5 选 B / D6 审计落 digest）。历史 v1.0：2026-09-17 起草，六个裁决点全开：D1 部署源是否必须与开发工作区**物理**分离；D2「盘上缺失」是否仍等于「已退役」（= #2386 验收第 3 条的阻塞点，本稿取向=退役改显式动作、scan 只报告）；D3 scan / hot-update / 派发补推必须解析到同一个已校验 revision（现状 `_AGENT_SOURCE_DIR` 硬编码在运行代码父目录、无 env 可改指）；D4 部署检出的推进权（#1987 的直接症状是谁都动不了）；D5 是否需要「按 revision 回滚 Agent」——**这是 A/B 的主判据**；D6 审计必须落 revision 且与实际推送字节一致。方案 A 独立只读部署检出 / B 按 revision 归档只读树 / C 现状（**只作已标注的过渡**，出口指向 A 或 B）；本 ADR 不改代码，落地拆单与裁决前的证据缺口见 §7 |
| [ADR-0047](./ADR-0047-db-pool-and-connection-capacity.md) | 控制面 DB 连接池与 PG 上限的容量取向——预算归属与不变量（#703 ② / #2959） | **Accepted** | P1 | M7 | v1.3：2026-09-25 裁决 D3「双池不合并」、D4「不引入 pgbouncer」（均带复评触发器；不改参数与代码）。v1.2：2026-09-25 §4 校准回填——模拟 #3243（池峰 17/40、0×timeout）+ 生产真机 run 556（池峰 async 4 / sync 11、`checkout_failures` 无序列、29s 排空）两层证据维持 `20/20`、`reserve=8`、`2s`；门禁已实装（启动实测待下次重启）、告警已生效（40 rules / 9 groups）；v1.1：2026-09-23 owner 裁决 D1/D2/D5/D6——D1「`n_instances × n_engines × (pool_size + max_overflow) ≤ max_connections − superuser_reserved − reserved_connections − 非应用/运维预留`」成为**启动期硬门禁**（预算不成立即拒启，校验器读 PG 现算不硬编码）；D2 `pool_timeout=2s`，槽耗尽（53300）与池排队超时统一对外 **503 + `Retry-After`**（`DB_OVERLOADED`，不再 500）；D5 告警落事件侧立即触发（slots_exhausted → critical、无 for）；D6 `n_instances` 现在进公式。首轮值 sync/async 各 **20+20=40**（合计 80，97−80=17 余量）、`reserve=8`（**v1.2 已校准确认维持**）。证据：R523（490 RUNNING 同时终态 → 1644 次 /complete、1401 条 53300、池峰 async 86 + sync 12 ≥ 97）与同日 09:52/r518 同型、对照 r522 自然终态波（池峰 ≤4、零 53300）。D3 双池不合并（v1.3 裁决；终态出口 = 同步调用点自然归零）；D4 不引入 pgbouncer（v1.3 裁决）（可见性与会话级 advisory lock 身份），复评条件=ADR-0027 多实例或池指标语义改写同 PR |
| [ADR-0048](./ADR-0048-execution-status-semantics-v2.md) | 执行状态语义 v2——移除 run 级测试通过率判定（#2734 / v1.1 #2982） | **Accepted** | P1 | M7 | v1.1：2026-09-20 owner 重议恢复 PARTIAL_SUCCESS 真实产出（failed_only>0 即黄、无阈值线、设备失败永不判红不断链）与通过率显示（前端派生 completed/total，列表页通过率列 + Dashboard 通过率趋势图双口径回归；后端 pass_rate 字段/阈值列仍废止不回灌）；v1.0：2026-09-18 owner 裁决（`failure_threshold` 阈值判红轴与展示链整体移除，#783 abort→FAILED 保留）；supersede ADR-0022 D8；落实 #815 |
| [ADR-0049](./ADR-0049-audit-log-retention-layering.md) | audit_logs 分层保留期与裁剪（#2694 拆单 / #2741） | Accepted | P2 | M7 | v1.0：2026-09-19 owner 裁决四问全采推荐项——D1 分层 180/90/30（security/business/session，`token_issued` 归 security）、D2 business 为 NOT IN 默认桶+安全 action 登记义务、D3 会话类同表不折叠、D4 单例批删作业（0=停用）+不复用锁序机器、D5 汇总审计自免环；`terminal_payload_conflict` 爆发行不例外；实现随本 ADR 同 PR 落地（#2741） |
| [ADR-0050](./ADR-0050-install-evidence-retention-alignment.md) | audit_logs 保留期与 ADR-0044 D3 安装证据的对齐（#2789） | **Accepted** | P3 | M7 | v1.0：2026-09-20 owner 裁决采丙「明示接受 90d 视界」（零迁移；D3 持久=审计保留视界内，business 默认 90d env 可调；布尔事实走 host.extra 不丢）；甲（入 security 层，180d 仍有限）与乙（专表，超前建设）未采纳；v0.1 决策材料 PR #2816 |
| [ADR-0051](./ADR-0051-release-unit-and-content-addressing.md) | 发布单元与内容寻址——不可变性从源码目录移到包（#735 / #3075 / #1987 / #2386） | **Accepted** | P1 | M7 | v1.8：2026-09-26 **未完结**；落地状态刷新——D6 闭合、D7 三片合入且包已发布（待换 rev 激活）、D8 退役 check_new_script_family；v1.7 D7 补脚本→工具绑定条款（脚本包 `requires_tools` 声明、引擎核验注入、不新增主机 env 键）。现站 48/48 包模式、在位缺口 0；Phase 0/2a/2b/3 已落地，Phase 1/4/5 部分落地；**D6 闭合**——生产 env 真身落站点 `stp-releases/env.backend`，rev 根树内 symlink，运行时不再触开发检出。#3262 已收口 manifest retired 数据策略；仍待新站 `default_params`、flashtool/aimonkey 与控制面 dedup 工具包化、legacy env/回退和治理减法。完整证据与历史修订见正文。 |
| [ADR-0052](./ADR-0052-terminal-fact-parent-aggregation-decoupling.md) | 终态事实与父 Run 聚合解耦——Job 事务不写父级热行（#2959 / #3244） | **Accepted** | P1 | M7 | v1.1：2026-09-25 D6 改判**不采纳**（复议触发器保留）；§7 定值——无固定攒批窗口 + 聚合者排空循环、单批初值 500、pending 消费即删、表名 `plan_run_pending_aggregation`；未实施。v1.0：2026-09-25 裁决（owner 授权 Claude）：**D1–D5 接受并开始实现，D6 延后**（带复议触发器）；§5 拆为决策门槛（plan_run 556 真机 `/complete` p99 2.467s 已满足）与实施验收门槛（原 6 条，② 基线改取 2.467s）；ADR-0026 §6 同步标注被替代两处。v0.1：2026-09-24 起草（owner 口径：**先 ADR 后实现**）——D1 `/complete` 等终态事务不再锁 `plan_run`、不写 `plan_run`/`plan_run_host` 计数、不写 `acknowledged_job_ids`；D2 durable pending 标记（insert-only）+ 提交后唤醒（Redis 仅传输，`counter_reconciler` 只作修复路径）；D3 按 `plan_run_id` 合并聚合、单 Run 单聚合者、读 Job 事实重算、at-least-once 幂等；D4 chain/dedup/通知/报告在父终态提交后且具重复执行保护（含「聚合已提交、投递失败」恢复）；D5 停写 ACK、保历史读兼容；D6 post_completion 独立队列/Worker（**独立 Decision，允许单独延后**）。**替代 ADR-0026 §6 的两处**（终态事务内自增计数 / 每 Job 持父行锁），保留单一 terminalization 入口与对账 sweep 自愈；不动 ADR-0048 D1。Proposed→Accepted 门槛 = 部署窗真机复跑 6 条（见其 §5） |
| [ADR-0053](./ADR-0053-center-storage-event-dedup.md) | 中心存储事件去重——内容对象、事件引用与 baseline 复用（#3230 / #3233） | **Accepted** | P1 | M7 | v0.3：2026-09-25 裁决 D7——提单单位是「故障发生」：同一发生跨 run 不重复提单、baseline 首见默认不提、同签名新发生照常提、身份不可证明时 fail-open，随 Phase C 落地。v0.2：2026-09-25 owner 接受 D1–D6 与 Phase A→B→C→D。文件 CAS + manifest + DLE 引用；baseline 复用依赖强身份与 scan 输入完整；派生物隔离，发布/GC/可观测为首版条件。Phase A–D 均未实施 |
| [ADR-0054](./ADR-0054-agent-control-plane-shared-contracts.md) | Agent 与控制面的共享契约包——`backend/agent/contracts/`（#3298 / #738） | **Accepted** | P2 | M7 | v1.0：2026-09-25 裁决（owner 授权 Claude）：D1–D6 全部接受，D3 补「`backend/agent/__init__.py` 保持轻量」并纳入 C6；v0.1：2026-09-25 起草（PR #3303）——D1 共享定义唯一归属 `backend/agent/contracts/`，随 `agent-code` 下发（不改 ADR-0040 输入集、不新增发布单元）；D2 只收契约（stdlib + 登记的可选第三方，无 import 期副作用）；D3 agent 侧相对导入、控制面绝对导入、`__init__` 为空；D4 C3 通配放行 `contracts.**` + C6 纯度用 AST 测试（import-linter 祖先 forbidden 实测静默不生效）；D5 每次搬迁删副本/兜底/parity 测试/C3 行/白名单条目；D6 契约不靠 `__file__` 深度定位工件。落地 4 步：pipeline_validator+legacy_aee → aee/watcher 三模块 → artifact_digest 算法 → kernel_usb_faults/state_migration 逐个判断；**已落地**（2026-09-26：四步 PR #3358/#3364/#3374/#3390/#3392 合入，C3 基线清零、`_SHARED_ALLOWLIST` 仅剩 metrics，#3298 关闭） |
| [ADR-0055](./ADR-0055-schedule-device-selector.md) | 定时回归链的设备选择——固定名单与条件现算两种模式（#2909） | **Accepted** | P2 | M7 | v1.0：2026-09-26 owner 裁决——存量 schedule 迁为 `fixed`，新增 `selector` 模式在链头触发时按条件 + 固定健康门现算；实施未开始 |
| [ADR-0056](./ADR-0056-terminal-fact-layer.md) | 终态事实层——签名行、DLE 摘要、设备健康时间线与 MTBF 分母（#3326） | **Accepted** | P1 | M7 | v1.0：2026-09-26 裁决（owner 授权 Claude）——F1–F5 采起草取向：分表、适配器归一签名、租约时长作 MTBF 分母、存量回填、缺事实行跳过删除；实施未开始 |
| [ADR-0057](./ADR-0057-device-retirement-semantics.md) | 设备退役语义（#2962；ADR-0038 D7 所称的独立提案） | **Accepted** | P2 | M7 | v1.0：2026-09-26 裁决（owner 授权 Claude）——E1–E5 采起草取向：退役设备再上报保持退役并单次告警、有活跃 Job / 租约时 409、存量人工确认批量退役、30 天建议退役、告警排除；实施未开始 |

## 里程碑看板（由主表「目标里程碑」列派生）

> **维护约定（#2989）**：本表是主清单「目标里程碑」列的**派生视图**——主表标了 `M7`
> 的 ADR **必须**出现在下行；新增 ADR 写主表时同步补本行。标题不再写
> 「Proposed」——板上含 Accepted / Proposed 混态，真正未决项以状态列
> `Proposed` 为准。**Proposed（未决）：无**（ADR-0056、ADR-0057 均于 2026-09-26 起草并裁决转 Accepted）。**ADR 内未裁子项：无**（2026-09-25 一轮收口：ADR-0038 D9、ADR-0053 D7、ADR-0047 D3/D4、ADR-0052 D6/§7、ADR-0031-A §7、ADR-0030 开放问题 3、ADR-0028 PRUNE_LOCAL、ADR-0012 第 2–3 层、ADR-0023 D2–D8 均已裁决；带复议触发器的否决不计为未裁）。

| 里程碑 | 目标日期 | 包含 ADR |
|---|---|---|
| M1 | 2026-03-15 | ADR-0008, ADR-0009 |
| M2 | 2026-04-15 | ADR-0011（已 Accepted：第一层指标落地）；ADR-0010 已由 ADR-0020 取代；ADR-0013/0014/0016/0018 已落地 |
| M3 | 2026-05-15 | ADR-0012（第 2–3 层：2026-09-25 裁决，第 2 层收敛待实施、第 3 层不采纳）, ADR-0019, ADR-0020, ADR-0021–0023 |
| M4 | 2026-06+ | ADR-0025（方案 C Sprint 1–4）；PRD/设计/验收见 [`docs/DOC-MAP.md`](../DOC-MAP.md) |
| M5 | 2026-07 | ADR-0026 P0–P2（规模化执行正确性 + 控制面减负） |
| M6 | 待定 | ADR-0027（控制面水平扩展；重启条件见 ADR-0025 D1） |
| M7 | 进行中 | **Superseded**（2026-09-22 由 ADR-0051 取代）：ADR-0039、ADR-0046。<br>**Accepted**：ADR-0029（M1–M4 已落地）、0030、0031、0032、0033（v1.13；§5.4 条件 4 多站点已触发；部分落地；后续切片并入 ADR-0051 Phase 4）、0034、0036、0037、0038（v0.3：D9 2026-09-25 裁决生效，#3159 未实施）、0040、0042、0043（实施已落地）、0044、0045（词表已落地 `adf9c24a`）、**ADR-0047**（v1.3：容量不变量进启动门禁 + 503 过载语义；§4 校准回填维持首轮值 `20/20`+`reserve=8`；D3 不合并 / D4 不引入 pgbouncer 已裁）、0048、0049、0050、**0051**（v1.5；现站包执行已验收，独立站点全发布单元包化仍未完结）、**0052**（v1.1：终态事实与父 Run 聚合解耦；D1–D5 接受，D6 不采纳（带复议触发器），§7 已定值；未实施）、**0053**（v0.3：内容对象与事件引用分离；2026-09-25 owner 接受；Phase A–D 均未实施；D7 按发生去重提单已裁）、**0054**（v1.0：Agent 与控制面共享契约包 `backend/agent/contracts/`；已落地——四步 #3358/#3364/#3374/#3390/#3392 合入，C3 基线清零，#3298 已关）、0055（v1.0：2026-09-26 裁决，实施未开始）、0056（v1.0：2026-09-26 裁决，实施未开始）、0057（v1.0：2026-09-26 裁决，实施未开始）。 |

## 维护约定

- 每次关键架构变化必须新增或更新 ADR，并在 MR/PR 中引用。
- 若 ADR 被替代，旧 ADR 不删除，仅将状态改为 `Superseded` 并指向新 ADR。
- AI 生成方案或修改代码时，优先检索本目录并遵循 `Accepted` ADR。
- 里程碑看板与主表「目标里程碑」列必须同批更新（#2989）；漏板 = 未决项隐身。
