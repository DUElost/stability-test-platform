# Agent 套件裸等待批 4（收尾）：可改的已改完，其余是场景搭建或否定断言（#2602）

Status: implemented
Class: bug-fix

## Decision

本批 **改 2 处、并把剩余站点逐条定性**——「裸等待」这一族在 Agent 套件里到此**挖尽**：

**改（2 处）**：

| 站点 | 原写法 | 现在等什么 |
|---|---|---|
| `test_main.py` 心跳用例 | `ht.start(); sleep(0.35); ht.stop()` | `mock_send_hb.call_count >= 2`（**已发出 ≥2 次心跳**） |
| `test_local_disk_monitor.py` 溢出守护 | `sleep(0.2); assert thread.is_alive()` | `check_spy.call_count >= 1`（**确实跑过一轮检查**——原来线程若还没被调度起来，`is_alive()` 会随机为假） |

**不改（逐条定性，写在这里备查）**：

| 站点 | 定性 |
|---|---|
| 两个 `*progress_stamps_1690*` 的 `time.sleep(0.05)` | **场景搭建**：sleep 在 `with progress_heartbeat("work", interval=0.01)` **内部**，用来制造"慢段"让周期心跳发生（断言就是"慢段应有周期心跳"）。我的扫描器按"邻近断言"把它算成裸等待，属误判。 |
| `test_artifact_uploader.py` 的两处 `sleep(0.05)` | 场景搭建（让首条离开队列，后续才会因满而丢）；无可等计数器（批次 3 已改注释说明）。 |
| `test_device_watcher.py` 的 `sleep(0.25)` | **否定断言**："停止后不应再重连"——非事件没有正向可等的量；保留固定窗口即保留该判据的强度。 |
| `test_main.py:156` 的 `sleep(0.1) # Let it start` | 让线程有机会起来后再 stop（断言是"stop 后线程已死"）；无"已起来"的观测量，属测试强度。 |
| `test_step_stall_detection.py` 的两处 | 与批次 0（#2577/#2584）同一文件：`wall_clock=None` 下的"不该死"用例，无判定窗口。 |

## Alternatives

- **A. 把否定断言也"确定化"（等一个恒不发生的量）**：不可能也不必要——判据本身依赖"给足时间后仍无动作"；
  真要确定化需要发布器/看护器暴露"再也不会做 X"的观测量（产品代码改动），不在一批测试改造里做。
- **B. 把 `*progress_stamps_1690*` 的 sleep 也换掉**：否决。那不是等待，是**被测语义的一部分**
  （"慢段"必须存在，心跳才会发生）；换掉等于删掉用例的一半。
- **C. 继续按扫描器的清单逐条改**：本批到此为止——清单剩余项经逐条阅读**没有可改的**，
  继续机械执行只会产生"为改而改"的 diff。

## Verification

- **测试**：`test_main.py` + `test_local_disk_monitor.py` → **41 passed**，**连跑 3 轮全绿**（每轮 1.8s）。
- **全量**：`backend/agent/tests/` → **2130 passed / 70.7s**（无回归）。
- `ruff check` 通过。
- **一次自查**：`local_disk_monitor` 那处我给 `patch.object(...)` 补了 `as check_spy` 绑定
  （原 patch 未绑定名字，直接引用会 NameError）——跑测试时立刻暴露。

## Revisit

- **本族的收尾判据**（四批下来稳定）：固定等待只有两种合法形态——**(a) 等可观测条件 + 上界**、
  **(b) 场景搭建并在注释写明"为什么没有可等的量"**；其余都是缺陷。
- **若发布器/看护器将来暴露更多观测量**（如"已取走计数"、"线程已退出"），上表里"不改"的几处
  可以再收一轮——那时是产品代码带来的新条件，不是测试侧的欠账。
- **`backend/tests/`（控制面）**：同一判据扫过（#2595），当时只处理了 RunConsole 的 5 处；
  若将来再扫，按本族判据（含"场景搭建"分支）分诊，不要机械替换。
