# EventUploader 重投入口复位 rescheduled（#800）

Status: implemented
Class: bug-fix

## Decision

失败轮在 `_UploadJob.rescheduled = True` 后经 `threading.Timer` 重投**同一个
job 对象**；成功轮 finally 仍见 `rescheduled=True` → `_forget_active` 永不执行，
`event_id` 滞留 `_active_ids` 直到进程退出。该事件后续状态回退（merge 失败
重扫 / 远端 mismatch 重传）在 `_recover_pending` / `_retry_failed_loop` 每轮
被拉回后又被去重静默丢弃——泄漏量随瞬时故障线性累积。

修复：`_run_upload_holding_slot` 入口复位 `job.rescheduled = False`
（每次投递 = 新一轮）。语义保持：

- 重投成功 → 复位后 finally 释放标记 ✓（修复目标）；
- 重投再失败 → except 再次置位 + 新 Timer → 退避窗口内标记仍保留
  （#380 的防重入队语义不变）；
- 首轮失败 → 同上，退避期间保留。

## Alternatives

- **Timer 回调投递前复位**——放弃：复位点与「处理开始」分离，若队列满
  （put 阻塞/异常路径）会出现「已复位但未处理」的标记窗口；入口复位与
  finally 判定在同一执行单元，闭合更紧；
- **finally 改为「仅在非重试出口 forget」的显式枚举**——放弃：需要枚举
  全部出口（终态/成功/不可恢复异常），rescheduled 已是对应的单一事实源，
  复位它比二次枚举稳。

## Verification

- **反例实证**：回退实现保留测试 → 新用例失败（第二轮成功后 rescheduled
  残留、标记未释放）；修复版全绿；
- 新增用例（`test_event_uploader.py` +1）：失败一轮（`True` + 标记保留）
  → 重投成功（复位 + 标记释放）；
- `backend/agent/tests/` 全套 **1561 passed**（2m20s，含既有 terminal
  释放/退避保留/远端恢复回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- `threading.Timer` 重投路径若未来改为持久化队列（跨重启重试），
  rescheduled 标志应随之下沉到队列条目状态（本单维持进程内语义）。
