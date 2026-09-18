# Agent 套件裸等待批 2：step5b 集成 + puller 改有界轮询（#2602 后续）

Status: implemented
Class: bug-fix

## Decision

按 #2602 的清单继续，本批 5 处（`test_step5b_integration.py` ×3、`test_puller.py` ×2）：

| 站点 | 原写法 | 现在等什么 |
|---|---|---|
| `test_cancel_while_waiting_denies_permit` | `sleep(0.15) # waiter is queued` | `42 in s.waiting_devices` |
| `test_cancel_after_promote_via_coordinator_releases_slot` | `sleep(0.15)` 后 `holder.release()` | `40 in s.waiting_devices` |
| `test_lease_lost_cleanup_before_cancel` | `sleep(0.15)` 后走 lease-lost 顺序 | `55 in s.waiting_devices`（**设备** 55；400 是 job id） |
| `test_stop_drain_false_degrades_pending` | `sleep(0.1)` 后 submit 队列项 | 新增 `entered` 事件：worker 进入 `pull` 即为可观测 |
| `test_on_done_exception_does_not_crash_worker` | `sleep(0.2)` 后 submit 第二条 | `call_count["n"] >= 1`（下方本来就在轮询同一计数器） |

`test_step5b_integration.py` **本来就有这个惯例**（`while i not in s.waiting_devices and …`），
本批只是把它抽成 `_wait_queued(scheduler, device_id, timeout)` 并用在同一文件其余站点。

**明确未动**：同文件 `test_max_concurrent_permits_respected` 里 worker 内部的 `sleep(0.05)` ——
那不是"等异步起来"，而是**故意占住 permit 制造争用**（与同批 flush 用例的 `sleep(0.8)` 同类：
测试强度/场景搭建，不是 flake 源）。

## Alternatives

- **A. 把 `wait_until` 抽到共享测试模块**：本单不做。仓内既有惯例是**每文件自持**（`test_device_watcher.py`
  有自己的 `_wait_until`，`backend/tests/` 另有若干 `_wait_until_blocked`），且各文件等的对象不同
  （谓词 / RUNNING / waiting_devices）；等第三、四处**同语义**副本出现再抽。
- **B. 机械替换其余 20 处**：否决（同 #2602）：等待对象不同，假条件比不改更坏。
- **C. 只改"看起来最可能红"的一处**：否决。本批 5 处同形，且该套件在 PR 路径上（红即卡合入）。

## Verification

- **测试**：`test_step5b_integration.py` + `test_puller.py` → **50 passed**，**连跑 3 轮全绿**（每轮 6.2s）。
- **全量**：`backend/agent/tests/` → **2130 passed / 71s**（无回归）。
- **一次自查抓到的错**（值得记）：`test_lease_lost_cleanup_before_cancel` 里我第一版等的是
  `400 in waiting_devices`，而 waiter 用的是 **`s.acquire(55)`**（400 是 job id）——首轮跑出
  `1 failed` 才发现。**先跑测试后提交**在这里救了一次：机械按"上下文里出现的数字"取 id 是不可靠的。
- `ruff check`（含 `--fix` 掉两处因去掉 sleep 而不再使用的函数级 `import time`）通过。

## Revisit

- **其余 15 处**（`test_job_session_e2e.py` / `test_script_progress_stamps_1690_batch2.py` /
  `test_device_watcher.py` / `test_main.py` 等）：逐个核可观测条件后处理（#2602 清单）。
- **「故意占住」与「等异步起来」的判别**：本批又遇到一处（worker 内 `sleep(0.05)` 制造争用）。
  判据是**意图**——"为了让别人排队而占着" vs "等某个状态出现"；前者不该改，后者必须改成轮询。
  扫描脚本抓不出这个区别，只能人工判（#2602 的 Revisit 也记了同类盲点）。
- **`_wait_queued` 与 `_wait_until` 的收敛**：等出现同语义的第三份副本时抽公共模块（现在
  一个等 `waiting_devices`、一个等任意谓词、一个等 `RUNNING`）。
