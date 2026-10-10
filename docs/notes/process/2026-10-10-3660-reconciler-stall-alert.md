# #3660：设备租约回收器 D5 停转告警（明确停转 / 无数据）

Status: implemented
Class: process

## Decision

新增两条控制面告警，信号只使用
`stability_reconciler_runs_total{check="terminal_job_active_lease",outcome="success"}`：

- `StabilityDeviceLeaseReconcilerStalled`：15 分钟窗口内有采样且成功增量为 0，持续 10 分钟 → 明确停转。
- `StabilityDeviceLeaseReconcilerNoData`：该 success 子序列 `absent(...)` 持续 15 分钟 → 无数据 / UNKNOWN。

两态分别成规则、分别可读，不合并；无数据不得静默当成健康（#3532 反模式）。

**预置选择：代码侧。** 在 `backend/core/metrics.py` import 时为
`check="terminal_job_active_lease"` 的 `success` / `error` 调用 `.labels(...)` 建出 0 值子序列。
理由：重启后若回收器一次也没成功，没有预置则只能落入「无数据」；预置后「明确停转」可判，
并把 SOP G1 的「无结果」收窄为真正的采集/进程问题。非主节点也会暴露 0，
`sum(increase(...))` 仍只反映主节点成功增量。不顺手修 #3500 其余 Counter；冲突时以 #3500 为准。

未改回收器行为，未改 #3646 管理端释放 API。未改 SOP G1 正文（见 Revisit）。

**SOP G1 第 10 步是否改为「先查本告警状态」：** 建议在告警规则部署到控制面 Prometheus
并完成一轮人工对照后改为「先查本告警是否 firing，再按需跑原 PromQL」；本单不改 SOP 句子，
避免告警尚未上线时把执行者指向不存在的信号。

## Alternatives

- 规则侧 `absent_over_time` / 出生安全 `increase` 写法、不预置 Counter：能盖「无数据」，但重启后
  「明确停转」在首个成功之前仍不可判；本单需要停转与无数据分开，故选代码侧预置。
- 使用 `stability_apscheduler_job_runs_total{job_name="device_lease_reconciler"}`：否决。
  `_with_leadership` 在非主节点跳过执行时仍记 `success`。
- 单条告警用 `or absent(...)` 合并两态：否决。与 Planner「不得合并」及 #3532 可读性要求冲突。
- 本单一并预置 #3500 盘点的全部带标签 Counter：超出范围；只做本信号两条子序列。

## Verification

- 前检：开放 PR 队列为空；无 PR 改动本单三文件；#3500 仍 OPEN、无 assignee、无开放 PR。
- 云端实施（M2）：按执行契约 §3.6 不 declare Registry。
- promtool 3.13.3（CI 同版本、同 tarball sha256 pin）：`promtool test rules deploy/prometheus/alerts-stability-platform.test.yml`。
- 七场景：持续无运行、持续报错、先报错后恢复、序列从未出现、采集中断、全程成功、序列出生即为 N
  （另含从 0 起步增长的修复后对照）。
- 「序列出生即为 N」：`_ _ 2 2…` 上 `increase()>0` 为 0（修复前失明）；`0 0 2 4…` 上为 1（预置后可见）。
- pytest：`scripts/run_pytest.py tests/test_reconciler_runs_precreate_3660.py tests/test_prometheus_alerts_contract.py -q`
  → 11 passed, 48 skipped（promtool 相关用例在本机有 promtool 时另由全量场景覆盖）。
- 「序列出生即为 N」负向：对 `_ _ 2 2…` 期望 `increase()>0 == 1` → promtool FAILED（got 0）；
  对 `0 0 2 4…` 期望 `increase()>0 == 1` → SUCCESS。
- `check:quick`：16 gates 通过；`schema-at-head` 因无 `DATABASE_URL` 按设计跳过。

## Revisit

- 告警在控制面 Prometheus 落地并对照一两次真实停转/无数据后，把 SOP G1 第 10 步改为先查本告警状态
  （#3651 Agent Note Revisit 已登记）。改句时遵守 writing-conventions.md（ADR-0059）。
- #3500 若改为统一规则侧出生安全或统一预置框架，复查本两条子序列是否重复或冲突。
