# #764 修复：outbox 404-ack 先判 body——部署错位不再静默丢终态

Status: implemented
Class: bug-fix

## Decision

**变更**：`backend/agent/outbox_drainer.py` 的 404 分支先解析响应 body：

- `_is_job_not_found(response)`（新增）只认**明确形态**——字符串
  `detail == "job not found"`（`/complete` 的实际形态，`agent_api.py`），或结构化
  `code ∈ {JOB_NOT_FOUND, JOB_GONE}`（detail/error 两处承载位置，大小写与空白容忍）；
- 命中 → **保持既有语义**：`ack_terminal` + `outbox_drain_job_gone` WARNING
  （不计 `sent`，与旧行为一致——`sent` 只计真实上送成功）；
- 未命中（未知路由 / 非 JSON body / 其它 detail）→ 走
  `_retain_or_dead_letter(reason="unstructured_404")`，与 409 unstructured 分支**同策略**：
  留在 outbox 重试，达 `_MAX_TERMINAL_ATTEMPTS`（10 次）转死信（ERROR 日志 +
  `conflicts_retained_total` 指标），不再静默 ack。

**为什么必须修**：FastAPI 对未知路由也返回 404（`{"detail": "Not Found"}`），与
`/complete` 的「job 不存在」同码不同义。route 变更 / 部署错位 / 代理打错路径的窗口
内，旧实现会把**终态事实**当 job gone ack 掉，仅剩一条 WARNING；`job_terminal_outbox`
是同族三表里**唯一没有** `replay_*_dead_letter` 出口的，静默丢弃不可逆。

**为什么选 retain→死信而不是无限 retain**：与 409 unstructured 分支保持一致
（#762 的既有裁决），且需要给「部署错位长期未修」一个可见终态（ERROR + 死信计数），
避免卡死行占队头。此路径下终态事实仍是**显式**丢失（带审计错误行）而非静默。

**未改**：`/complete` 的 404 语义（控制面）、其它状态码分流（409/408/429/5xx 不动）。

## Alternatives

- **所有 404 一律 retain**：拒绝。`job not found` 是合法终态（job 已清理），一律
  retain 会制造无意义积压、占队头饿死新终态行（#762 修的就是这个形态）。
- **维持现状（所有 404 一律 ack）**：拒绝。这正是本单问题——部署错位窗口静默丢事实。
- **用 Content-Type / 路径前缀等 HTTP 层特征判别**：拒绝。代理与框架版本差异大，
  body 语义判别（code/detail）更稳定，且与既有 `_parse_error_code` 同族。
- **新增独立的部署错位告警/指标**：不做。#743 已有 `complete 404 速率`告警覆盖
  「404 变多」的观测面；本单只改分流，不扩指标（避免与 #743 重复）。
- **同时把死信行做可 replay 出口**：超出本单范围（属 terminal outbox 缺 replay
  的同族问题，见 Revisit）。

## Verification

- **红绿对照**：`git stash` 暂存实现改动后重跑新测试 → **15 failed**（含
  `test_404_unknown_route_is_retained_not_acked`、`test_404_unstructured_follows_dead_letter_cap`）；
  恢复实现 → `pytest backend/agent/tests/test_terminal_outbox_dead_letter.py -q` →
  **36 passed**；
- 新增 6 组断言：job-gone 仍 ack（回归守卫）/ 未知路由 retain 且不 ack /
  未知路由持续 → 达 10 次上限转死信 / body 判别矩阵 12 例 / `None` response /
  非 JSON body；
- `JWT_SECRET_KEY=ci-test-secret-key python -m pytest backend/agent/tests/ -q` →
  **1783 passed**（163s，同 CI 环境口径）；
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- **死信终点**：若部署错位真的持续 10 个周期（~150s），终态行进死信且当前**无
  replay 出口**——若现场出现该路径的丢事实事故，需与 #742/#762 一起评估
  terminal outbox 的人工重放/审计入口；
- **控制面 404 结构化**：若中心日后给 `/complete` 404 增加 `code`（如
  `JOB_NOT_FOUND`），判别矩阵已覆盖，无需改代码；
- **其它状态码的同类语义混用**：本单只处理 404；若发现 400/403 等存在
  「同码不同义」的部署错位风险，按同一模式（body 判别 + retain 分支）扩展。
