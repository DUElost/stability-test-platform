# 后台池配额：把「取池/建池」也纳入归还守卫（#2072 重开残余）

Status: implemented
Class: bug-fix

## Decision

本单是我上一轮 #2297 留下的洞，重开说明点得很准：我写下不变量「离开 `submit()` 时配额要么
已交给 future，要么已归还」，但**取池/建池那段在守卫之外**，于是「建池失败」这条路径谁都不
归还。修法只有一句：把 `with _pool_lock: if _pool is None: _pool = _new_pool()` 整体挪进
`try` 内，让 `finally: if not handed_off: _release()` 真的覆盖所有未交接路径。

把「任务体不执行」的路径**列全**写进 docstring（原来只列了两条，重开后又补两条）：

| # | 路径 | 归还由谁保证 |
|---|---|---|
| 1 | `pool.submit` 抛「非 shutdown」错误 | `finally`（`handed_off` 仍为 False） |
| 2 | `shutdown(cancel_futures=True)` 取消排队 future | future 的 done 回调 |
| 3 | 被 shutdown 拒绝后重建、第二次仍被拒 | `finally` |
| 4 | **`_new_pool()` 自己抛**（`BACKGROUND_POOL_SIZE` 非正数 → `ThreadPoolExecutor.__init__` `ValueError`） | 本次收口：挪进守卫后由 `finally` 保证 |

第 4 条与本单原症状**同形**（容量单调下降 → 恒 `PoolQueueFullError` → 调用方 warning 后丢弃
= 静默丢后台工作），只是触发源从"提交失败"换成"建池失败"。所以这不是新语义，是同一条
不变量的第二个漏洞 —— 也正是"写了不变量、却没按它检查所有路径"的代价。

两个建池点都要收：第一处（池为 None 时首建）与第二处（被 shutdown 拒绝后重建）。第二处原先
已在守卫内，但同样没人写过它的用例，本次各补一条。

## Alternatives

- **只在 `_new_pool()` 外加 try/except，失败时手工 release**：否决。归还点又变回"散点式补漏"
  ——下一个新增路径照样漏；不变量应由结构保证（守卫罩住全部前置动作），不是靠我记得加一段。
- **把 acquire 挪到"池已就绪"之后再取配额**：否决。那样 acquire 与 submit 之间的窗口会让
  有界性判断失真（#1122 的前提是"满了立刻拒绝"），且要重新处理 `_depth` 与指标的顺序。
- **顺手给 `BACKGROUND_POOL_SIZE` 加 `Field(ge=1)` 式校验**（与本表 #2278 同型）：否决。
  本单要的是"任何建池失败都不蚕食容量"，与"配置是否合法"是两条判据；后者属 #2287 那类
  旋钮下界通扫（判据不同：间隔类 0 的含义与批大小 0 不同义），不该在这里塞进第二条裁决。

## Verification

- `backend/tests/core/test_thread_pool.py` **7 passed**，其中新增 2 条**对旧实现红**
  （回退 `thread_pool.py` 保留用例 → `2 failed`）：
  - `test_new_pool_failure_returns_quota`：连打 `MAX_QUEUE + 5` 次建池失败后，
    `queue_depth()` 不变，且换回真池仍能提交 + `drain()` 归零 —— 直接复刻"容量单调下降至
    永久 `PoolQueueFullError`"这一现形；
  - `test_rebuild_failure_after_shutdown_returns_quota`：第二个建池点（shutdown 后重建）
    抛错时配额已归还。
  旧实现跑这条套件耗时 26s、新实现 1.2s —— 差值本身就是证据：泄漏使 `_depth` 永不归零，
  autouse 的 `_drain_pool` 每轮都要等满 5s 超时。
- 既有 5 条（正常归还 / 任务抛错归还 / 满队列拒绝计数 / 提交失败不蚕食 / 取消归还）一字未改，
  两版皆绿。
- `check:quick`、`ruff`：见 PR。

## Revisit

- `_drain_pool` 这类"等满超时"的隐性成本说明：泄漏类缺陷在测试里表现为**变慢**而不是变红。
  如果以后再动这个池，值得给 `drain()` 的超时补一条断言（等超时发生即说明有路径没归还），
  而不是让它在 teardown 里静静烧 5 秒。
- 配额语义仍是"一次提交一格"，跨 pool 重建存活；若将来支持 `BACKGROUND_POOL_MAX_QUEUE`
  热更，`_queue_slots` 需要一起重建且要定义在途槽位怎么迁移 —— 那是另一件事。
