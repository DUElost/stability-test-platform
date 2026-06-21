# 平台验收：冒烟与回归索引

> **PRD**：[`prd/00-platform-overview.md`](../prd/00-platform-overview.md)  
> **预发布逐条**：[`preprod-drill-runbook.md`](../preprod-drill-runbook.md)  
> **生产最小集**：[`production-minimum-deployment-checklist.md`](../production-minimum-deployment-checklist.md)

---

## 1. CI 门禁（每次 PR）

| ID | 检查 | 命令/位置 |
|----|------|-----------|
| AC-CI-01 | 后端语法 | `python -m compileall backend` |
| AC-CI-02 | 后端测试 | `pytest backend/tests/`（PostgreSQL service） |
| AC-CI-03 | 前端类型 | `cd frontend && npx tsc --noEmit` |
| AC-CI-04 | 前端构建 | `cd frontend && npm run build` |
| AC-CI-05 | 主链冒烟 | CI job `Main-chain integration smoke` |
| AC-CI-06 | Agent 单测 | `pytest backend/agent/tests/`（建议 PR 触达 Agent 时本地跑） |

---

## 2. 主链路（自动化）

| ID | 场景 | 测试文件 |
|----|------|----------|
| AC-MAIN-01 | Plan 创建 → dispatch → Job 列表 | `integration/test_main_chain_happy_path.py` |
| AC-MAIN-02 | Plan 链触发 | `integration/test_plan_chain_e2e.py` |
| AC-MAIN-03 | PENDING 超时 | `integration/test_pending_job_timeout.py` |
| AC-MAIN-04 | 派发门禁失败可重试 | `api/test_plan_precheck*.py` |
| AC-MAIN-05 | PlanRun 聚合端点 | `api/test_plan_run_aggregation_endpoints.py` |

---

## 3. 安全与认证

| ID | 场景 | 测试 |
|----|------|------|
| AC-SEC-01 | CSRF 拒绝/放行 | `test_csrf_origin_middleware.py` |
| AC-SEC-02 | Refresh 黑名单 logout | `api/test_refresh_token_blacklist.py` |
| AC-SEC-03 | 生产 Cookie guard | `test_production_guards.py` |

---

## 4. Agent 核心

| ID | 场景 | 测试目录 |
|----|------|----------|
| AC-AGT-01 | pipeline_engine lifecycle | `agent/tests/test_pipeline*.py` |
| AC-AGT-02 | Watcher / log_signal | `agent/tests/test_*watcher*` |
| AC-AGT-03 | AEE paths / reconciler | `agent/tests/test_aee*.py` |
| AC-AGT-04 | Script registry | `agent/tests/test_script_registry.py` |

方案 C 专项：[`2026-plan-c-sprint2-3.md`](./2026-plan-c-sprint2-3.md)

---

## 5. 手工冒烟（发版前）

与 [`preprod-drill-runbook.md`](../preprod-drill-runbook.md) 对齐，最小集：

| ID | 步骤 | 期望 |
|----|------|------|
| AC-M-01 | 登录 Dashboard | Cookie 会话有效 |
| AC-M-02 | 主机 ONLINE | 心跳正常 |
| AC-M-03 | 创建 PlanRun | 门禁 ready → Job RUNNING |
| AC-M-04 | PlanRun 详情 | 时间线/设备矩阵有数据 |
| AC-M-05 | Job 终态 | 报告可打开；设备非 BUSY |
| AC-M-06 | Agent 热更新 | 服务恢复、可再认领 |

---

## 6. 方案 C 发版附加（Agent 变更时）

见 [`2026-plan-c-sprint2-3.md`](./2026-plan-c-sprint2-3.md) §发版勾选。

**注意**：Sprint 3 控制面未收口前，Archive 区与 risk_summary 可能仍按旧模型 — 不作为方案 C 发版阻塞项，但须在 #32 跟踪。

---

## 7. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-06-21 | 初版：平台级验收索引 |
