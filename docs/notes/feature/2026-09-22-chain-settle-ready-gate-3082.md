# 链触发 settle 窗设备就绪提前放行（#3082，revisit #2755 方向 1）

Status: implemented
Class: feature

## Decision

#2755 的固定 settle 窗（默认 180s）是止血：父 PlanRun 终态后无条件跳过即时触发，
与运维「就绪即续链」预期不符（父段全绿、设备回 ONLINE 无租约后仍要干等满窗，且刚
结束就观测会误判「链断了」）。本票落地 #2755 标记为「更本质」的方向 1：在
`trigger_next_plan`（async 即时路径）与 `trigger_next_plan_sync`（sync/补偿路径，
含 `reconcile_chain_trigger_sync`）的 settle 分支内加**设备就绪门控**——窗内若满足
就绪判据则提前放行，`CHAIN_TRIGGER_SETTLE_SECONDS` 语义从「固定等待」退化为
「上限兜底」。

就绪判据（`_settle_ready_decision` 纯函数，sync/async 共用；口径偏保守）：

- 父段 job×设备行经现行 `_select_chain_devices` 筛选后**候选集非空且无排除设备**
  （BUSY/ERROR/过期 OFFLINE 的排除态本身就是冲击未落回的信号）；
- 候选设备**全部** `device.status == ONLINE`——job COMPLETED 但仍 BUSY 的 #2648
  过渡态、心跳窗内瞬时 OFFLINE 都不算就绪；
- 候选设备上**无 ACTIVE 租约**（`DeviceLease.status == 'ACTIVE'`）。

任一条不满足 → 维持 #2755 行为：睡满窗，由 reconciler 下一 tick 重试。可观测面
区分两态：日志 `plan_chain_trigger_settling`（未就绪跳过）vs
`plan_chain_trigger_settle_early_release`（提前放行，含 saved_seconds），metrics
`stability_plan_chain_settle_outcome_total{outcome=settling_skipped|early_release}`。
回退开关 `CHAIN_TRIGGER_SETTLE_READY_GATE_ENABLED`（默认 true；false 回到纯固定窗）。
可选的第 3 条判据（teardown 冲击指标落回基线）未做——见 Revisit。

## Alternatives

- **把窗缩到 0 或调小**：直接放弃 #2755 尖峰防护（r431 实测 2s 间隔 init 失败
  40.6%），issue 非目标明令禁止；
- **窗内轮询 sleep 到就绪再返回**：把等待搬进触发协程，阻塞 job 终态化事务面，
  且 #2755 已否决「settle 塞进 reconciler 全局 interval」的同族形态——维持
  「跳过 + 下一 tick 重试」的无状态形状，就绪探测只是让某些 tick 更早通过；
- **判据只看 device.status**：job COMPLETED 但设备 BUSY（teardown 收尾回写滞后，
  恰是 #2648 的成因）会被误判就绪——故要求全 ONLINE 且叠加 ACTIVE 租约谓词。

## Verification

- `backend/tests/services/test_plan_chain_trigger.py` 32 passed：新增
  `TestChainTriggerSettleReadyGate`（就绪提前放行 / ACTIVE 租约阻断 / RELEASED
  租约不阻断 / 排除设备阻断 / 门控关回固定窗 / 无 job 父段等窗）、async 即时路径
  对称两例（mock session 验证查询序与放行/跳过分支）、`_settle_ready_decision`
  真值表单测；原 #2755「窗内跳过」三例种子改置 BUSY 过渡态后语义不变；
- 关联面回归：引用 `plan_chain_trigger`/`get_scheduler_settings` 的 9 个测试文件
  139 passed（含 `test_plan_chain_e2e`、`test_job_terminalization`）；
- `env_inventory.py --write/--check` 绿（259 读取名，新变量登记 `backend/.env.example`）；
- 验收对照：①就绪时子链触发延迟明显短于 180s（提前放行路径）；②设备 BUSY/租约
  未释时不早触发（阻断用例回归 #2755 尖峰防护）；③settle 跳过 vs 提前放行可由
  日志/metrics 区分。

## Revisit

- issue 中可选判据「teardown 冲击指标落回基线」未实现——若上线后
  `outcome=early_release` 段的 init 失败率仍高于过窗段，把该判据补进
  `_settle_ready_decision`（纯函数单点）或调 `CHAIN_TRIGGER_SETTLE_READY_GATE_ENABLED=false`；
- 就绪探测在窗内每个 tick 各加 2 次索引查询（job×device、lease），量级可忽略；
  若未来链规模把它放大，改为窗内一次性判定缓存；
- 观察两周后若提前放行率常态 ≈1，可评估把 `CHAIN_TRIGGER_SETTLE_SECONDS` 默认值
  从 180 降到纯兜底量级（窗的存在理由已被门控吸收）。
