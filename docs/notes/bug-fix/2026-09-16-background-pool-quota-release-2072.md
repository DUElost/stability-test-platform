# 后台池配额归还改挂 future（#2072，#1122 残留）

Status: implemented
Class: bug-fix

## Decision

`submit()` 的配额归还点从「任务体的 `finally`」改为「future 的 done 回调 +
离开函数时未交接则就地归还」，并写死一条不变量：

> 离开 `submit()` 时，配额要么已经交给 future，要么已被归还——两者恰有其一。

原实现只有任务体一个归还点，而任务体有两条**不执行**的路径：

1. `pool.submit` 抛「非 shutdown」错误 → 直接 `raise`，没排上队也没归还；
2. `shutdown(wait=True, timeout=N)` 宽限期后的 `cancel_futures=True` → 被取消的
   排队 future 永不执行任务体。

`_queue_slots` 是模块级信号量、跨 pool 重建存活（文件里原注释自述「shutdown 重建
pool 不清空配额」），因此两条路径都是**进程级累积**泄漏：容量单调下降，到 0 后所有
后台提交恒抛 `PoolQueueFullError`，而调用方（`notification_service` 降级、
`post_completion`）的处理是「记 warning 后丢弃」= 静默丢后台工作；`snapshot()` 的
`rejected_total` 只反映拒绝次数，看不见「配额被蚕食」。

实现细节：

- `fut.add_done_callback(_release)`：正常结束、任务抛错、被取消三种终结形态都会
  触发 done 回调（3.9+ 语义，本机 3.13 实测：`shutdown(cancel_futures=True)` 取消
  的排队 future 回调收到 `(cancelled=True, done=True)`）；
- 提交失败的分支不再各自补 `release`（那正是「散点式补漏」的老毛病），统一由
  `finally: if not handed_off: _release()` 兜住**所有**未交接路径，包括重建 pool 后
  第二次仍被 shutdown 拒绝的窄路径；`handed_off` 为假时 done 回调必然尚未挂载，
  不存在二次归还；
- 第二次尝试**复用**已持有的配额（不 release 再 acquire）：语义是「同一次提交尝试
  占一格」，重试不额外占用，也就不会在满负荷下自己把自己挤出去；
- `shutdown()` 本体一字未改（问题不在停机时序，在归还点）。

## Alternatives

- **只在两个失败点各补一次 `release()`**：否决。归还点仍随失败形态增长——以后任何
  新增的提前 return/raise 都会重新漏；`handed_off` 的不变量写法让「未交接即归还」
  成为结构性保证，不依赖列举失败路径。
- **给信号量加「按 job id 记账」的回收器**：过度设计。配额本来就是「一次提交一格」，
  done 回调与提交一一对应，不需要额外账本。
- **把 `MAX_QUEUE` 调大或改成无界**：否决，那是回退 #1122 的有界前提。

## Verification

`backend/tests/core/test_thread_pool.py` **5 passed**（新增 2 条），且对**旧实现红**：

- `test_repeated_submit_failures_do_not_erode_capacity`：旧实现在 200 次失败提交后
  抛 `PoolQueueFullError: background pool queue full (max_queue=200)`（实测报错），
  即「容量被蚕食完」的直接现形；新实现 210 次失败后仍能把真任务跑完、`drain()` 归零。
- `test_shutdown_cancelled_futures_release_slots`：旧实现 5 条被取消的排队任务全部
  漏归还 → `drain()` 超时、`queue_depth()` 停在 5；新实现归零。
- 既有 3 条（正常归还 / 任务抛错归还 / 满队列拒绝计数）未改动且通过。
- 关联影响：`drain()` 是 conftest 清库前排空守卫（#2074）的判据，泄漏会让它等不到
  零 → 本单同时消掉一类「测试里偶发 PoolQueueFullError」的成因。
- `scripts/run_gates.py check:quick`、`ruff`：见 PR 检查。

## Revisit

- 生产侧 `shutdown(timeout=...)` 目前**无调用点**（`backend/main.py` 只关 RunConsole），
  所以本单的可达性主要在「同进程跨用例累积」与将来接入优雅停机时；一旦停机路径真的
  带 timeout 上线，需复核 `_pool = None` 与 `handed_off` 之间无并发窗口。
- 可观测面缺口仍在：`snapshot()` 只能看到 `rejected_total`，看不出「配额被谁占着」。
  若再出现容量异常，出口是给 `snapshot()` 加在途任务标识清单（而不是先加指标）——
  本单不改观测面，避免与 #2144 的指标收口重叠。
