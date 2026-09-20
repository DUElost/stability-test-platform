# #2882 dashboard compute 超时后的在飞判据与执行器隔离

Status: implemented
Class: bug-fix

## Decision

#2799 给 dashboard_summary 的 compute 加 `asyncio.wait_for` 超时是对的，但它只终结
「等待」，终结不了线程：`to_thread` 里的 compute 仍自持 `SessionLocal` + 一条**全进程
共享默认执行器**的线程继续跑；超时走 `_schedule_retry()`，而旧串行化判据看的是
`_flush_task`——flush 早已因超时收尾，判据形同虚设；心跳又持续把 `_failure_streak`
清零，退避上限永远到不了 ⇒「持久挂起 + 持续心跳」下线程按重试节奏增殖，挤占
heartbeat/logs/SAQ 的 to_thread 名额与池连接。

采单内两条建议的**组合**（互相补强，非二选一）：

1. **专用单线程执行器**：`run_in_executor(_get_executor(), ...)`，`max_workers=1`、
   懒建、`_reset_for_tests`/`shutdown` 时 `shutdown(wait=False, cancel_futures=True)`。
   挂起 compute 不再污染共享默认执行器（lifespan 已有
   `shutdown_dashboard_summary_publisher` 接线，`main.py:301-304`，退出序列不会被
   挂起线程拖住）。
2. **在飞判据绑定线程真正收尾**：`wait_for(shield(fut))`——shield 使超时不取消
   future，`fut.add_done_callback(_on_compute_done)` 因此成为「线程结束」的唯一信号。
   `_arm_flush_cb` 在旧 `_flush_task` 判据后新增 `_compute_future is not None and not
   done → 跳过武装（WARNING）`；被跳过的武装不擦 `_dirty`，`_on_compute_done` 在
   脏标记仍在时补 `_arm_flush(0.0)`——挡住增殖的同时不丢唤醒（#2799 收口语义保留）。
   回调里先消费 `fut.exception()`，线程迟到抛错不再产生 never-retrieved 噪声。

## Alternatives

- 只做在飞标记、不换执行器：增殖被挡住，但**已经**挂起的那条线程仍占共享默认执行器
  名额（单内主诉之一），且超时到线程收尾之间的窗口里其他 `to_thread` 路径被挤。弃。
- 只做专用执行器、不加在飞判据：单 worker 会把重试 compute 排队而非并行——线程数不涨
  了，但队列随「超时+心跳」无限加深，挂起恢复瞬间集中补算 N 次全量聚合。弃。
- 用可取消的任务代替线程（把 compute 改 async/分片查询）：改动面是查询层重构，远超
  本单残余范围。弃。

## Verification

- `backend/tests/services/test_dashboard_summary_publisher.py`：9 passed（含新增 2 例：
  `test_hang_timeout_does_not_multiplicate_under_heartbeats`——挂起期间 6 次心跳后
  `len(calls)==1`、release 后补推 `calls==2`+broadcast≥1；
  `test_compute_runs_on_dedicated_executor`——spy `loop.run_in_executor` 断言 executor
  是 `pub._get_executor()` 单例且非默认池）。
- `test_dashboard_summary.py` + publisher 套件：22 passed；既有用例（#2447 退避、
  #2447 串行化、#2799 超时出口/丢唤醒收口）全部原样通过——超时路径断言
  （`_failure_streak>=1`/`_flush_task is None`/broadcast==0）与在飞判据兼容。
- 变异自证（每步清 `__pycache__`，还原后套件复绿）：① 摘掉 `_arm_flush_cb` 的在飞
  判据 ⇒ `test_hang_timeout_does_not_multiplicate_under_heartbeats` 红（其余 8 例仍绿，
  说明判据独立可判别）；② `run_in_executor(None, ...)` 换回默认执行器 ⇒
  `test_compute_runs_on_dedicated_executor` 红。

## Revisit

- 若生产观测到 compute 常态超时（说明聚合本身要拆/加索引），本模块的「单 worker +
  超时重试」形态要重新评估——那属于 compute 性能单而非本单范围。
- `_on_compute_done` 的补推只认 `_dirty`；若将来把推送做成带序号/代际的幂等广播，
  这里需要一并检查「跳过的武装」是否要保留触发源信息。
