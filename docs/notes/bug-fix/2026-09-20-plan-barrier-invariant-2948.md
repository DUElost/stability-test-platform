# barrier 跨参数不变式进 Plan 写入边界（#2948）——并堵上派生出的第二洞

Status: implemented
Class: bug-fix

## Decision

不变式 `barrier(_timeout|_max_wait) >= coordinator_heartbeat_timeout` 已有单一
真值源（`adr0026_params.validate_param_invariants`，env 侧）；Plan 侧不复制
比较逻辑文案，而是**在 `_validate_assembled_lifecycle` 里以同一 `_sched()`
基准做等价校验**（recycler 判死用同一个 `coordinator_heartbeat_timeout_seconds`
——三处共源，不出现「写入边界用 300、执行侧用 env 覆值」的第二套真值）。
错误码 `INVALID_BARRIER_CONFIGURATION` 带 `field` + 当前窗值 + 两条出路
（调大/留空，`barrier_max_wait` 留空=不设硬顶的 #117 语义在 message 里写死）。

**实现时发现的第二洞（票面之外）**：PUT 局部更新的 elif 分支条件集是
`{patrol_interval, timeout, barrier_timeout}`——**漏了 `barrier_max_wait_seconds`**，
且该调用漏传第 5 参：即便加了校验器，「只改硬顶」的 PUT 也依然全绿通过。
本单同时修条件集与传参，并给它独立用例（`test_put_only_max_wait_still_validated`
——不修则此例恒红，这是洞被钉住的自证）。

两条几何决定记在案：
- `value == coord_timeout` **放行**（`<` 才拒，与 env 侧不变式同形——存活窗
  恰好等于 barrier 仍是「活得过判死」的非退化态）；
- 只测**非 None** 字段：`barrier_timeout=None` = Agent 回落 env（env 侧自会
  校验其不变式），`max_wait=None` = 无硬顶（#117），两者都无语可违。

## Alternatives

- **把校验塞进 pydantic `model_validator`（PlanCreate/Update 层）**：弃——
  coord_timeout 来自运行时 settings，模型层引入 IO 依赖会把 schema 单测
  全部拖进 settings 语境；且 PUT 局部更新（fields_set 语义）在模型层无法
  区分「未提供」与「提供 null」；
- **直接调用 `validate_param_invariants` 全量清单**：弃——Plan 只暴露 barrier
  两参，其余 9 参是 env/控制面领地；混用会把 Agent 启动失败类错误码引到
  Plan API 上，语义错位（保持 env 与 Plan 各自只判自己可触达的参数）。

## Verification

- 新用例 6（POST 双拒/边界放行/PUT 全量/PUT 只改硬顶/合法值往返）全绿；
  `test_plans_api` 72 例无回归（既有测试 barrier 全为 None/≥300，校验不伤存量）；
- 邻居批（schedules/dispatcher/abort）→ 见 PR checks。

## Revisit

- 若 Agent 侧允许 per-plan coord_timeout 覆写（目前无此面），本校验的基准要
  改为「该 plan 生效窗内的实际 coord 值」——那时 `_sched()` 单点即断，届时
  把两处（env/Plan）收敛到一个 resolver，别平行加第三份；
- #872/#174 的前科是「误杀」方向（barrier 太短丢批次）；反向（barrier 超长生死
  倒挂）不在本不变式射程，若出现实例再议上界。
