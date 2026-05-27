# 稳定性测试平台 — 项目健康度与剩余工作（整合说明）

> 生成日期：2026-05-27  
> 整合来源：[`main-chain-fragility-analysis-2026-05-23.md`](./main-chain-fragility-analysis-2026-05-23.md)、[`production-readiness-assessment-2026-05-23.md`](./production-readiness-assessment-2026-05-23.md)、[`main-chain-remaining-work-implementation-plan-2026-05-25.md`](./main-chain-remaining-work-implementation-plan-2026-05-25.md)、[`production-minimum-deployment-checklist.md`](./production-minimum-deployment-checklist.md)  
> 代码基线：主链路加固提交系列 `262836e` → `332e179` → `f0ec89d` → `256bb6e`（README 对齐）

---

## 1. 背景

2026-05-23 起对「Plan 创建 → 派发门禁 → Agent 执行 → PlanRun 聚合 → UI 实时刷新」主链路做了专项脆弱性分析（见 [主链路脆弱性分析](./main-chain-fragility-analysis-2026-05-23.md)）。分析结论：用户「感官脆弱」主要来自派发异步无反馈、生产 SocketIO 不可达、Job 长时间 RUNNING/UNKNOWN、Plan 链静默失败、设备 BUSY 长 grace 等。

随后在约一周内以多批提交收口 P0/P1 代码与文档，核心 commit 脉络如下：

| Commit | 主题 |
|--------|------|
| `262836e` | 主链路脆弱性收口：dispatch gate 统一（SCHEDULE/CHAIN 走 sync gate）、precheck SocketIO 推送、SAQ enqueue 显式失败、Plan 链 `next_plan_triggered` 回滚、设备校验、主链集成测试 `test_main_chain_happy_path.py` |
| `332e179` | P1 可观测：patrol recovery（`JOB_NOT_RUNNING` → recovery/sync）、precheck reaper 增强、DispatchGateCard / DeviceMatrix / Hosts 页 UI |
| `f0ec89d` | 安全与运行时：注册门控、`/metrics` 鉴权、patrol 超时分级 env、派发 sync 可配置重试、outbox 积压指标、AlertManager 规则草案 |
| `256bb6e` | 文档：README 与 Plan/PlanRun 架构、ADR-0024、生产部署要点对齐 |

**当前状态（2026-05-27）**：主链路 **P0 代码/CI/部署文档已落地**；后端全量 pytest 约 **718 passed**（PostgreSQL testcontainers）；前端 Vitest 与 `main-chain-integration-smoke` CI job 已纳入主链回归。**无法仅凭仓库确认** 的项：预发布真实设备 smoke 签字表、生产环境 Nginx/HTTPS 一次性核对、AlertManager 实际挂载。

---

## 2. 已完成的加固摘要

- **派发门禁（dispatch gate）**：MANUAL 经 SAQ + precheck；CHAIN/SCHEDULE 统一 `dispatch_plan_sync` inline gate；phase 变更 SocketIO `precheck_update`；失败可 `retry-dispatch`；`DISPATCH_SYNC_MAX_ATTEMPTS` 可配
- **SAQ / Redis**：`enqueue_sync(..., required=True)` → 503；lifespan Redis PING + in-process SAQ 启动失败即退出；`STP_SKIP_INFRA_CHECK` 仅非生产逃生
- **PlanRun 聚合与 Plan 链**：行锁 + terminal guard；`next_plan_triggered` 失败回滚 + `chain_dispatch_failed`；abort 留痕；链式 E2E 测试 `test_plan_chain_e2e.py`
- **Job 超时**：`job_timeout_config.py` 集中默认；生产 patrol RUNNING 心跳默认 **300s**（init 仍 900s）
- **UI 可观测（P1）**：DispatchGateCard stale banner；devices 端点 `grace_remaining_seconds` / `busy_reason`；矩阵认领 SLA / grace 倒计时；PlanRun 级报告导出 API + 前端对接
- **Agent 边缘**：subprocess 进程组隔离；patrol-heartbeat `JOB_NOT_RUNNING` → `patrol_recovery`；terminal/log_signal outbox 积压经 heartbeat 上报 + Prometheus Gauge；step_trace_cache 防膨胀
- **安全（ADR-0024）**：HttpOnly Cookie + CSRF + refresh 黑名单；生产 Cookie guard；默认关闭公开注册（`STP_ALLOW_REGISTER`）；`/metrics` 可选 Bearer/Agent-Secret
- **测试与 CI**：`test_main_chain_happy_path.py`、SAQ 503、PENDING timeout + SocketIO、Plan 链 E2E；`ci.yml` → `main-chain-integration-smoke`；可选 `smoke-nightly.yml`
- **可观测草案**：dispatch gate / precheck / patrol / CSRF / outbox 等指标；`deploy/prometheus/alerts-stability-platform.yml`（待运维部署生效）
- **文档**：[`production-minimum-deployment-checklist.md`](./production-minimum-deployment-checklist.md) §5 smoke、[`preprod-drill-runbook.md`](./preprod-drill-runbook.md) §4.0、README 生产要点

---

## 3. 当前薄弱领域

基于 2026-05-25 实施计划复查与 2026-05-27 现状，剩余风险集中在五类（非互斥）：

### 3.1 测试与验收

| 现状 | 风险 |
|------|------|
| CI 以 mock/SQLite/PG 集成为主；真实设备 smoke **不在默认 PR** | 脚本/NFS/ADB 环境差异仅在生产暴露 |
| T-B8（UNKNOWN→grace→FAILED PlanRun 级）、T-B9（patrol_stall + Agent 集成）**进行中** | 调度三阶段串联断言不足 |
| 生产同源 SocketIO E2E（Nginx 443）**无自动化** | 反代配置错误时仅靠 10s 轮询兜底 |
| pipeline init→patrol→teardown **真实 subprocess** 集成弱 | 脚本回归依赖人工或 Agent 单机 |

### 3.2 生产运维与部署闭环

| 现状 | 风险 |
|------|------|
| AlertManager 规则文件已有，**部署与路由未验证** | 聚合失败、CSRF、心跳超时等无值班闭环 |
| T-A6/T-A7（人工 smoke、checklist 逐项勾选）依赖运维执行 | 仓库绿 ≠ 环境已验收 |
| PostgreSQL 备份 / Loki **仅文档提及** | 灾备与集中排障未产品化 |
| 单实例 backend + 进程内 APScheduler/RateLimit **未架构改造** | 误扩实例导致重复调度 |

### 3.3 前端体验与权限边界

| 现状 | 风险 |
|------|------|
| PlanRun 导出已对接后端；批量 zip / 多 Job 报告仍为增强项 | 测试经理大批量导出需绕行 |
| **无路由级 admin 门控**（users/notifications/audit 等） | 内网误操作或越权浏览 |
| 读 API 鉴权已部分落地；**公网暴露仍不足** | 聚合端点、report、stats 等需网络隔离或全量鉴权 |
| Host 页 claim lease 阻塞提示仍弱（metric 已有） | 运维需查 Prometheus 才知设备被占 |

### 3.4 运行时策略与 Agent 边缘

| 现状 | 风险 |
|------|------|
| T-B1 prepare 脚本版本锁、T-B2 verify 瞬态/终态分类 **进行中** | 脚本失活窗口、网络抖动仍可能整 Run FAILED |
| `STP_WATCHER_ENABLED` 默认 **false** | 稳定性测试核心价值（log_signal）未在生产默认开启 |
| Watcher CATCHUP、灰度 runbook（T-C1）未收口 | 开启后 CPU/IO 影响未评估 |
| 真实 E2E 与 nightly smoke 依赖密钥/设备池 | 无人值守回归覆盖有限 |

### 3.5 架构扩展性（P2）

| 项 | 说明 |
|----|------|
| SocketIO Redis adapter | 多 backend 实例 room 分裂 |
| 外置 APScheduler / Gunicorn 多 worker | 与当前单 uvicorn 设计冲突 |
| 分布式 rate limit | 进程内 300 req/min/IP |
| MapReduce / Settings 占位页 | 导航噪音或期望落差 |
| 细粒度 RBAC | 多人协作长期项 |

---

## 4. 仍待办清单

> **运维（ops）** = 环境、部署、签字、告警路由、备份演练，通常不改应用代码。  
> **代码（code）** = 仓库内实现、测试、文档与模板同步。

### P0 — 上线阻塞 / 必须人工确认

| ID | 任务 | 类型 | 状态 |
|----|------|------|------|
| P0-O1 | 生产执行 [`production-minimum-deployment-checklist.md`](./production-minimum-deployment-checklist.md) 全项（Nginx `/socket.io/`、`VITE_API_BASE_URL=`、HTTPS、Cookie env、Redis、单实例） | ops | ⬜ 须签字 |
| P0-O2 | `alembic upgrade head`（含 `revoked_refresh_token`） | ops | 模板已有 |
| P0-O3 | 预发布真实设备 smoke：[`preprod-drill-runbook.md`](./preprod-drill-runbook.md) §4.0 + `seed_and_smoke.py` 签字表归档 | ops | CI 不代替 |
| P0-O4 | `JWT_SECRET_KEY` / `AGENT_SECRET` / `CORS_ORIGINS` 生产强配置 | ops | guard 已有 |
| P0-C1 | 公网：读 API 全量鉴权或 Nginx IP 白名单 | code+ops | 部分（plan-runs 等已加固） |
| P0-C2 | 公网：关闭或门控 `POST /auth/register` | code | ✅ 默认门控 env |

### P1 — 强烈建议（上线后 2–6 周）

| ID | 任务 | 类型 | 状态 |
|----|------|------|------|
| P1-O1 | 部署 AlertManager + 加载 `deploy/prometheus/alerts-stability-platform.yml` + 值班路由 | ops | 草案已有 |
| P1-O2 | Grafana dashboard 导入 + `/metrics` scrape | ops | 模板已有 |
| P1-O3 | PostgreSQL 自动备份 + 恢复演练 | ops | ⬜ |
| P1-O4 | 预发布 30min 观测窗口（ADR-0011 第三层） | ops | runbook 有 |
| P1-O5 | **2026-06-21** 收紧 refresh 无 jti grace → 401 | code | 日历项 ADR-0024 |
| P1-C1 | 前端 admin 路由门控 | code | ⬜ |
| P1-C2 | T-B1 prepare 脚本 sha 快照锁 | code | 进行中 |
| P1-C3 | T-B2 verify transient vs terminal | code | 进行中 |
| P1-C4 | T-B8 PlanRun 级 UNKNOWN→grace→FAILED 集成测试 | code | 进行中 |
| P1-C5 | T-B9 patrol_stall + manual_retry Agent 集成 | code | 进行中 |
| P1-C6 | Host 页 claim lease 阻塞提示（T-C5） | code | metric ✅ UI ⬜ |
| P1-C7 | Vitest：login 401 不循环（若仍有缺口） | code | 多数已修复 |

### P2 — 优化与技术债

| ID | 任务 | 类型 | 说明 |
|----|------|------|------|
| P2-O1 | Loki 或等价集中日志 | ops | 替代纯本地 log_writer |
| P2-C1 | SocketIO Redis adapter + 多实例 backend | code | L 工作量 |
| P2-C2 | 生产同源 SocketIO Playwright/Cypress E2E | code+ops | 需 staging |
| P2-C3 | Watcher 生产评估与灰度（T-C1） | code+ops | 默认 off |
| P2-C4 | pipeline 真实 subprocess 集成（T-C3） | code | Agent CI 增量 |
| P2-C5 | 移除 legacy `/ws/`、MapReduce/Settings 占位 | code | S |
| P2-C6 | 分布式 rate limit、外置 scheduler | code | 架构级 |

---

## 5. 建议优先级

在资源有限时，建议按下列顺序推进（**先证明能稳定跑，再补值班与权限，最后做扩展**）：

```mermaid
flowchart LR
  A[smoke / CI 回归] --> B[AlertManager 部署]
  B --> C[admin 路由门控]
  C --> D[导出与报告增强]
  D --> E[Watcher 灰度]
```

| 优先级 | 动作 | 理由 |
|--------|------|------|
| **1** | **smoke + CI**：保持 `main-chain-integration-smoke` 绿；完成 T-A6/T-A7 人工签字；补 T-B8/T-B9 集成 | 唯一证明「真环境能跑通」的路径 |
| **2** | **AlertManager 部署**：挂载已有规则文件，验证 `stability_plan_run_aggregation_failed_total`、CSRF、dispatch 类告警 | metric 已埋点，缺运维闭环则故障仍靠人工刷库 |
| **3** | **admin 路由门控**（P1-C1） | 内网多人协作时降低误配置与越权浏览 |
| **4** | **导出增强**（批量 zip、PlanRun 级 PDF 等） | T-B5 已满足单 Run markdown/json；属体验增强非阻断 |
| **5** | **Watcher 灰度**（T-C1） | 稳定性测试长期价值高，但默认 off；须在 smoke 稳定后评估 NFS/IO |

**明确后置**：SocketIO Redis adapter、多 worker Gunicorn、Loki 全量接入——与「单实例 + 主链跑通」假设冲突，宜在水平扩展需求明确后再立项。

---

## 6. GitHub Issue 是否有必要？

### 6.1 何时适合开 Issue

- **跨角色、跨周跟踪**：运维部署（AlertManager、checklist 签字、备份）与开发任务（T-B1/B2/B8/B9）由不同人负责，需要指派、截止日与讨论串。
- **可审计的发布门禁**：发布经理需要「P0-O3 smoke 签字」类 ticket 状态，而非仅在 Markdown 里勾选。
- **外部协作或开源贡献者**：Issue 模板可降低重复提问。

### 6.2 何时文档即可（不必强行开 Issue）

- **单团队、同一仓库迭代**：当前 [`main-chain-remaining-work-implementation-plan-2026-05-25.md`](./main-chain-remaining-work-implementation-plan-2026-05-25.md) 已含 U1–U18 任务卡与 Phase A/B/C；本文 §4 表格与之互补。
- **纯运维一次性核对**：checklist / runbook 勾选 + 内部 wiki 签字表往往比 Issue 更贴流程。
- **已完成或 CI 已覆盖项**：避免 Issue 与代码状态长期漂移。

### 6.3 建议结论（2026-05-27）

| 场景 | 建议 |
|------|------|
| 2–5 人小团队、主开发兼运维 | **不必**为每项剩余工作建 Issue；以本文 + 实施计划 + checklist 为单一事实源即可 |
| 有专职运维/SRE 或需发布审批留痕 | **建议**仅为 **ops 类 P0/P1** 与 **进行中 code 任务（T-B1/B2/B8/B9）** 开少量 Issue（约 6–10 个），避免与已签收 T-A* / T-B3–B7 重复 |
| 长期 P2 架构债 | 可合并为 1 个 epic + 子任务，或留在 ADR/文档直至立项 |

**Epic 跟踪**：仓库已用 GitHub Issue 承载下列跨周工作（标签：`enhancement` / `documentation`；运维向可自建 `ops`）。细项仍以本文 §4 与 [实施计划](./main-chain-remaining-work-implementation-plan-2026-05-25.md) 为单一事实源，Issue 仅作指派与发布门禁留痕。

| Epic 主题 | 对应 §4 ID |
|-----------|------------|
| 生产部署与验收 | P0-O1～O4、P0-O3 smoke |
| 测试与 CI 加固 | T-B8/T-B9、smoke-nightly |
| 可观测性部署 | P1-O1～O3、P1-O4 |
| P1 体验与权限 | P1-C1、导出增强、env 模板 |
| P2 技术债（可选） | P2-C1～C3、2026-06-21 grace |

---

## 7. 相关文档索引

| 文档 | 用途 |
|------|------|
| [main-chain-fragility-analysis-2026-05-23.md](./main-chain-fragility-analysis-2026-05-23.md) | 脆弱点清单、P0/P1 原文、测试缺口 |
| [main-chain-remaining-work-implementation-plan-2026-05-25.md](./main-chain-remaining-work-implementation-plan-2026-05-25.md) | U1–U18 任务卡、Phase A/B/C 排期 |
| [production-readiness-assessment-2026-05-23.md](./production-readiness-assessment-2026-05-23.md) | 安全/运维 P0 全表、鉴权缺口 |
| [production-minimum-deployment-checklist.md](./production-minimum-deployment-checklist.md) | 生产最小部署与 smoke |
| [preprod-drill-runbook.md](./preprod-drill-runbook.md) | 预发布逐条验收 |

---

*维护：主链路加固与发布节奏变更时同步更新 §2–§5；重大 commit 系列请在 §1 表增补。*
