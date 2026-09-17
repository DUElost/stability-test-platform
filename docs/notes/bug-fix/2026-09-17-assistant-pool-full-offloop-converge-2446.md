# AI 助手池满兜底：占位收敛移出事件循环线程（#2446）

Status: implemented
Class: bug-fix

## Decision

#2073 给首轮提问的异步入队失败接上了唯一回执出口（`on_async_failure` 收敛占位），
但该回调跑在**事件循环线程**上，兜底分支 `except PoolQueueFullError` 却直接调用
同步的 `fail_pending_placeholders`（`SessionLocal()` + 查询 + commit）。触发条件
恰好是「Redis/SAQ 入队失败 **且** 后台线程池已满」——系统最忙、事件循环最不该被
阻塞的时刻，注释自己写明的约束（「不宜做同步 DB 写」）被这一支踩中；单进程控制面
期间所有 HTTP/WebSocket 停摆，并叠加 `QueuePool` 争用（同类现场 #703）。

修法：池满时改由 `_converge_placeholder_off_loop` 把收敛交给**事件循环的默认
executor**（`asyncio.to_thread`），与已满的后台池相互独立、不占循环线程；任务进
本模块既有的 `_BG_TASKS` 强引用集（create_task 结果无人持有时可能被 GC），done
回调复用 `_bg_task_done`（新增加 `label` 关键字参数，默认值保持既有调用方日志不变）。

- 首选仍是后台池 `submit`（有界、可观测），**仅在池满这一支**降级到默认 executor
  ——两者都不在循环线程上执行；
- #2073 的「占位必有终态」语义一字未改：收敛动作照做，只是换了执行线程；
- `asyncio.to_thread` 与本文件既有先例同形（审批路径
  `_decide_action`：`create_task(asyncio.to_thread(execute_action, ...))`）。

## Alternatives

- **只记日志 + 置内存标记，交给周期收敛**（issue 建议 ①）：否决。代码里没有
  任何周期收敛器——`_converge_pending` 只由轮次任务自身的 `finally` 与本次回调
  调用；Redis 真故障时那一轮任务根本不存在，标记无人消费，占位重新变回永久
  pending（正是 #2073 修掉的形态）。要成立得先新建一个清扫器，代价远大于本修。
- **为兜底单配专用 worker**（issue 建议 ③）：否决。为极窄的「池满 + Redis 故障」
  交叉场景常驻一组线程，收益不抵复杂度；默认 executor 已提供独立线程。
- **`await asyncio.to_thread(...)` 直写在回调里**（issue 建议 ② 的字面形态）：不可行
  ——回调签名是 `Callable[[BaseException], None]`（同步，`enqueue_sync` 的 `#1555`
  契约），不能 await；故以 `create_task` 承载同一语义。
- **让 `submit` 阻塞等待槽位**：否决。等槽位本身就意味着在循环线程上等待，
  与要修的问题同形。
- **顺带把 `notification_service` 的同源降级路径一起改**：不改。
  `_fallback_to_pool`（`#1555`）的失败分支不做 DB 写，不受本缺陷影响。

## Verification

- 新增 `test_pool_full_fallback_offloads_instead_of_blocking_loop`（路由接线，同步用例）：
  打桩 `submit` 恒抛 `PoolQueueFullError`，断言 `_converge_placeholder_off_loop`
  被调用且携带 session_id 与错误文案。
- 新增 `test_pool_full_fallback_converges_off_loop`（执行线程 + 终态，async 用例）：
  在真实事件循环里调兜底，断言收敛函数**不在循环线程上**、其线程上无运行中的
  事件循环，且占位仍收敛为 `failed` 并写入 `meta.error`。
- **反例构造（先证伪再采信）**：
  - A（把兜底改回 `fail_pending_placeholders(...)` 直调）→
    `test_pool_full_fallback_offloads_instead_of_blocking_loop` **FAILED**；
  - B（把 `_converge_placeholder_off_loop` 内部改成同步直调）→
    `test_pool_full_fallback_converges_off_loop` **FAILED**。
  恢复实现后两者转绿。
- 实测命令与结果：
  - `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
    backend/tests/api/test_ai_assistant_endpoints.py -q` → **62 passed**（含既有
    `TestTurnEnqueueAsyncFailure` 两条，未改动）；
  - `python -m ruff check backend/api/routes/ai_assistant.py
    backend/tests/api/test_ai_assistant_endpoints.py` → All checks passed；
  - `python scripts/run_gates.py check:quick` → 见 PR 检查记录。

## Revisit

- 默认 executor 的线程上限是解释器默认（`min(32, cpu+4)`），不在本模块控制内。
  若将来出现「Redis 故障 + 池满」长时间持续并伴随大量会话（每会话一次收敛），
  再评估给这条路径单独的**有界**执行器或合并成单次批量收敛。
- 优雅停机时 `_BG_TASKS` 里的收敛任务若被取消，占位会留在 pending。窗口极窄
  （停机 × Redis 故障 × 池满），且下次用户重问即收敛；若线上出现该形态，出口是
  在停机序列里 `asyncio.gather(*_BG_TASKS)` 有界等待。
- 回调依赖 `enqueue_sync` 的「回调在事件循环上执行」契约（`saq_worker` docstring）。
  若该契约改变（回调改到工作线程），`asyncio.get_running_loop()` 会抛
  `RuntimeError`，被外层 `except` 记为 `ai_turn_placeholder_converge_failed`
  ——届时本兜底应改为直接同步调用。
