# 保留清理：取锁后早退补报窗口 + 批大小下界（#2278）

Status: implemented
Class: bug-fix

## Decision

两处都是 #2104（持锁窗口观测）/ #2105（批大小杠杆）落地后的**残留**，不是新语义：

### 1. 取锁之后的两条早退必须上报 `stability_retention_txn_seconds`

`lock_t0` 在取锁前设置，而三个上报点（两条延迟出批 + 提交后）都在这两条早退
（`_retention_lock_runs` 锁内复核清空、`_retention_safe_ids` 安全集为空）**之后**。
于是「取锁→等待→一无所获」这一类 tick 恰好不进指标——而这一类正是 #2104 存在的
理由：锁序统一（#2022）之后代价的形态从「死锁」变成「等待」，窗口长度是它的上界。
指标漏掉最该看的样本，比没有指标更糟（它会让人以为已经在看）。

失败路径的上报点（`except` 分支，判 `lock_t0 is not None`）此前已存在，本单不动它；
**未取锁**的出口（候选为空，在 `lock_t0` 之前）也**不**上报——空 tick 进窗口指标会
稀释真实观测，这条边界由 `test_no_window_reported_before_any_lock_is_taken` 钉住。

### 2. `plan_run_retention_batch_size` 加下界 1

`int = 100` 无下界：设 0 时 `_retention_candidate_ids` 的
`while len(selected_ids) < limit` 一次都不执行 → 保留清理**静默永久停摆**，而
`stability_retention_candidate_runs` 如实显示 0，读起来像「无积压」。
判据取 `Field(default=100, ge=1)`（响亮的失败），不取消费侧 `max(1, …)` 钳制：
后者会把「配置写错」伪装成「按预期在跑」，与本单要消灭的失效模式同型。越界 env 在
取值处即 `ValidationError`，保留清理作业显式失败并计入 `apscheduler` 错误指标面。
「想少删」的正确旋钮是 `PLAN_RUN_RETENTION_DAYS`（保留期），不是把批大小归零。

## Alternatives

- **只在文档里写「不要设 0」**：否决——#1958/#2104 这一串的教训就是「靠人记住的约束
  会失效」；既然 pydantic 边界是零成本的，就用它。
- **给 Settings 加启动期校验（boot 即拒）**：本层是惰性 `lru_cache` 视图（ADR-0042 D4
  明确控制面当前不热更），把校验搬到启动期要新增一个「所有域都实例化一遍」的钩子，
  影响面大于本单必要性。当前失败点仍在**第一个使用它的 tick**，且带明确 error 名，
  够响亮。再评估条件见 Revisit。
- **窗口上报改成 `try/finally` 一把罩**：否决。代码里已有显式告诫
  「不要把它挪到提交后的 console log 清理之后——那不是持锁窗口」；`finally` 罩到
  `with SessionLocal()` 结束会把提交后的文件操作计入窗口，改的是**指标语义**而不是
  补漏报。逐出口上报保持窗口终点=事务结束（提交/回滚），与既有三个上报点一致。

## Verification

`backend/tests/scheduler/test_retention_cleanup.py` + `tests/test_settings_scheduler.py`
对**旧实现 3 failed / 32 passed**，对**新实现 35 passed**：

- `test_window_reported_when_lock_recheck_empties_batch`（路径 ①，旧实现红）
- `test_window_reported_when_all_candidates_kept_by_refs`（路径 ②，旧实现红；
  该出口在真实数据下只能由并发插入引用触发——`_retention_candidate_ids` 已在 SQL 层
  用 `~referenced` 预排除，故用例钉的是**出口接线**，链引用计算本身由 #936 用例覆盖）
- `test_retention_batch_size_has_lower_bound`（0 被拒 / 1 可用，旧实现红）
- `test_no_window_reported_before_any_lock_is_taken`（双向边界，两版都绿：它防的是
  本次修**过头**，不是复现旧缺陷）
- 这是 `record_retention_txn` **首次**被测试钉住（此前 `git grep` 于 `backend/tests`
  与 `tests/` 零命中——指标加了两周无人断言过它会上报）。
- `tools/dev/env_inventory.py --write` 重刷生成块（`backend/core/settings/scheduler.py`
  行号漂移）；`--check` 一致（218 个读取名），`Field(default=…)` 形态被清单解析器
  正常识别（默认值列仍为 `100`）。
- `python scripts/run_gates.py check:quick`、`ruff`：见 PR。

## Revisit

- 同域其它旋钮是否也属「0 = 静默停摆」型（`recycler_batch_size`、
  `patrol_stall_batch_limit`、`*_interval_seconds` 的 0/负值语义）：本单只处理被
  指认的批大小，未通扫。通扫的判据应是「该值是否进入 `while … < limit` 或周期除法」，
  而不是「一律 ge=1」——间隔类 0 的含义是「每 tick 都跑」，与批大小的 0 不同义。
- 若将来给控制面加配置热更（ADR-0042 D4 的将来时），启动期一次性校验所有域 Settings
  就变成必需项，届时把本单的失败点从「第一个 tick」前移到「启动/热更时」。
