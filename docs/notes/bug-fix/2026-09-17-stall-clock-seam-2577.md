# stall 检测的时钟接缝：把 40ms 竞争窗口从 CI 门禁里拿掉（#2577）

Status: implemented
Class: bug-fix

## Decision

**给 `_pump_process` 的时钟一个可注入接缝**（issue 指定的终态出口）：新增关键字参数
`now: Callable[[], float] = time.monotonic`，函数内 6 处 `time.monotonic()` 全部改走 `now()`
（初始化 / 推进刷新 / 起表 / 轮询 tick / 停滞判定 / 结束时长）。**生产路径默认墙钟，语义零变更**。

**测试侧按 issue 的处方驱动**：新增 `_ScriptedClock`（测试推进）+ `_advance_on_markers`
（守护线程按**子进程的推进标记**前进时钟——子进程每打一行 PROGRESS 就 touch 一个文件）。
于是「推进刷新停滞钟」与「回退/重复不刷新」两种语义跑在**同一时钟节奏**下，判据有判别力
而不是靠真实时间窗。

**改造的 6 条用例**（原先共享同一个 ~40ms 竞争窗口）：

| 用例 | 旧形态的窗口 | 现在 |
|---|---|---|
| `test_steady_progress_stamps_keep_it_alive_past_the_stall_window` | 子进程 0.3s vs stall 0.25 | 每戳前进 0.1 < 0.25 → 确定不过期 |
| `test_progress_lines_reset_the_stall_clock` | 同上 | 同上（并数到 6 次 `on_progress`） |
| `test_plain_output_does_not_count_as_progress` | 普通行 0.3s vs 0.25 | 普通行**不落标记**、子进程保持存活 → 时钟跳到 1.0 → 判死确定 |
| `test_repeated_seq_does_not_reset_stall_clock` | 0.32s vs 0.2 | 只有首戳被承认 → 两拍后判死确定 |
| **`test_regressing_seq_does_not_reset_stall_clock`** | **0.24s vs 0.2（CI 上反复红的那条）** | 同上 |
| `test_legacy_stamp_without_seq_still_resets` | 0.24s vs 0.2 | 每戳都刷新 → 确定不过期 |

**为什么不是别的做法**（issue 已列，此处复述判据）：
- **加大 sleep**：只降低碰撞概率，同时拖慢 agent 套件（该 job 本就在压墙钟）；
- **默认自动重跑**：这条用例要证明的正是「停滞检测在真实抖动下仍能判定」，用重试抹平等于放弃判据；
- **不引入接缝、只改断言**：断言本来是对的，错的是「判定依赖某一拍恰好落在退出前」。

## Alternatives

- **A. 把轮询间隔调得更小（如 5ms）**：否决。窗口变宽但仍存在（真实调度的抖动上限不可证），
  且更密的轮询浪费 CPU；本质问题是「用真实时间窗表达一个可精确表达的判据」。
- **B. 用 mock 掉 `time.monotonic`（monkeypatch 全局）**：否决。读者线程与主线程都要用它，
  全局替换会波及无关代码；显式参数是更小的面（也只有一处默认值）。
- **C. 只改那条 flaky 用例**：否决。同一文件里 6 条共享同一窗口形态，只修一条等于把
  下一次 flake 留给邻居（issue 也点名「两条无关分支同形失败」）。

## Verification

- **确定性**：新用例**连跑 5 轮全部 32 passed**，每轮 6.2s（改造前，停滞未判出时子进程要跑满
  60s：一次失败跑出 **126s**）。
- **回归面**：`backend/agent/tests/` 全量 **2130 passed**（与 CI 同数量级），说明 pump 的
  时钟替换没有改变生产语义。
- **红绿差分**：基线引擎（无 `now` 参数）跑新用例 → `TypeError: unexpected keyword argument 'now'`
  （7 处），换回新实现全绿。
- **过程中自捉的一个真缺陷**：第一版把函数体的替换做成了精确串匹配，漏掉 `_handle_line` 里
  那处（缩进不同）→ 推进把 `last_progress` 重置回**真实墙钟**，与注入时钟相减恒为负，
  停滞永不触发（表现为两条用例失败 + 套件跑满 126s）。这正说明该接缝必须整面替换：
  留一处混用，判据就静默失真。
- `ruff check`、`check:quick`（11 gates）通过。

## Revisit

- **该接缝属 #715（两层时钟 / PROGRESS 打戳与真实停滞检测）的所有权范围**：本单只做「可注入」
  这一最小面，未改语义（`stall_seconds` 的分辨率仍由轮询间隔定）。若 #715 推进到结构化打戳，
  应把 `now` 接缝接到它的时钟源上，而不是另起一套。
- **同类真实时间窗**：本文件里另有 wall-clock 用例（`wall_clock=0.15/0.25`）仍走真实时间——
  它们的窗口比 stall 那批宽（子进程 sleep 60 vs 0.25s），暂未改造；若将来也 flake，
  同一接缝可直接复用。
- **`_advance_on_markers` 的标记轮询**：5ms 轮询 glob 在小 `tmp_path` 上开销可忽略；
  若测试目录变大，应改为 inotify 或计数文件。
