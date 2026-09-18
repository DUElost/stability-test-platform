# Agent 套件裸等待批（首批）：test_operation_scheduler 改有界轮询（#2602，#2595 同族）

Status: implemented
Class: bug-fix

## Decision

`backend/agent/tests/` 扫出 **26 处「裸 sleep 后紧跟断言/动作」**（vs 循环内安全轮询 48 处、
子进程脚本字符串假阳性 3 处）。**首批修 `test_operation_scheduler.py` 的 6 处**，改为
`_wait_until(pred, timeout, what)`（可观测条件 + 上界 + 超时 `pytest.fail` 带说明）。

**为什么先修这个文件**：
1. 它**本来就有这个惯例**——`while s.waiter_count == 0 and time.time() < deadline` 就在同文件
   第 26 行；其余 6 处只是没跟上，属"同一文件内两种写法"，修它不需要新造抽象；
2. 可观测条件都在调度器的**公开面**（`waiter_count` / `waiting_devices`），逐条对得上：

| 站点 | 原写法 | 现在等什么 |
|---|---|---|
| `test_acquire_blocks_when_at_capacity` | `sleep(0.1); assert result == []` | `waiter_count == 1`（等待者已排队） |
| `test_same_device_double_acquire_denied` | `sleep(0.1)  # ensure device 2 is queued` | `2 in waiting_devices` |
| `test_cancel_waiting_denies` | `sleep(0.1)` 后 `cancel_device(2)` | `2 in waiting_devices`（否则取消是空操作） |
| `test_pending_handoff_promotes_waiter` | `sleep(0.15)` 后断言 `40 in waiting_devices` | 直接等这个断言对象 |
| `test_shutdown_denies_waiters` | `sleep(0.15)` 后 `shutdown()` | `waiter_count == 2` |
| `test_set_max_concurrent_wakes_waiter` | `sleep(0.1)` 后 `set_max_concurrent(2)` | `waiter_count == 1` |

**这个套件在 PR 路径上**（`pr-agent-tests` 的 "Run agent tests"）——裸等待的随机红**直接卡合入**；
今天 #2571/#2579/#2574 的连环红就是这一族（另一条链是 #2577 的 stall 用例，已修）。

## Alternatives

- **A. 一次性改完 26 处**：本单不做。其余 20 处的等待对象各不相同（线程 `join` 型 / SAQ 重试型 /
  回调同步型 / 事件型），机械替换成 `_wait_until(lambda: True)` 之类的假条件比不改更坏；
  逐个核可观测条件是另一批工作量（#2602 登记）。
- **B. 统一 sleep 调大**：否决。同 #2577/#2595：只降概率、拖慢 PR 路径，且掩盖"在赌机器速度"。
- **C. 把 `_wait_until` 提到共享测试工具模块**：本单不做。目前只有两个文件各自定义
  （RunConsole 一处 `_wait_running`、这里一处 `_wait_until`），语义各自贴合；
  等第三、四处出现再抽（避免过早抽象）。

## Verification

- **测试**：`backend/agent/tests/test_operation_scheduler.py` → **15 passed**（14 + 新自证），
  连跑 **5 轮全绿**。
- **副作用（正向）**：该文件从 ~1s 降到 **0.03–0.15s**——有界轮询条件一满足即返回，
  不再白等固定毫秒（PR 路径预算受益）。
- **自证用例**：`test_wait_until_fails_loudly_on_timeout` —— 用永假条件触发超时，断言
  `pytest.fail` 真的抛出；没有它，"把 sleep 换成 `_wait_until`"就只是换个名字的空转。
- **扫描面**：162 个用例文件的三类计数（48 循环内 / 3 字符串假阳性 / 26 裸等待）与
  其余文件的清单记在 #2602，判据同 #2595。
- `ruff check` 通过。

## Revisit

- **其余 20 处**（`test_step5b_integration.py` ×4、`test_puller.py` ×2 等）：触及哪个文件就
  按同一判据处理（#2602 有清单）。
- **抽公共工具的时机**：当 `_wait_until` 的第三、四个副本出现时，再抽到共享模块
  （现在两个副本，语义不同：一个等 `RUNNING`，一个等任意谓词）。
- **扫描口径的盲点**：本判据（亚秒 sleep + 邻近断言 + 是否在循环内）抓不到"睡够久所以从没红过"
  的**假绿**型（例如把竞争窗口睡过去、断言恒真）。那类需要逐一读用例意图，机器筛不出来，
  只在此登记。
