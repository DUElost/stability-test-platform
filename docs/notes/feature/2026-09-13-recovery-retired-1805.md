# #1805 矩阵 row 12：退役主机不得经 recovery/sync 复活在飞作业

Status: implemented
Class: feature

## Decision

`POST /agent/recovery/sync`（`backend/api/routes/agent_api.py::recovery_sync`）在取到
host 后、**身份更新之前**新增退役判据：退役主机（`retired_at IS NOT NULL`）对上报的
`active_jobs` 一律返回 `ABORT_LOCAL / reason="host_retired"`，**不返回 `RESUME`**，
也不写入 `boot_id`/`last_agent_instance_id`。补 2 例测试（1 正 1 负）。

## 缺陷确认（实测，非推演）

矩阵 row 12（`ADR-0038:166`）把 `agent_api.py:2887-2910`（recovery/sync 覆写
boot_id）列入「Agent 自服务写面」。D5bis 要求派发/认领**活读 `retired_at`**、在飞
Run 以显式 `HOST_RETIRED` 收敛（FAILED + 审计）。

**探针实测（修复前）**：对退役主机 + ACTIVE lease + 在跑 Job 调 `recovery_sync`：

```
PROBE ACTION: RESUME / same_boot_instance_takeover
```

→ 退役主机的在飞作业被 **recovery 复活**，**绕过** D5bis 规定的 `HOST_RETIRED`
收敛路径。即 recovery 是继「派发」「认领」之后的**第三条**能让作业回到退役机的
路径，而切片一/四只堵了前两条。

**为什么既有 lease 约束拦不住**：`device_lease_reconciler` 不读 `retired_at`，
故退役**不会**清理 ACTIVE lease；lease 存活 → recovery 判定照走 RESUME 分支。

## 修法与位置

早返回放在**身份更新之前**：退役机即使上报新 `boot_id`/`instance_id` 也不该继续
承接工作，故不写身份、不进入 RESUME 判定，直接引导本地停止。

**为什么用 `ABORT_LOCAL` 而非新增动作**：`ABORT_LOCAL` 是既有动作（Agent 本地停止），
语义恰好是「不要继续跑这个作业」；引入新动作会扩大 Agent 侧协议面，且需两端同步
升级。理由字符串 `host_retired` 与切片一的 `_FATAL_DISPATCH_REASONS`、切片三的
`skipped_retired` 同族，便于检索归因。

**作业终态归属**：本端点只引导 Agent **本地**停止，不改 Job 状态、不删 lease——
终态收敛仍由既有 abort/lease 回收链完成（与「不越权写终态」的既有分层一致）。

## Alternatives

- **返回 `RESUME` 但在 payload 里标注 retired** → 否决：那仍是「让作业在退役机上
  继续跑」，与 D5bis「不静默缩小目标集合、以 HOST_RETIRED 收敛」相悖；标注不能替代
  收敛。
- **新增动作如 `ABORT_RETIRED`** → 否决：扩大 Agent 侧协议面并需两端同步升级；
  `ABORT_LOCAL + reason` 已能表达同一语义（reason 承载归因）。
- **在本端点直接写 Job FAILED** → 否决：越出「recovery 只做状态对齐」的职责，
  且绕过既有 abort/终态聚合链（`PlanAggregator.on_job_terminal` 等），可能造成
  聚合口径不一致。终态仍归既有链路。
- **在 `device_lease_reconciler` 里退役即清租约** → 否决（本切片范围）：那会改动
  租约回收语义（影响面远大于本面），且「退役不清租约」是既有设计（退役不删行、
  租约按到期/liveness 回收，见 `host_retirement.py` 抬头 D2 说明）。本单只在 recovery
  入口加判据。
- **只改文档说明「recovery 不适用于退役机」** → 否决：实测证明该路径**可达且会
  真实 RESUME**，不是理论问题。

## Verification

- `python -m pytest backend/tests/api/test_agent_dual_write.py -k recovery_sync -q`
  → **17 passed**（含新增 2 例）；
- **探针实证（修复前）**：`PROBE ACTION: RESUME / same_boot_instance_takeover`
  ——确认缺陷可达；
- **红绿双向**：临时移除判据 → `test_recovery_sync_retired_host_aborts_instead_of_resuming`
  **失败**；还原 → 17 passed；
- **负向对照**：`test_recovery_sync_active_host_still_resumes` 通过——活跃主机仍
  照常 RESUME，修复未把正常恢复一并禁掉；
- **回归**：`test_agent_dual_write` + `test_agent_api_watcher`
  + `test_host_retired_heartbeat_1806` + `test_job_state_machine`
  → **108 passed**；
- `ruff check` 两文件 → All checks passed；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿。

## Revisit

- **矩阵 row 12 剩余面**：`heartbeat.py:403`（设备 re-home）已由 ⑤ 切片（#1806）
  以「保持退役、不自动解除」覆盖；`routes/scripts.py:212,231,241`（脚本目录重拉）
  **尚未处理**——经核查该面是 **Agent 只读拉取全局脚本目录**（
  `agent/registry/script_registry.py:75` 调 `GET /api/v1/scripts`），既不改退役机
  状态也不触碰退役机，**不属于 D-5 两类中的任何一类**。是否需显式处理（例如仍
  放行并注明）需独立裁决，本单不自行扩面。
- **`device_lease_reconciler` 与退役的交互**：当前退役不清租约，故 ACTIVE lease
  可跨退役存活。本单只在 recovery 入口拦；若后续发现其它依赖「lease 存活即
  可继续」的路径（如 `/complete`、upload 终态迟到上传），应逐一按 D-5 分类核查。
- **`ABORT_LOCAL` 的 Agent 侧行为**：本单只改控制面返回；Agent 收到
  `ABORT_LOCAL` 后的本地清理与终态上报由既有链路保证，未在本单新增断言。
  **更正（#2030，2026-09-15）**：该断言不成立——退役早返回当时连
  `pending_outbox` 一起丢掉（`"outbox_actions": []`），Agent 侧「无 action 不
  ack」使终态 outbox 每轮重发且永不被 ack；修复与验证见
  [`2026-09-15-recovery-retired-outbox-2030.md`](../bug-fix/2026-09-15-recovery-retired-outbox-2030.md)。
