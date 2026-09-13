# #1822 链衔接瞬时 OFFLINE 宽限入列

Status: implemented
Class: bug-fix

## Decision

#1686 只认 `Device.status == ONLINE` 做链下一段筛选，父段终态瞬间若设备
处于重启/探活抖动（瞬时 OFFLINE）会被写入 `chain_excluded_devices` 且
`next_plan_triggered` 封门——**无回补路径**，整段链覆盖静默缺失。

本单取建议路径 1（宽限）：与 `HOST_HEARTBEAT_TIMEOUT_SECONDS`（默认 300s）
对齐，`OFFLINE` 且 `last_seen` 落在窗口内仍入列；`BUSY` / `ERROR` / 过期
`OFFLINE` 继续排除，保留 #1686「勿因真离线阻塞准入泵数小时」的约束。

抽取 `_select_chain_devices` 供 async/sync 共用；排除项可带 `last_seen` /
`reason` 便于观测。

## Alternatives

- 子 run 准入前周期补入 `chain_excluded_devices`（路径 2）：改动面大，需与
  admission pump / job 创建协作；留作 Revisit。
- 仅 UI/告警消费 `chain_excluded_devices`（路径 3）：不修复漏跑。
- 撤销 #1686 全量继承：会复现准入泵长时间阻塞。

## Verification

- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest backend/tests/services/test_plan_chain_trigger.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

- 若现场仍见漏跑，再做路径 2（QUEUED 子 run 补入已恢复设备）；
- `chain_excluded_devices` 的 UI/告警消费面仍可独立推进。
