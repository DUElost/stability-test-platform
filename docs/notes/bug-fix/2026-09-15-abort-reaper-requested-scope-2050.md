# #2050 abort reaper 不读 requested_job_ids：host 级 abort 连带判死其他主机的正常 job

Status: implemented
Class: bug-fix

## Decision

`abort_plan_run(host_id=H)` 只把**该主机**的 PENDING/RUNNING job 放进
`abort_requested.requested_job_ids`（`plan_run_abort.py:394-408` 的 `scoped_rows`），
但写的是 **run 级** `abort_requested.at`。而 abort reaper 的候选判据此前只有
「存在 `at` + grace 已到」：

```python
# backend/scheduler/device_lease_reconciler.py:442-450
.where(
    JobInstance.status == JobStatus.RUNNING.value,
    abort_at_text.isnot(None),
    abort_at_text.cast(TIMESTAMP(timezone=True)) < grace_deadline,
)
```

`_abort_reaper_recheck_job()` 也只复核 `status == RUNNING`，无任何归属校验 → 同 run 上
**从未被请求中止**的其他主机 RUNNING job 会在 `ABORT_ACK_GRACE_SECONDS`（默认 60s）后
被打成 UNKNOWN；`state_machine` 只允许 `UNKNOWN → {RUNNING, FAILED}`，迟到的 Agent
`COMPLETED` 也落 FAILED，且 UNKNOWN 期间保留 ACTIVE lease 占着设备——与
`abort_plan_run` docstring 的契约（"other hosts' jobs … left unchanged"）直接矛盾。
触发面是常态：一次主机热更新（`abort_jobs_for_host`，`abort_running_jobs=True`）× 多主机
fleet run。

决定：**给 reaper 的候选面加归属判据**——`requested_job_ids` 存在且非空时，只回收
名单内的 job（`_abort_request_covers_job()`）。键缺失/畸形（历史 run_context）时**不参与
过滤**，保持既有行为，避免老数据里未写集合的 run 永远无人回收。

**与 #1928 的关系（边界）**：#1928 已删除死函数 `_record_host_abort_request`，并裁定
「要按 host 独立 grace，先立 ADR 裁决 reaper 消费语义」。本 PR **不动 grace 语义**——
宽限仍是 run 级、仍由每次 host 级 abort 重置（`abort_plan_run` 的 docstring 已按 #1928
注记写明）；收窄的是**候选集**（不再回收没被请求过的 job），这是「实现与契约对齐」，
不是新增 per-host grace。若 owner 要的是「grace 从第一次请求起算 / 按 host 独立宽限」，
仍需 ADR——本单不越界。

## Alternatives

- **只改 docstring（承认 run 级语义）**：危害（误杀其他主机的正常 job、设备被 UNKNOWN
  lease 占住、整轮 run 被拖成 FAILED）仍在，属"文档化 bug"而非修复——否决。
- **host 级 abort 干脆不写 run 级 `abort_requested`**：连该主机自己未被 ack 的 job 也
  失去回收，RUNNING 悬挂 + lease 不释放，比现状更糟——否决。
- **per-host grace（`abort_requested_hosts[host].at` + reaper 按 host 消费）**：#1928
  明确要求先立 ADR；超出本单范围——留 Revisit。
- **在 SQL 里做 JSONB 数组包含**：候选面已被「RUNNING + abort_requested」限定，行数有界；
  Python 侧过滤可同时覆盖 PG 原生与降级（fallback）两条查询路径，且与既有
  `_abort_at_expired` 的同款写法一致——采用。

## Verification

- `python -m pytest backend/tests/scheduler/ backend/tests/services/test_plan_run_abort_aggregator_race.py backend/tests/api/test_plan_run_abort_api.py -q`
  → **132 passed**（含新增 3 例）
- 新增用例（`backend/tests/scheduler/test_abort_reaper.py`）：
  - `test_host_scoped_abort_spares_other_hosts_running_jobs`：同 run 两台主机各一个 RUNNING job，
    `requested_job_ids=[host1 的 job]` → 只回收它，host2 的 job 仍 RUNNING 且 `ended_at is None`；
  - `test_run_level_abort_reaps_every_requested_job`：名单含全部 → 全回收（run 级行为不变）；
  - `test_legacy_abort_without_requested_ids_still_reaps`：**不写**该键（历史形态）→ 仍全回收
    （兼容性护栏：防止把老数据改成"无人回收"）。
- **反事实验证**：把 `device_lease_reconciler.py` 还原为改动前 → `test_host_scoped_abort_spares_other_hosts_running_jobs`
  **FAILED**（1 failed / 8 passed）；恢复后 9 passed。
- `python scripts/run_gates.py check:quick` → 见 PR 描述。

## Revisit

- **残留窗口（明确记录）**：host 级 abort **之后**才被 claim 成 RUNNING 的**该主机** job
  不在名单内 → 不再被 reaper 回收（改由 upgrade gate / 正常完成处理）。run 级 abort 的
  claim 竞态由 `plan_run_abort.py:539` 的 `requested_job_ids` 刷新覆盖；host 级是否也要在
  claim 路径并入名单，属 reaper 消费语义的一部分，与「per-host grace」一并等 ADR。
- 若 owner 选择「按 host 独立 grace」：改动面是 `abort_requested_hosts[host_id].at` +
  reaper 按 host 取 `at`，本 PR 的候选面判据可原样复用。
- 上游跟踪：#1928（死函数删除与 grace 重置语义）、#1880（host 作用域错位）。
