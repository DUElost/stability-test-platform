# 控制面套件裸等待收尾：改 3 处 + 其余逐条定性（#2595 后续）

Status: implemented
Class: bug-fix

## Decision

`backend/tests/`（259 个用例文件）按 #2595 的判据复扫，本次**带"场景搭建"分支**逐条定性：
候选 7 处 → **改 3、留 4**，该侧这一类到此**挖尽**。

**改（3 处）**：

| 站点 | 原写法 | 现在 |
|---|---|---|
| `test_phase0_closure.py::test_shutdown_event_wakes_main_loop` | `t.start(); sleep(0.1); shutdown_event.set()` | 用 `entered` 事件等**线程已进入 `wait`** 再 set |
| `test_phase0_closure.py::test_sigterm_sets_shutdown_event` | `os.kill(SIGTERM); sleep(0.1); assert is_set()` | `assert shutdown_event.wait(timeout=1.0)`（信号投递本来就是异步） |
| `test_precheck_notify.py` 去抖用例 | 两次 `emit…`；`sleep(0.08); assert len(captured) == 1` | 有界等首次 emit 出现后再断言 count |

第一处的**实质**比"消掉 sleep"更重要：原先线程若尚未起来，`set()` 会先发生，
`woke_at[0]` 恒 ≈0，用例**看似通过却根本没测到唤醒路径**——加了 `entered` 屏障后，
断言测的才是它声称要测的东西。

**留（4 处，逐条定性）**：

| 站点 | 定性 |
|---|---|
| `test_saq_tasks.py` 的 `sleep(0.3)` | 在 `previous()` 里 = **故意让上一轮跑久一点**（让等待中的重试真的等待）→ 场景搭建 |
| `test_dedup_scan_merge.py:117/140` | **放大交错窗口**（行内注释已写"无锁时必交叉"）→ 让竞态可被检出，不是等待 |
| `test_run_console.py:544` 的 `sleep(0.8)` | 同批 flush 串行化用例：给 reader 制造竞态的机会（#2596 已定性保留） |

## Alternatives

- **A. 把 4 处"保留"的也改掉**：否决。它们的语义是"必须占着/必须跑久/必须给窗口"——
  没有"某个状态出现"可等；换成轮询会让断言变同义反复（#2618/#2620 同一结论）。
- **B. 只改 `test_phase0_closure.py` 的 SIGTERM 那处**：否决。另一处虽然"不会红"，
  但它**静默地没测到要测的东西**（见上），属比 flake 更隐蔽的缺陷。
- **C. 顺手把 `backend/tests/` 全量跑一遍**：本单改动面是 2 个文件、3 处等待，
  跑这两个文件（含同目录邻居）足够；全量留给夜间 job。

## Verification

- **测试**：`test_phase0_closure.py` + `test_precheck_notify.py` → **22 passed**，**连跑 3 轮全绿**（每轮 2.6–2.8s）。
- `ruff check` 通过。
- **前序批次状态**：同族的 #2596（RunConsole 5 处）与 #2603（scheduler 6 处）**已合入**；
  #2584（stall 检测时钟接缝，PR 路径 flake 的根源）**已合入**——今天卡住多条 PR 的那条
  随机红路径已经断根。

## Revisit

- **两套件都扫完了**（`backend/agent/tests/` 四批 + `backend/tests/` 两批）。若将来新增
  用例里再出现固定等待，按同一判据分诊：**(a) 等可观测条件 + 上界**、**(b) 场景搭建并在
  注释写明为什么没有可等的量**；其余都是缺陷。
- **扫描器仍会把两类误判成候选**（子进程脚本字符串、`with` 块内的场景搭建）——
  本单的复扫已带 `⊂with` 提示但仍需人工读；若这个系列还要再跑，值得把"是否在 `with`
  块内"做成扫描器的显式输出（现在是启发式辅助列）。
- **`test_phase0_closure.py` 那一类"静默没测到"**比 flake 更难发现：判据是**断言是否依赖
  被等待的状态**——本单是靠读代码发现的，机器筛不出来，登记在此。
