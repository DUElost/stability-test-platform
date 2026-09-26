# 准入 verify 批量 RPC 超时的缓解与诊断修复（#3422）

Status: implemented
Class: bug-fix

关联：[#3422](https://github.com/DUElost/stability-test-platform/issues/3422)（本单，根因仍开）、
[#3370](https://github.com/DUElost/stability-test-platform/issues/3370)（被阻塞的验收台账）、
[#3399](https://github.com/DUElost/stability-test-platform/issues/3399)（同日 drift 口径，独立）。

## Decision

生产实测（2026-09-26，见 #3422 判别表）：**单次 `gather_verify` 的 ack 放大随主机数劣化**——
≤5 主机（579/580）恒成功，≥30 主机（581 单轮 30/30；578 全机队每轮 7–20/48）恒超时；
而 48 个**独立**单机 RPC 全 200（≤2.5s）。admission 全有全无 ⇒ 15:50 起大 run 无法准入。
本次落地三处（均为**缓解 + 可观测**，根因未定位、单子保持 OPEN）：

1. **并发加界（缓解）**：`gather_verify` 用 `asyncio.Semaphore(VERIFY_CONCURRENCY)`
   包 `verify_one_host`，默认 **5**（`STP_PRECHECK_VERIFY_CONCURRENCY` 可覆盖）。
   每台仍持有独立的 `VERIFY_TIMEOUT_SECONDS`（10s）预算，返回结构与顺序不变。
2. **超时归因修正（诊断）**：`call_agent_rpc` 增捕 `socketio.exceptions.TimeoutError`
   ——python-socketio 的 `call()` ack 超时抛**自己的**同名类（`str()` 为空、与
   `asyncio.TimeoutError` 无继承关系），此前落进通用分支显示为 `failed: `（空消息），
   生产上即据此误判。两条超时路径统一映射为 `timed out after {timeout}s`。
3. **RPC 埋点（诊断）**：新增 `stability_agent_rpc_total{event,outcome}`
   与 `stability_agent_rpc_duration_seconds{event}`（event/outcome 白名单归一）；
   `call_agent_rpc` 在所有终局落点（ok/timeout/error/no_ack/not_connected）。

## Alternatives

- **提高 `VERIFY_TIMEOUT_SECONDS`**：不采纳——把故障从「超时」变成「慢」，且掩盖
  放大机制；判别数据也显示是并发规模相关而非单机慢。
- **reconciler 式重试/放宽 admission 全有全无**：不采纳——改变准入语义（owner 明确
  保留 MANUAL 全有全无；SCHEDULE/CHAIN 已有收缩准入），且不解决根因。
- **只加埋点不改行为**：不采纳——验收与排程 run 被阻塞中，需要先恢复可用性。
- **回滚 release**：不采纳——两 release 该路径 `git diff` = 0，且控制面重启不清除。

## Verification

- 新增用例（worktree 实跑）：
  - `backend/tests/services/test_precheck_verify_concurrency_3422.py`：并发峰值 ≤ 加界值；
    单主机异常按 host 归因、不打断其余主机。
  - `backend/tests/realtime/test_agent_rpc.py`：`socketio.exceptions.TimeoutError` →
    `AgentRpcError("… timed out after 2.0s")`；outcome 埋点自增（ok/timeout）。
- 聚焦回归：`backend/tests/realtime/test_agent_rpc.py` +
  `backend/tests/services/test_precheck_verify_concurrency_3422.py` +
  `test_precheck_scripts.py` + `test_plan_precheck.py` + `test_admission_queue_step*.py`
  → `141 passed`；新增用例定点 `6 passed`。
- 全量 `backend/tests/` 与 `check:quick`：见 PR 描述（逐条贴实际输出）。
- **生产验证（部署后）**：≥30 主机的 run 应能准入（581 的复现配方可直接复用）；
  `stability_agent_rpc_total{event="verify_scripts"}` 应显示 timeout/ok 分布；
  #3370 的验收随即可恢复执行。

## Revisit

- 本单**保持 OPEN**：并发加界是**过渡缓解**，终态出口 = 根因定位后去掉或重新标定该界
  （根因方向：`sio.call` ack 回调在 N 并发下的行为 / engineio 发送路径 / nginx websocket
  代理，需要进程内可视性）。
- 若部署后仍有个别超时（加界不充分），调 `STP_PRECHECK_VERIFY_CONCURRENCY` 并回填 #3422。
