# 助手轮次投递语义收口：去重≠失败 / 异步失败必须有回执（#2071 / #2073）

Status: implemented
Class: bug-fix

## Decision

两单同一面：**`enqueue_sync` 的返回值不是「投递成功」的同义词**，而两个调用点各自
把一种失败形态读错了方向——

- #2071（P1）把**设计内的正常去重**读成投递失败；
- #2073（P2）把**稍后才发生的真实失败**读成成功。

同处一文件族（`orchestrator.py` + `ai_assistant.py` 路由），故一单收口。

### #2071：轮次内联终态**不再尝试入队**续轮

`_finalize_action` 无条件 `_enqueue_continuation`。当动作在**本轮 job 内**终结
（T1 `runconsole` 工具 `RunKeyBusyError`/spawn 失败、白名单服务型工具、T2b 自动
放行的 `dispatch_plan_run`）时，本轮 job 仍持 `ai-turn:<session>` 的 key，SAQ 的
Lua 入队对已存在 key 恒返回 `nil`（`saq/queue/redis.py:447-471`，`Job.id` 由 key
派生）→ 必被去重。#1216 的 `required=True` 语义因此把每条内联路径都判成失败：

```
_converge_pending(failed) → 该行脱离 pending/running → 本轮末尾清理只处理 pending/running
                          → 永久红色「入队失败」气泡，与正确答案同时存在
```

修法不是在失败判定上打补丁，而是**取消这次入队**：内联路径上本轮自己会把真实结果
喂回模型（旧代码注释已写明），续轮任务本就是多余的。判定用 ContextVar 登记的
「本进程正在执行的本轮会话集合」：

```python
_turn_round_sessions: ContextVar[frozenset[int]]
def _in_own_turn_round(session_id) -> bool   # 命中 → 记 info 日志后直接 return
```

`asyncio.to_thread` 复制当前上下文，所以轮次内 `execute_action → _finalize_action`
读得到标记；RunConsole reader 线程（动作异步完成）、审批路由线程读不到 → 仍走
#1216 的 `required` 真实投递 + 有界重试 + 失败可见。**外部动作完成后的续轮投递语义
一字未改**（`test_continuation_enqueue_failure_marks_placeholder_failed` 仍在）。

顺带消除的三项同源代价：`auto_continuation_count` 在入队**之前**自增导致的预算空耗
（#1227 的 20 次上限被内联路径白吃）、每次内联终态固定 2×0.5s 的重试睡眠、
以及一轮多动作时的重复占位翻转。

### #2073：`on_async_failure` 接上，占位必有终态

首轮提问路由用 `required=False`：返回 True 只代表「协程排上了事件循环」，Redis 故障
发生在之后 → 该分支下 `if not enqueued: 503` 对 Redis 故障**不可达**，占位永久
pending（前端 `hasPending` 每 2s 无限轮询 + #1223 守卫锁死会话 + 用户无重试入口）。
用 #1555 已提供的机制收口：传 `on_async_failure`，把占位收敛为 failed 并把错误
写入 `meta.error`。回调在事件循环上跑，故收敛动作交给后台线程池（`submit`），
池满时降级为就地收敛；回调自身绝不抛。

不改成 `required=True`/`async def` 路由（issue 列出的另一选一）：那会把 Redis 延迟
搬进用户请求的关键路径，且 503 语义下占位仍需在失败时收敛——收口点其实同一个，
但把可观测的异步失败换成同步阻塞请求，代价更大。

## Alternatives

- **只在 `_enqueue_continuation` 里把「去重」与「异常」分开判**（保留入队，失败仅
  不标红）：否决。入队本身就是多余的，保留它等于留着 1s 睡眠、预算自增和一个
  「去重到底是谁造成的」无法区分的老问题——本轮持 key 与其他轮次持 key 在 SAQ 返回值
  上同形，只有调用方上下文知道差异。
- **用 bool ContextVar 而非 session 集合**：否决。若 SAQ 未给每个 job 独立上下文，
  bool 会在并发跑的**其他会话**轮次间串味，把外部续轮误判成内联（=静默不汇报，
  比现在这个误报更坏）。按 session_id 比对后，误判需要「同会话两轮并发」，而这已被
  路由的 409 `ai_turn_in_progress` 守卫排除。
- **给占位加「可重试失败」态 + 前端重试按钮**：本轮不做。#2073 要的是「不再永挂」，
  failed + 文案「请重新提问」已满足；重试入口是产品面（另单）。
- **`admission_pump.py` 的同源读法（#2071 附带影响 2）随本单一并改**：暂不。它是
  另一条链（PlanRun 准入），去重后的 `requeue_plan_run(PRECHECK_STALE)` 是**有界退避
  重排**、会自愈，与助手侧「永久红气泡」不同量级；且该文件在窗另有 Execution 声明。
  登记为 Revisit。

## Verification

- 新增 `test_inline_finalization_does_not_report_bogus_enqueue_failure`（#2071）：
  对**旧实现红**（实测 `assert [{'key': 'ai-turn:1', 'required': True, ...}] == []`
  失败，3 次多余入队尝试），对**新实现绿**（同时断言无 failed 气泡、占位仍由本轮
  末尾收口、终答照常产出）。
- 新增 `TestTurnEnqueueAsyncFailure`（#2073）两条：异步失败回调必须存在且收敛占位
  （对**旧实现红**：`cb is None`）；同步 `False` 路径的 503 + failed 不回退。
- `backend/tests/api/test_ai_assistant_endpoints.py` 全量 **60 passed**；
  #1216/#1227 的既有断言（外部续轮失败仍可见、超预算不再入队）未改动且通过。
- `python scripts/run_gates.py check:quick`、`ruff`：见 PR 检查。

## Revisit

- **`admission_pump` 的同 key 去重语义**（#2071 附带影响 2）：是否引入
  `enqueue_sync(..., dedup_ok=True)` 让「已在队列里」不再触发退避重排——需要与
  #1274 的收敛器语义一起判，别只改一处。
- 若将来允许同一会话并发跑两轮（去掉 409 守卫），本处的 `_in_own_turn_round`
  判据即失效，必须换成显式的 job-key 持有者比对（拿 SAQ job id/owner），不能退回 bool。
- `auto_continuation_count` 仍是「按尝试计数」。真实投递失败（Redis 故障）也会计数；
  若出现「因基础设施抖动提前停链」的抱怨，出口是把自增移到入队成功之后并配 CAS。
