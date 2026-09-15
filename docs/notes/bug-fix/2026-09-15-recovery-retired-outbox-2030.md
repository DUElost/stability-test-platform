# #2030 退役机 recovery_sync 丢 outbox_actions：终态证据每轮重发且永不被 ack

Status: implemented
Class: bug-fix

## Decision

ADR-0038 ④ 切片七（#1805，`aae0f504`）在 `recovery_sync` 的退役早返回里固定返回
`"outbox_actions": []`，切断了 Agent 侧 ack：Agent 只在响应携带 action 时才 ack
本地 outbox（`backend/agent/main.py`），`get_pending_outbox()`（`acked=0` 的行）
因此每轮重发且永不被 ack，退役前产生的 `UPLOAD_TERMINAL` 结果也不再投递。

决定：把 outbox 推导块从函数尾部**前移到退役判据之前**，退役早返回与正常路径
共用同一份 `outbox_actions`（`"outbox_actions": [a.model_dump() ...]`）。推导只读
（`db.get(JobInstance)`，不改身份、不锁 lease），前移不改变任何写语义与加锁顺序
（`Lease → Job → Device` 仍在 job_actions 段内）；退役机在 D5 下本就属回收类
放行面——终态证据必须能上传/被 ack（「退役即不再承接工作」不等于「丢弃已产生的
证据」）。

**影响面**：`backend/api/routes/agent_api.py::recovery_sync` 一个函数；响应契约
（`outbox_actions` 逐条 action/reason）不变，仅退役分支由空列表恢复为正常推导。

## Alternatives

- **在退役分支内复制推导块**：两处逻辑可独立漂移，后续改推导规则须改两处。否决。
- **提取 `_derive_outbox_actions()` helper 供两处调用**：语义等价且不动语句顺序，
  但把一段无锁、无写、无顺序依赖的 20 行逻辑提前提成函数，收益仅是「不改顺序」，
  结构变化反而更大。否决（出现第三个调用点再提取）。
- **Agent 侧改成「无 action 也 ack」**：Agent 无从区分「控制面尚未处理该 entry」与
  「控制面明确不回」，且会改变所有控制面响应的兜底语义。否决。
- **退役分支只回 NOOP 类 action**：丢掉非终态 job 的 `UPLOAD_TERMINAL` 语义——
  退役前产生的终态证据仍应投递。否决。

## Verification

- `python -m pytest backend/tests/api/test_agent_dual_write.py -k recovery_sync -q`
  → **18 passed**（原 17 + 新增 1 例）；
- 新增 `test_recovery_sync_retired_host_still_returns_outbox_actions`：退役主机 +
  三条 pending_outbox（非终态 → `UPLOAD_TERMINAL` / 已终态 → `NOOP` / 不存在 →
  `NOOP`）逐条断言，并在同例断言在飞作业仍是 `ABORT_LOCAL/host_retired`
  （切片七语义不回归）；
- **单点 mutation 反例**：仅把退役分支的返回值改回 `[]`（正常路径不动）→ 新用例
  **FAILED** 且 3 个活跃路径既有 outbox 用例仍 passed（红灯可归因于退役分支）；
  恢复后复绿。mutation 后清 `backend/api/routes/__pycache__/agent_api.*.pyc`
  防复用旧字节码假绿（本仓先例）；
- 回归：`test_agent_dual_write` + `test_agent_api_watcher` +
  `test_host_retired_heartbeat_1806` + `test_job_state_machine` → **109 passed**；
- `ruff check` 两文件 → All checks passed。

## Revisit

- 原切片七 Note（`docs/notes/feature/2026-09-13-recovery-retired-1805.md`）Revisit
  第 3 条「Agent 收到 ABORT_LOCAL 后的本地清理与终态上报由既有链路保证」被本单
  证伪，已在该 Note 上交叉链接更正。
- `ABORT_LOCAL` 与 `UPLOAD_TERMINAL` 对同一 job 并存时 Agent 侧的处理顺序
  （先处理 outbox 上传、再本地停止）未新增断言——属既有链路行为；若出现
  「上传失败是否仍执行 ABORT_LOCAL」的真实问题再补。
