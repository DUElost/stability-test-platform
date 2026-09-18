# Agent 套件裸等待批 3：artifact_uploader（#2602 后续）

Status: implemented
Class: bug-fix

## Decision

`backend/agent/tests/test_artifact_uploader.py` 的 5 处亚秒 `time.sleep` 逐条分诊后，
**改 3 处、留 2 处**（判据同 #2602：「等某个状态出现」→ 改轮询；「为了让别人排队而占着」→ 保留）：

| 站点 | 判据 | 处置 |
|---|---|---|
| `test_dir_or_missing_promote_failed` 的 `sleep(0.2)` + `assert sess.posts == []` | 否定断言无法直接等；先等处理干净（`submits_total == promotes_ok + promote_failed + drops`） | 改为 `_wait_all_handled(u)` |
| `test_copy_failure_drops_without_post` 同上 | 同上 | 同上 |
| `test_invalid_payload_is_dropped_locally` 的 `sleep(0.15)` + `assert submits_dropped == 1` | 计数本身可等 | 改为 `_wait_for(lambda: u.stats.submits_dropped == 1, timeout=2.0)` |
| 队列满用例的 `sleep(0.05)`（"让 worker 把首条拎走"） | **场景搭建**：要让首条离开队列，后续才会因满而丢；"已被取走"不对外暴露，**没有可等的计数器** | **保留** + 注释说明 |
| `test_stop_degrade` 的 `sleep(0.05)`（同上） | 同上 | **保留** + 注释说明 |

新增 `_wait_all_handled(u)`：本文件已有 `_wait_for(cond)` 助手，这一步只是把「全部处理完」
这个复合条件写出来——**否定断言（"没有 POST"）必须先等前提成立再看**，否则 `sleep(0.2)`
既是等待也是赌。

## Alternatives

- **A. 把 5 处全换成轮询**：否决。后两处没有可观测计数器（"worker 已取走首条"不对外暴露），
  强行等 `submits_dropped >= 1` 会让断言变成同义反复（等不到就失败 ✗ 而不是"确认队列形态"）。
  要真确定化它们，需要发布器暴露"已取走"计数（属产品代码改动，不在一批测试改造里做）。
- **B. 直接删掉那两处 `sleep(0.05)`**：否决。删了会让"队列被填满"这一形态变得不确定
  （首条若还在队列里，后续 submit 的丢弃数不同），测试语义会漂。
- **C. 沿用 `_wait_for` 内联谓词、不加 `_wait_all_handled`**：否决。该条件在文件里出现两次，
  且公式（三个计数器之和）不写出来很容易被后来者写错一半。

## Verification

- **测试**：`backend/agent/tests/test_artifact_uploader.py` → **21 passed**，**连跑 3 轮全绿**（每轮 4.6–4.7s）。
- **全量**：`backend/agent/tests/` → **2130 passed / 73.8s**（无回归）。
- `ruff check` 通过。

## Revisit

- **两处保留的 `sleep(0.05)`**：若发布器将来暴露「已取走」计数（例如 `picked_total`），
  这两处可一并改成轮询——那时再动。
- **剩余站点**（#2602 清单）：`test_job_session_e2e` / `test_device_watcher` / `test_main` /
  `test_local_disk_monitor` / `test_heartbeat_parallel_probe` / 两个 `*progress_stamps_1690*`
  等；判据与本批相同，逐文件做。
- **分诊判据再固化一次**（三批下来它稳定成立）：**固定等待要么换成「可观测条件 + 上界」，
  要么证明它是在搭建场景（"必须占着/必须先离开"）并在注释里写明为什么没有可等的量**——
  没有第三种。
