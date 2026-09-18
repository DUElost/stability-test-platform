# #736 切片：活跃 job 占位绑定抽出 `active_job_bindings`

Status: implemented
Class: bug-fix

## Decision

把 `main()` 内 lease-lost / register / deregister 三个占位闭包迁到
`backend/agent/active_job_bindings.py`。`JobRunnerStateSlot` 承接晚绑定的
`job_runner_state`（LeaseRenewer 先于 JobRunnerState 构造的原顺序不变）。

同 PR 棘轮：`main.py` 846 → **825**，封顶 **867**（×1.05）。

## Alternatives

- **先上 `AgentApplication`**：弃——占位绑定是 claim/recovery 共用依赖，先垂直
  搬走更利于后续 recovery 闭包簇。
- **把模块级 occupancy 集合也搬进类实例**：弃——心跳/capacity 闭包仍引用
  `_active_job_ids` 等全局；本刀只搬绑定逻辑。

## Verification

- `pytest` active_job_bindings + lease_lost + recovery_executor：**37 passed**
- `check:quick`（除本机 `schema-at-head`）：pending

## Revisit

- 下一刀：recovery 闭包簇（execute / resume / periodic），或薄壳
  `AgentApplication`。
