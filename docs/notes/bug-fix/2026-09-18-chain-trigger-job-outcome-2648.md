# Agent Note — 链触发按父段 job 终态选设备（#2648）

- **Issue**: #2648
- **Status**: Done
- **Class**: bug-fix

## Decision

`_select_chain_devices` 的判定主依据从「触发瞬间的 device.status」改为「父段
JobInstance 终态」：job COMPLETED 的设备无条件进入链下一段；非 COMPLETED
（FAILED/ABORTED）沿用 #1822 状态规则（ONLINE / 心跳窗口内瞬时 OFFLINE 入列，
BUSY/ERROR/过期 OFFLINE 排除）。

生产实证（2026-09-18）：run 421（24 台全部 COMPLETED）终态化 3 秒后触发
run 422，21 台 teardown 后 BUSY→ONLINE 回写未完成，被按瞬时 status=BUSY
永久踢出后三段且不可回补。这 21 台在 08:00 全量窗（run 426）全部正常完成，
证明设备健康、纯属调度竞态。修复后可用性判断收敛到准入层（admission 终检
+ DEVICE_BUSY 重试），触发层不再预判。

## Alternatives

1. 触发前等待全部设备 BUSY→ONLINE 沉降再选——引入触发延迟与新超时语义，
   且「等多久」无自然答案；否决。
2. 链触发传入父段全量设备、完全不筛——把 stale OFFLINE 也放回，会在准入
   all-or-nothing 现状下复活 #1686（真离线设备阻塞整链）；保留 #1822 的
   非 COMPLETED 兜底规则以兼容现状，准入收缩策略由 #2651 单独解决。

## Verification

- `python -m pytest backend/tests/services/test_plan_chain_trigger.py
  backend/tests/services/test_chain_trigger_offline_filter.py
  backend/tests/services/test_plan_run_chain.py` → 20 passed
  （新增 `test_select_chain_devices_completed_job_overrides_busy_and_offline`
  钉住「COMPLETED 优先于 BUSY/过期 OFFLINE」；既有 #1822 用例改挂 FAILED
  job 语义不变；AST 结构守卫 #2030 断言继续成立）。

## Revisit

#2651（准入收缩）落地后，非 COMPLETED 兜底规则可再评估是否整体退化为
「父段 job 终态唯一判据」。
