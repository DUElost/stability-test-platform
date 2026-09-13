# UnisocUniviewReconciler：单 root 缺失不阻塞 _processed 裁剪（#1820）

Status: implemented
Class: bug-fix

## Decision

`UnisocUniviewReconciler._sync_device_events_to_local` 原先对两个设备
uniview root 执行 `ls -1 {root} 2>/dev/null`，并把 `shell_fn` 返回
`None`（传输失败）与 `ls` 非零退出（root 不存在/不可读）混为一谈——
任一 root 失败即 `listing_complete=False`，`_last_listed` 整拍保持
`None`，`_prune_processed` 永不裁剪（#767 回归：仅缺
`/data/vendor/uniview` 等单 root 的机型上 `_processed` 再次只增不减）。

改为复合 shell `ls -1 {root} 2>/dev/null; echo __STP_RC__:$?`，由
`_list_remote_uniview_root` 解析 marker 行区分：
- `shell_fn` 返回 `None` → 传输失败 → 该 root 不计入列表，整拍
  `listing_complete=False`（与 #767 一致，不裁剪）。
- `ls` rc≠0 → 该 root 权威空集，继续处理其余 root。
- `ls` rc==0 → 按行解析事件目录名。

## Alternatives

- 探测 root 是否存在再 `ls`——多一轮 adb，且与「缺失即空集」语义等价。
- 单 root 失败仍挂起裁剪——即 #1820 所修复的旧行为，不可接受。
- 把缺失 root 记入 `_last_listed` 为特殊哨兵——增加裁剪分支复杂度，
  空集 union 更直接。

## Verification

- `backend/agent/tests/test_unisoc_reconciler.py::TestProcessedPrune`：
  新增 `test_missing_uniview_root_treated_as_empty_allows_prune`；既有
  `startswith("ls -1 /data/")` 的 mock shell_fn 仍匹配新复合命令。
- `pytest backend/agent/tests/test_unisoc_reconciler.py::TestProcessedPrune -q`

## Revisit

- 若未来需要区分「权限拒绝」与「目录不存在」的 rc，可打 debug 日志；
  裁剪语义上两者均为该 root 无事件名。
