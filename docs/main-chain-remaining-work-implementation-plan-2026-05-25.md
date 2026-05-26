# 主链路剩余工作实施计划

> 生成日期：2026-05-25  
> 依据：`docs/main-chain-fragility-analysis-2026-05-23.md`（§2 脆弱点 / §4 P1 / §5 测试缺口）  
> 复查结论：**P0 5 项代码/CI/部署文档已全部落地**（含 smoke/CI；预发布真实设备 smoke 执行记录须运维侧留存，无法仅凭仓库确认）。P1 与 §2：**已有多项完成，另有多项仅部分完成**（见 §1.4）；本文覆盖未完成项 **U1–U18**  
> 关联：`docs/preprod-drill-runbook.md`、`docs/production-minimum-deployment-checklist.md`、`.github/workflows/ci.yml`

---

## 1. 目标与范围

### 1.1 目标

在 P0 加固（SAQ enqueue 显式失败、precheck SocketIO、`next_plan_triggered` 回滚、生产 SocketIO 同源、smoke/CI）已落地的前提下，收口主链路**体验盲区**、**可观测缺口**与**测试/E2E 债务**，使用户从「Plan 执行 → 派发门禁 → Job 运行 → PlanRun 终态」全路径可感知、可诊断、可回归。

> P0 5 项的代码/CI/部署文档已全部落地；但预发布真实设备 smoke 的实际执行记录无法仅凭仓库确认。P1 与 §2 建议中，已有若干项完成，另有若干项仅部分完成；仍待收口的重点包括生产 patrol 默认值、grace/BUSY 显示、outbox 监控、生产 SocketIO 真实 E2E 和部分集成断言。

### 1.2 范围内

| 维度 | 说明 |
|------|------|
| 主链路体验 | grace/BUSY 秒级倒计时与来源、PENDING 认领 SLA 可见文案、派发 stale 感知、导出报告 |
| 主链路可靠性 | 启动期 Redis/SAQ **仍需收口**（显式 PING、saq_ready、文档统一）、verify 瞬态/终态、sync 重试、prepare 脚本版本一致性 |
| 主链路可观测 | outbox 积压、precheck/sync 指标、聚合失败 AlertManager（`stability_plan_run_aggregation_failed_total` 已有） |
| 测试与 smoke | 集成/E2E 补全、runbook 人工演练、生产部署核对 |
| 文档/CI | env 模板、部署清单、smoke runbook 增量 |

### 1.3 范围外

- AlertManager / Loki / 备份自动化（除非 metric 已暴露且需写 runbook 观测项）
- 读 API 全量鉴权、HTTPS 模板、register 关闭等 **production-readiness** 安全项（并行但不纳入本计划排期）
- Watcher 功能开发（U9 仅做生产评估与灰度决策，不含大规模 feature）

### 1.4 当前基线（2026-05-25）

**P1 完成度（严格统计，与 fragility-analysis §4 对齐）**：

| 类别 | 项 |
|------|-----|
| 明确完成（3） | CHAIN/SCHEDULE inline gate；WiFi `AllocationError` hard fail；`JOB_NOT_RUNNING` → recovery/sync |
| 部分完成（3） | timeout 分级框架（`job_timeout_config.py`，生产 patrol 默认仍 900s）；UNKNOWN/PENDING tooltip（`DeviceMatrixCard.tsx`）；`stability_claim_lease_failed_total` |
| 部分完成（metric） | 聚合失败：`recycler.py` 已写 audit + `stability_plan_run_aggregation_failed_total`；无 `aggregator_sync` 命名 metric、AlertManager 告警仍缺 |

已部分落地、本计划**增量**收口：

- `backend/core/job_timeout_config.py`：分级 timeout 框架（生产 patrol 默认仍 900s）
- `precheck_update` SocketIO + `PlanRunDetailPage` gate stale banner（90s）
- `DeviceMatrixCard`：PENDING 120s SLA tooltip、UNKNOWN/grace 提示初版；**仍缺** API 字段 `grace_remaining_seconds` / `busy_reason` 及矩阵可见倒计时
- `POST /plan-runs/{id}/retry-dispatch` + `sync_attempts` cap=1
- `test_main_chain_happy_path.py` + CI `main-chain-integration-smoke` + `smoke-nightly.yml`
- PlanRun「导出」当前为前端 `getSummary` JSON 下载，**未**对接 `/runs/*/report/export`
- `STP_ENABLE_INPROCESS_SAQ=1` 时 `start_saq_worker()` 失败已导致 lifespan 失败（`main.py:119`、`saq_worker.py` connect）；**仍缺**显式 Redis PING、`saq_ready` 健康暴露、checklist 统一表述

---

## 2. 优先级分阶段

### Phase A — 上线前强烈建议（1–2 周）

聚焦：**依赖健康**、**生产 timeout 决策**、**用户可感知的等待/占用状态**、**最小 smoke 闭环**。

> **Phase A 签收进度（2026-05-26）**：T-A1～T-A4 已签收；合并前全量回归 **2026-05-26**：`pytest backend/tests/` → **718 passed / 15 skipped / 0 failed / 0 errors**（Python 3.11 + testcontainers PostgreSQL 16，约 46 min）。

| 任务 ID | 对应 U | 标题 |
|---------|--------|------|
| T-A1 | U5 | 启动期 Redis/SAQ 依赖收口 |
| T-A2 | U1 | 生产 patrol 超时默认值与 env 文档 |
| T-A3 | U4 | `PRECHECK_QUEUE_STALE_SECONDS` 调优与可观测 |
| T-A4 | U2 | Grace 倒计时与 BUSY 来源 UI |
| T-A5 | U11 | SAQ 不可用 → API 503 集成测试 |
| T-A6 | U17 | 预发布人工 smoke 演练（真实设备） |
| T-A7 | U18 | 生产最小部署核对清单执行 |

### Phase B — 上线后 2–4 周

聚焦：**派发/脚本一致性**、**瞬态错误恢复**、**导出与后端聚合**、**scheduler 链路与 UI 测试补全**。

> **Phase B 签收进度（2026-05-26）**：T-B3 / T-B5 / T-B6 / T-B7 / T-B10 / T-B4 已签收；T-B1 / T-B2 / T-B8 / T-B9 **进行中**（见各任务卡片 blocker）。

| 任务 ID | 对应 U | 标题 |
|---------|--------|------|
| T-B1 | U3 | Prepare 锁脚本版本 + complete 失败 SocketIO |
| T-B2 | U6 | Verify transient vs terminal 分类 |
| T-B3 | U7 | Sync 可配置重试与派发重试 UX |
| T-B4 | U8 | Agent outbox 积压监控 |
| T-B5 | U10 | PlanRun 导出报告对接后端 |
| T-B6 | U12 | precheck `agent_offline` UI 测试补全 |
| T-B7 | U13 | PENDING 120s timeout + SocketIO 集成测试 |
| T-B8 | U14 | UNKNOWN → grace → FAILED PlanRun 级断言 |
| T-B9 | U15 | patrol_stall + manual_retry Agent 侧集成 |
| T-B10 | U16 | Plan 链 dispatch 全链路 E2E |

### Phase C — 增强 / 技术债

聚焦：**Watcher 灰度**、**生产 SocketIO E2E**、**pipeline 真实脚本**、**P1 杂项 metric**。

| 任务 ID | 对应 U | 标题 |
|---------|--------|------|
| T-C1 | U9 | Watcher 生产评估与灰度 runbook |
| T-C2 | — | 生产同源 SocketIO E2E（Nginx 443 push） |
| T-C3 | — | pipeline init→patrol→teardown 真实 subprocess 集成 |
| T-C4 | — | 聚合失败 AlertManager 规则（metric 已有） |
| T-C5 | — | claim lease Host 页阻塞提示（metric 已有） |

---

## 3. 任务卡片（U1–U18）

### T-A1 · 启动期 Redis/SAQ 依赖收口

| 字段 | 内容 |
|------|------|
| **ID / U** | T-A1 / **U5** |
| **状态** | **已签收** |
| **依赖** | 无（P0 enqueue `required=True` 已存在） |
| **现状** | `STP_ENABLE_INPROCESS_SAQ=1` 且非 `TESTING` 时，`start_saq_worker()` 连接/启动失败已导致 lifespan 失败（`main.py:119`、`saq_worker.py:88-90`） |
| **改动面** | backend（`main.py` lifespan、`saq_worker.py`）、docs、deploy env example |
| **步骤** | ① lifespan 启动时对 `REDIS_URL` 执行显式 `PING`（或等效连接探测），失败 `RuntimeError`（**增量**；SAQ worker 失败路径已有） ② 日志明确区分「Redis 不可达」vs「SAQ worker 未就绪」 ③ 更新 `production-minimum-deployment-checklist.md` §3.3：Redis/SAQ 为硬依赖，措辞与代码一致 ④ 可选：`/health` 或专用端点暴露 `saq_ready` |
| **验收标准** | 本地停 Redis 后 backend 无法启动；`STP_ENABLE_INPROCESS_SAQ=1` 且 SAQ 不可用时无法启动；pytest lifespan mock 不受影响 |
| **工作量** | S |
| **风险** | 中 — 误伤开发环境；需 `TESTING=1` / 显式 `STP_SKIP_INFRA_CHECK=1` 逃生（仅 non-production） |
| **审查收口** | 2026-05-25 针对性验收通过。2026-05-26：`STP_SKIP_INFRA_CHECK=1` 现跳过 Redis PING **与** in-process SAQ 启动，与 `.env.backend.example` / checklist 文案一致；`test_health_saq.py` patch `backend.main.is_saq_ready`。全量 pytest 2026-05-26：**718 passed / 15 skipped / 0 failed**。 |

---

### T-A2 · 生产 patrol 超时默认值与 env 文档

| 字段 | 内容 |
|------|------|
| **ID / U** | T-A2 / **U1** |
| **状态** | **已签收** |
| **依赖** | 无 |
| **改动面** | backend（`job_timeout_config.py`）、docs、deploy env |
| **步骤** | ① 与稳定性/业务方确认生产 `PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS` 目标值（建议 300–600s，init 仍 900s） ② 若调整默认值：改 `production_default` 并保持 dev 180s 不变 ③ `production-minimum-deployment-checklist.md` + `.env.backend.example` 增加 `PATROL_RUNNING_HEARTBEAT_TIMEOUT_SECONDS` 行 ④ README / runbook 说明「patrol 与 init 分级 timeout」行为 ⑤ 补充 `test_job_timeout_config.py` 生产默认断言 |
| **验收标准** | 文档与代码默认一致；recycler 对 patrol-active Job 使用 patrol 窗口（已有 `running_heartbeat_timeout_seconds`） |
| **工作量** | S |
| **风险** | 中 — 过短导致误杀长 patrol 间隔 Plan；须结合 `patrol_interval_seconds` 评估 |
| **审查收口** | 2026-05-25 针对性验收通过。2026-05-26：代码生产默认 300s；README §Job 超时、`preprod-drill-runbook.md` §5.1、checklist env 表已与 `job_timeout_config.py` 对齐。全量 pytest 2026-05-26：**718 passed / 15 skipped / 0 failed**。 |

---

### T-A3 · PRECHECK_QUEUE_STALE_SECONDS 调优与可观测

| 字段 | 内容 |
|------|------|
| **ID / U** | T-A3 / **U4** |
| **状态** | **已签收** |
| **依赖** | T-A1（SAQ 健康） |
| **改动面** | backend（`precheck_reaper.py`、`core/metrics.py`）、frontend（`PlanRunDetailPage` 阈值常量）、docs |
| **步骤** | ① 评估是否将默认 90s → 60s（或按 env 可配，已有 env 读取） ② enqueue precheck 时写入 `dispatch_state.enqueued_at` + Counter `stability_precheck_enqueue_total` ③ reaper re-enqueue 写 audit 或 metric `stability_precheck_reaper_reenqueue_total` ④ 前端 gate stale banner 阈值与后端 env 对齐（勿 hardcode 90） ⑤ 更新 runbook：「派发超过 N 秒无 Job」排查树 |
| **验收标准** | `test_precheck_reaper.py` 覆盖新阈值；UI banner 与 env 一致；Grafana 可画 enqueue vs reaper |
| **工作量** | S |
| **风险** | 低 |
| **审查收口** | 2026-05-25 针对性验收通过。2026-05-26：默认 stale 阈值与 env 对齐；enqueue/reaper metric 与前端 banner 已落地。全量 pytest 2026-05-26：**718 passed / 15 skipped / 0 failed**。 |

---

### T-A4 · Grace 倒计时与 BUSY 来源 UI

| 字段 | 内容 |
|------|------|
| **ID / U** | T-A4 / **U2** |
| **状态** | **已签收** |
| **依赖** | T-A2（timeout 文档定稿，便于展示 SLA） |
| **现状** | `GET /plan-runs/{id}/devices` 已返回 `grace_remaining_seconds` / `busy_reason`；矩阵与 Drawer 可见 grace / 认领 SLA / BUSY 来源（Vitest 覆盖） |
| **改动面** | backend（devices 聚合端点字段）、frontend（`DeviceMatrixCard`、`DeviceDetailDrawer`、可选 Host 页）、docs |
| **步骤** | ① 后端 `/plan-runs/{id}/devices` 增补：`grace_remaining_seconds`、`busy_reason`（`active_lease` / `adb_excluded` / `host_offline` 等） ② 矩阵列展示「grace 剩余 Xs」进度条或 chip（扩展现有 tooltip 为可见文案） ③ UNKNOWN 行显示 reconciler 秒级倒计时 ④ PENDING 行显示「认领 SLA 剩余」（tooltip 逻辑已有，需矩阵可见） ⑤ `DeviceDetailDrawer` 展示 BUSY 来源与关联 Job/PlanRun ⑥ Vitest：`DeviceMatrixCard.test.tsx` 覆盖 grace/pending 可见倒计时 |
| **验收标准** | 模拟 UNKNOWN Job：UI 显示 ≤300s 倒计时；设备被 lease 占用时显示原因；无需查 DB 即可判断卡点 |
| **工作量** | M |
| **风险** | 低 — 纯展示；字段需与 `UNKNOWN_GRACE_SECONDS` 同步 |
| **审查收口** | 2026-05-25 针对性验收通过。2026-05-26：`_derive_busy_reason` 增补 `adb_state` offline/unknown → `adb_excluded`（与 claim 排除一致）；Drawer Vitest 覆盖 grace / 认领 SLA / BUSY 来源。全量 pytest 2026-05-26：**718 passed / 15 skipped / 0 failed**。 |

---

### T-A5 · SAQ 不可用集成测试

| 字段 | 内容 |
|------|------|
| **ID / U** | T-A5 / **U11** |
| **依赖** | P0 enqueue 503 行为 |
| **改动面** | backend/tests、CI |
| **步骤** | ① 新增 `test_plans_run_saq_unavailable.py`：`enqueue_sync(..., required=True)` 抛 `EnqueueSyncError` → `POST /plans/{id}/run` 返回 503 + 可读 detail ② 断言 PlanRun 未处于「空 RUNNING」或 dispatch_state 标记 failed ③ 纳入 `main-chain-integration-smoke` job |
| **验收标准** | CI 绿；503 body 含运维可行动文案 |
| **工作量** | S |
| **风险** | 低 |

---

### T-A6 · 预发布人工 smoke 演练（真实设备）

| 字段 | 内容 |
|------|------|
| **ID / U** | T-A6 / **U17** |
| **依赖** | `seed_and_smoke.py`、runbook §4.0 |
| **改动面** | docs（runbook 勾选表）、可选 CI secrets |
| **步骤** | ① 按 `preprod-drill-runbook.md` 部署控制平面 + 1 Agent + 1 设备 ② 执行 `python backend/scripts/seed_and_smoke.py`（非 `--no-wait`）至 PlanRun 终态 ③ 记录：precheck 耗时、SocketIO 是否实时、Job 步骤 trace ④ 填写 runbook 附录「smoke 签字表」（日期/执行人/结果） ⑤ 可选：`workflow_dispatch` smoke-nightly 填 `STP_ADMIN_PASSWORD` |
| **验收标准** | PlanRun 终态 SUCCESS/PARTIAL；UI 与 DB 一致；签字表归档 |
| **工作量** | M（人工） |
| **风险** | 中 — 环境/设备不可用阻塞发布 |

---

### T-A7 · 生产最小部署核对清单执行

| 字段 | 内容 |
|------|------|
| **ID / U** | T-A7 / **U18** |
| **依赖** | T-A6 经验反馈 |
| **改动面** | docs（`production-minimum-deployment-checklist.md`） |
| **步骤** | ① 逐项勾选 §3（Nginx `/socket.io/`、`VITE_API_BASE_URL=`、Cookie env、AGENT_SECRET、Redis、单实例） ② 验证 `/metrics` IP 白名单 ③ alembic `upgrade head` ④ 登录 → Plan 执行 → 详情页 SocketIO 刷新（非仅 10s 轮询） ⑤ 将核对结果写回 checklist §5 或内部 wiki |
| **验收标准** | checklist 全部必选项 ✅；发现缺口则开 issue 或 PR |
| **工作量** | S（人工） |
| **风险** | 低 |

---

### T-B1 · Prepare 锁脚本版本 + complete 失败 SocketIO

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B1 / **U3** |
| **状态** | **进行中** |
| **Blocker** | 需 ADR-0023 prepare 快照 sha256 字段设计与 `complete_plan_run_dispatch` 漂移检测 PR；本批次优先测试/导出/sync 重试 |
| **改动面** | backend（`plan_dispatcher_sync.py`、`plan_precheck.py`、`aggregator`）、frontend（SocketIO invalidation） |
| **步骤** | ① `prepare_plan_run` 时将 `plan_snapshot.scripts[]` 写入 `content_sha256` 快照（来自 Script 表） ② `complete_plan_run_dispatch` 校验 sha 仍 active；漂移则 FAILED + 明确 error ③ complete 写 FAILED 时 `schedule_emit` `plan_run_status` ④ 测试：prepare 后 deactivate script → complete 失败 + push ⑤ 测试：prepare 快照与 gate verify 一致 |
| **验收标准** | 脚本失活窗口不再静默建 Job；UI 终态与 DB 同步（SocketIO） |
| **工作量** | M |
| **风险** | 中 — snapshot 体积；需与 ADR-0023 对齐 |

---

### T-B2 · Verify transient vs terminal 分类

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B2 / **U6** |
| **状态** | **进行中** |
| **Blocker** | 需 `_gather_verify` 错误 taxonomy + SAQ 重试边界单测；与 T-B3 sync 重试正交，待专批 |
| **改动面** | backend（`plan_precheck.py` `_gather_verify`） |
| **步骤** | ① 定义 transient 集合：`TimeoutError`、`ConnectionError`、`verify_exception` 且非 agent_offline ② transient → host_state `transient_error` + 可选 SAQ 重试，不立即 `_mark_precheck_failed` ③ terminal：`agent_offline`、sha mismatch、脚本不存在 ④ metric：`stability_dispatch_gate_verify_total{result=transient\|terminal}` ⑤ 扩展 `test_plan_precheck.py` mock RPC 抛 Timeout |
| **验收标准** | 单次网络抖动不整 Run FAILED；agent 真离线仍 FAILED |
| **工作量** | M |
| **风险** | 中 — 重试与 reaper 边界需单测 |

---

### T-B3 · Sync 可配置重试与派发重试 UX

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B3 / **U7** |
| **状态** | **已签收** |
| **依赖** | T-B2（可选） |
| **改动面** | backend（`plan_precheck.py`）、frontend（`DispatchGateCard`）、docs |
| **步骤** | ① env `DISPATCH_SYNC_MAX_ATTEMPTS`（默认 1，生产可 2） ② 替换 hardcode `sync_attempts cap = 1` ③ 失败且 attempts 未耗尽 → 留在 syncing 而非立即 terminal ④ UI 已有「重试派发」按钮：补充 attempts 展示与 disabled 规则 ⑤ 测试：第二次 sync 成功路径 |
| **验收标准** | env=2 时一次 sync 失败可恢复；=1 行为与现网一致 |
| **工作量** | M |
| **风险** | 低 |
| **审查收口** | 2026-05-26：`DISPATCH_SYNC_MAX_ATTEMPTS` env（默认 1）；sync/reverify 循环重试；precheck `sync_max_attempts` 字段；`DispatchGateCard` sync ×n/m 展示；`test_second_sync_attempt_success_when_max_attempts_two` 通过。 |

---

### T-B4 · Agent outbox 积压监控

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B4 / **U8** |
| **状态** | **已签收** |
| **改动面** | agent（`local_db.py`、`outbox_drainer.py`）、backend（`/agent/recovery/sync` 或 heartbeat extra）、`core/metrics.py` |
| **步骤** | ① Agent heartbeat 上报 `terminal_outbox_pending` / `log_signal_outbox_pending`（已有 local_db count 方法） ② 后端 Counter/Gauge：`stability_agent_outbox_pending{host_id,type}` ③ 文档：超阈值排查（网络/API 503/409 循环） ④ 可选：Host 详情页展示 outbox depth |
| **验收标准** | `/metrics` 可见 host 维度 outbox；Agent 单测 count 准确 |
| **工作量** | M |
| **风险** | 低 |
| **审查收口** | 2026-05-26：Agent heartbeat extra 上报 `terminal_outbox_pending` / `log_signal_outbox_pending`；后端 Gauge `stability_agent_outbox_pending{host_id,type}`；`test_heartbeat_outbox_metric.py` 通过。Host 详情页展示仍 optional 未做。 |

---

### T-B5 · PlanRun 导出报告对接后端

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B5 / **U10** |
| **状态** | **已签收** |
| **改动面** | backend（新端点或复用聚合）、frontend（`PlanRunHero` / `PlanRunDetailPage`）、docs |
| **步骤** | ① 设计 `GET /plan-runs/{id}/report/export?format=markdown|json`（聚合 summary + devices + timeline 摘要） ② 或 zip 多 Job report ③ 前端「导出报告」改调 API + 文件名带 plan/run id ④ 保留 JSON summary 快捷导出（可选） ⑤ Vitest：mock export 200 |
| **验收标准** | 终态 PlanRun 可下载 markdown/json；大 Run 不 OOM（分页或限 Job 数） |
| **工作量** | M |
| **风险** | 低 |
| **审查收口** | 2026-05-26：`GET /plan-runs/{id}/report/export?format=markdown|json`；`plan_run_export.py` 限 500 Job；前端改调 API；Vitest export + pytest `test_plan_run_export.py` 通过。 |

---

### T-B6 · precheck agent_offline UI 测试补全

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B6 / **U12** |
| **状态** | **已签收** |
| **改动面** | frontend/tests、backend/tests |
| **步骤** | ① backend 已有 agent_offline → FAILED ② Vitest：`DispatchGateCard` 展示 host 级 offline + errors ③ 集成：failed precheck fixture → GET plan-run → UI 断言 |
| **验收标准** | CI 覆盖 offline 文案与 phase=failed |
| **工作量** | S |
| **风险** | 低 |
| **审查收口** | 2026-05-26：`DispatchGateCard.test.tsx` + `PlanRunDetailPage.test.tsx` agent_offline / errors / phase=failed 断言通过。 |

---

### T-B7 · PENDING 120s timeout + SocketIO 集成测试

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B7 / **U13** |
| **状态** | **已签收** |
| **改动面** | backend/tests（recycler + mock emit） |
| **步骤** | ① 集成测试：创建 PENDING Job → 推进时间 >120s → recycler → FAILED ② patch `schedule_emit` 断言 `job_status` ③ 断言 lease 释放 |
| **验收标准** | 单测 + 集成在 CI 通过 |
| **工作量** | S |
| **风险** | 低 |
| **审查收口** | 2026-05-26：`test_pending_timeout_socketio.py` 集成测试 — PENDING >120s → recycler FAILED + patch `schedule_emit` job_status；纳入 `main-chain-integration-smoke`。 |

---

### T-B8 · UNKNOWN → grace → FAILED PlanRun 级断言

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B8 / **U14** |
| **状态** | **进行中** |
| **Blocker** | 需 recycler + reconciler + aggregator 三阶段串联 fixture；本批次未排入 |
| **改动面** | backend/tests（recycler + reconciler + aggregator） |
| **步骤** | ① 构造 RUNNING → recycler UNKNOWN → reconciler grace → FAILED ② 断言 PlanRun 终态 DEGRADED/FAILED ③ 可选：PlanRunDetailPage stuck banner 集成 |
| **验收标准** | 全链单测；PlanRun 不永久 RUNNING |
| **工作量** | M |
| **风险** | 低 |

---

### T-B9 · patrol_stall + manual_retry Agent 侧集成

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B9 / **U15** |
| **状态** | **进行中** |
| **Blocker** | Agent mock patrol-heartbeat 恢复序列较重；API 层 manual_retry 已有，Agent 侧 E2E 待专批 |
| **改动面** | backend/tests、agent/tests |
| **步骤** | ① API 层 manual_retry 已有 ② 新增：mock Agent patrol-heartbeat 恢复序列 ③ recycler patrol_stall 后 manual_retry → RUNNING ④ 可选：Agent `JOB_NOT_RUNNING` → recovery sync（P1#6 可合并） |
| **验收标准** | CI 覆盖 stall → retry → 心跳恢复 |
| **工作量** | M |
| **风险** | 中 — Agent mock 较重 |

---

### T-B10 · Plan 链 dispatch 全链路 E2E

| 字段 | 内容 |
|------|------|
| **ID / U** | T-B10 / **U16** |
| **状态** | **已签收** |
| **改动面** | backend/tests/integration |
| **步骤** | ① 父 PlanRun SUCCESS → aggregator → 子 PlanRun 创建 + gate ② dispatch 失败 → `next_plan_triggered` 回滚 + `result_summary.chain_dispatch_failed` ③ CHAIN run_type 走 precheck（若 T-C 未做，至少 verify mock） ④ 纳入 integration-smoke 或 nightly |
| **验收标准** | 两场景 CI 绿；与 P0#3 行为一致 |
| **工作量** | M |
| **风险** | 中 |
| **审查收口** | 2026-05-26：`test_plan_chain_e2e.py` — 父 SUCCESS → 子 PlanRun + gate；dispatch 失败 → `next_plan_triggered` 回滚 + `chain_dispatch_failed`；纳入 `main-chain-integration-smoke`。 |

---

### T-C1 · Watcher 生产评估与灰度 runbook

| 字段 | 内容 |
|------|------|
| **ID / U** | T-C1 / **U9** |
| **依赖** | T-A6 smoke |
| **改动面** | docs、agent env、可选 backend watcher-summary |
| **步骤** | ① 评估 NFS、`STP_WATCHER_ENABLED` 对 patrol 主链 CPU/IO 影响 ② 选 1 台 Agent 灰度开启 ③ 观察 `watcher-summary` + log_signal 量 ④ 写「开启/回滚」runbook ⑤ 决策：默认开 or 长期灰度 |
| **验收标准** | 书面评估 + 灰度签字；主链无回归 |
| **工作量** | M |
| **风险** | 低（默认 off） |

---

### T-C2 · 生产同源 SocketIO E2E

| 字段 | 内容 |
|------|------|
| **ID / U** | —（§5 缺口） |
| **依赖** | T-A7 |
| **改动面** | tests/e2e 或 docs+Playwright、deploy |
| **步骤** | ① 对 Nginx 443 起浏览器自动化 ② 登录 → 打开 PlanRunDetail → 触发 job_status push ③ 断言 DOM 更新 <3s |
| **验收标准** | 预发布环境自动化通过或 runbook 手工步骤归档 |
| **工作量** | L |
| **风险** | 中 — CI 需 staging 环境 |

---

### T-C3 · pipeline 真实 subprocess 集成

| 字段 | 内容 |
|------|------|
| **ID / U** | —（§5 缺口） |
| **依赖** | 无 |
| **改动面** | backend/agent/tests |
| **步骤** | ① 使用 `check_device` 等轻量脚本跑 init→patrol(1 cycle)→teardown ② mock adb 或 skip 需设备步骤 ③ 断言 step_trace 序列 |
| **验收标准** | Agent CI 增量 <5min |
| **工作量** | L |
| **风险** | 中 — 环境依赖 |

---

### T-C4 · 聚合失败 AlertManager 规则（metric 已有）

| 字段 | 内容 |
|------|------|
| **ID / U** | —（§4 P1#5） |
| **现状** | `recycler.py:268-281` 已在 `plan_aggregator_sync` except 分支调用 `record_plan_run_aggregation_failed()`（`stability_plan_run_aggregation_failed_total`）+ audit；`aggregator_sync.py` 本身无独立 metric |
| **改动面** | docs（AlertManager 规则）、可选 `metrics.py` 标签细化 |
| **步骤** | ① 确认 `/metrics` 已有 `stability_plan_run_aggregation_failed_total` ② 补 AlertManager 规则（如 `rate(...[5m]) > 0`）③ 可选：Grafana panel ④ 测试：mock aggregator 抛错 → metric 增（若尚无单测则补） |
| **验收标准** | 聚合失败可告警；recycler 不静默（audit+metric 已满足，告警为增量） |
| **工作量** | S |
| **风险** | 低 |

---

### T-C5 · claim lease Host 页阻塞提示（metric 已有）

| 字段 | 内容 |
|------|------|
| **ID / U** | —（§4 P1#7） |
| **现状** | `stability_claim_lease_failed_total` 已在 `agent_api.py:325` 计数；Host 页「设备被 lease 阻塞」提示仍缺 |
| **改动面** | frontend（Hosts）、docs |
| **步骤** | ① Host 页设备列表标注 lease 阻塞 ② 与 T-A4 `busy_reason` 复用 ③ 可选：AlertManager 规则 |
| **验收标准** | claim 失败可告警（metric 已有）；Host 页可见阻塞原因 |
| **工作量** | S |
| **风险** | 低 |

---

## 4. 建议排期（甘特式）

假设 **W1 = 2026-05-26 当周**，单人主力 + 部分并行测试/运维。

| 任务 | W1 | W2 | W3 | W4 | W5 | W6 |
|------|:--:|:--:|:--:|:--:|:--:|:--:|
| T-A1 Redis/SAQ 依赖收口 | ███ | | | | | |
| T-A2 patrol 生产默认值 | ██ | | | | | |
| T-A3 precheck stale 调优 | ██ | | | | | |
| T-A4 grace/BUSY UI | | ████ | ██ | | | |
| T-A5 SAQ 503 测试 | ██ | | | | | |
| T-A6 人工 smoke | | ██ | | | | |
| T-A7 生产部署核对 | | | ██ | | | |
| T-B1 prepare 脚本锁 | | | | ███ | | |
| T-B2 verify 分类 | | | | ███ | | |
| T-B3 sync 重试 | | | | | ██ | |
| T-B4 outbox 监控 | | | | | ███ | |
| T-B5 导出报告 | | | | | ████ | |
| T-B6–B8 测试补全 | | | ██ | ██ | ██ | |
| T-B9 patrol retry 集成 | | | | | | ███ |
| T-B10 Plan 链 E2E | | | | | ██ | ██ |
| T-C1 Watcher 评估 | | | | | | ██ |
| T-C2–C5 增强 | | | | | | ████ |

**里程碑**：

- **W2 末**：Phase A 代码+测试完成，启动 smoke（T-A6）
- **W3 末**：生产部署核对（T-A7）通过
- **W6 末**：Phase B 测试与导出收口；Phase C 按需启动

---

## 5. 与 smoke / CI 的关系

| 完成项 | 需更新的产物 |
|--------|----------------|
| T-A1 | `production-minimum-deployment-checklist.md`：Redis 显式 PING + SAQ 依赖措辞与代码一致 |
| T-A2 / T-A3 | checklist env 表；`preprod-drill-runbook.md` troubleshooting |
| T-A4 | 可选：smoke 脚本打印 devices grace 字段断言 |
| T-A5 | `ci.yml` `main-chain-integration-smoke` 用例列表 |
| T-A6 / T-A7 | runbook §4.0 签字；checklist §5 smoke 勾选 |
| T-B5 | runbook：导出报告验收步骤 |
| T-B6–B10 | 扩展 `main-chain-integration-smoke` 或 nightly job 路径 |
| T-C1 | agent `.env` 模板 + Watcher 专章 |
| T-C2 | 新 workflow `e2e-staging.yml`（可选，需 secrets） |

**当前 CI 基线**（不变直至上表任务合并）：

- PR：`backend-test` + `main-chain-integration-smoke` + frontend vitest
- Nightly：`smoke-nightly.yml` integration + 可选真实设备

---

## 6. 不在本计划范围（交叉 production-readiness）

| 项 | 说明 |
|----|------|
| HTTPS + Cookie 生产模板 | ADR-0024 guard；与主链并行，见 production-readiness P0#2 |
| 读 API 全量鉴权 | 公网前必须；内网 MVP 可暂缓（P0#7） |
| AlertManager / Loki / 备份 | ADR-0011 第二层；主链 metric（T-A3/B4/C4）就绪后再接告警 |
| 关闭 `/auth/register` | 公网安全；非主链执行阻断 |
| 单实例 backend 多副本 | 架构债；文档化即可（P0#6） |
| WiFi 分配 hard fail | ✅ 已落地（`plan_dispatcher_sync.py`）；prepare 脚本锁见 T-B1 |
| CHAIN 统一 precheck | ✅ 已落地（`dispatch_plan_sync` inline gate）；Plan 链 E2E 见 T-B10 |

---

## 附录：U1–U18 映射速查

| U | 任务 ID | Phase |
|---|---------|-------|
| U1 | T-A2 | A |
| U2 | T-A4 | A |
| U3 | T-B1 | B |
| U4 | T-A3 | A |
| U5 | T-A1 | A |
| U6 | T-B2 | B |
| U7 | T-B3 | B |
| U8 | T-B4 | B |
| U9 | T-C1 | C |
| U10 | T-B5 | B |
| U11 | T-A5 | A |
| U12 | T-B6 | B |
| U13 | T-B7 | B |
| U14 | T-B8 | B |
| U15 | T-B9 | B |
| U16 | T-B10 | B |
| U17 | T-A6 | A |
| U18 | T-A7 | A |

---

*维护：主链路加固子任务 | 与 fragility-analysis 同步更新*
