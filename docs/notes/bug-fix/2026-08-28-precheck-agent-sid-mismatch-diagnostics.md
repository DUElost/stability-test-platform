# precheck 的 agent_offline 与 HTTP 心跳冲突时补诊断告警

Status: implemented
Class: bug-fix

## Decision

precheck 判定 Agent 可达性走 **SocketIO RPC**（`precheck/verify.py:verify_one_host`
→ `call_agent_rpc`，room 键 `agent:{HOST_ID}`），而 `host.status` / `last_heartbeat`
来自 **HTTP 心跳**——两条互不相关的通道。HOST_ID 迁移后若 Agent 侧 `.env` 未同步，
RPC 恒抛 `AgentNotConnectedError` → 判 `agent_offline`，心跳却照样新鲜（4–23s），
于是心跳**掩盖**了失配：PlanRun 卡 QUEUED、`queue_blockers` 恒为
`host_unreachable`，日志里一条告警都没有（PlanRun #247，8 台 host 全中招）。

本次不改动判定结果，只把失配显式化：

- 新增 `backend/services/precheck/reachability.py`
  - `diagnose_unreachable_hosts(db, host_ids)` —— 纯读，产出每个 host 的
    `host_status` / `last_heartbeat` / `heartbeat_fresh` / `sid_registered` /
    `conflict` / `confidence`，心跳新鲜度阈值沿用
    `HOST_HEARTBEAT_TIMEOUT_SECONDS`（与 `api/routes/devices.py` 同一环境变量）；
  - `log_unreachable_conflicts(...)` —— 只对 `conflict` 项打 ERROR
    `agent_sid_mismatch`，带 host_id / plan_run / 心跳时间 / 失配成因说明。
- 接入两处判定点：
  - `precheck/runner.py` —— `mark_precheck_failed(agent_offline ...)` 前把诊断写进
    `run_context.precheck.hosts[hid].reachability`（事后可追溯）；
  - `services/admission_pump.py` —— 新增 `_annotate_unreachable`，首次 verify 与
    sync 后的 reverify 两处给 `host_unreachable` blocker 补 `reachability` 字段。

**confidence 字段的必要性**：开启 Redis adapter 时 Agent 可能连在别的实例上，
「本进程无 sid」只是弱信号，故 `confidence=low` 并在日志里带上
`redis_adapter_enabled`，避免把正常的多实例分布误报成失配。

影响面：只增字段与日志。判定、requeue 语义、状态机全部未变，已有
`agent_offline` 用例无需改动。

涉及文件：`backend/services/precheck/reachability.py`（新增）、
`backend/services/precheck/runner.py`、`backend/services/admission_pump.py`、
`backend/tests/services/test_precheck_reachability.py`（新增）。

## Alternatives

- **改判定**：心跳新鲜时不再判 offline，改走 HTTP 通道兜底。放弃——心跳新鲜
  **不等于** RPC 可用（进程僵死、room 未加入都会心跳照发），拿心跳去推翻 RPC 判定
  会把真正的离线放过去；且判定语义变动需要回归整个 admission 状态机。
- **只加指标不加日志**：`sid` 注册状态进 Prometheus。放弃——issue 第 2 点（暴露
  sid 注册状态）确实有价值，但没有告警通道时，值班的人仍要主动去看图；日志 ERROR
  是零门槛的那一层，先补齐它，指标留作后续。
- **冲突时直接 fail 而非 requeue**：放弃——HOST_ID 失配可以被运维修好，但其间
  Agent 短暂重启同样会「心跳新鲜 + RPC 断」，一律 fail 会把瞬时抖动变成 PlanRun
  失败。保留 requeue，只把矛盾暴露出来。
- **写 audit 记录**：issue 原文提了「告警 + 审计」。审计表语义是人工操作
  （`record_audit` 带 user_id），自动诊断写进去会稀释审计流。改为落
  `run_context.precheck`，同样是持久可追溯，但不污染审计。

## Verification

- `backend/tests/services/test_precheck_reachability.py`（新增 9 例）：
  conflict / 心跳过期 / host 非 ONLINE / sid 在册 / Redis adapter 下 confidence /
  未知 host 不出现 / 空输入 / 告警只覆盖 conflict 项且带可定位上下文。
- 回归：`backend/tests/services/test_plan_precheck.py`、
  `test_admission_queue_step4.py`、`backend/tests/integration/`（SQLite 模式全绿，
  76 + 9 例）。
- 人工核对：构造「host ONLINE + 心跳新鲜 + 未连 SocketIO」，确认日志出现
  `agent_sid_mismatch` 且 `run_context.precheck.hosts[*].reachability.conflict=true`。

## Revisit

- 若 issue 第 2 点（每 host 的 SocketIO sid 注册状态与心跳并列展示）落地，本模块
  的诊断字段应直接复用，避免两处各算一次「是否失配」。
- 若将来出现「HOST_ID 之外的失配成因」（如 namespace 变更、room 命名规则调整），
  日志里的成因文案需同步更新，否则会误导排查方向。
