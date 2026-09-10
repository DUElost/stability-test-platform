# merge SAQ 预算按双平台完整等待链重算（#1085）

Status: implemented
Class: bug-fix

## Decision

#1085（R10-F17，设计风险）：`_MERGE_TASK_SAQ_TIMEOUT` 旧公式
`= 300 + 660 + 120 = 1080s` 只按**单平台** merge 计，而 `run_merge_all_platforms_sync`
遍历 `DEDUP_PLATFORMS`（mtk + unisoc）各跑一轮工具调用（每轮
`subprocess timeout=300`），加标记水位线（180s）与 DLE pending 等待（660s），
双平台链最长 **1440s**（未计文件 I/O）——超出预算时 merge_task 会被 SAQ 误杀，
coroutine 取消但线程继续跑（#1123 同型残留），终态缺口。

修复（重算预算，不拆任务——拆分会破坏「merge 成功才 extract」的链式语义）：

- 常量改名并参数化：`_MERGE_TOOL_TIMEOUT_PER_PLATFORM = 300`（与
  `dedup_scan.run_merge_sync` 的 subprocess timeout 同源口径）、
  `_MERGE_PLATFORM_COUNT = len(DEDUP_PLATFORMS)`；
- `_MERGE_TASK_SAQ_TIMEOUT = 平台数 × 300 + 180 + 660 + 120 = 1560s`
  （双平台完整等待链 + 文件 I/O 与调度余量）；
- `_SYNC_OVERLAP_WAIT_BUDGET_DEFAULT`（#1123）自动跟随新预算——残留线程的
  等待预算同样覆盖双平台链；
- 同步修正两处测试：`test_saq_scan_pipeline.py` 的预算断言从旧公式改为逐项
  公式断言（平台数 == len(DEDUP_PLATFORMS) == 2，总预算 == 1560）；
  `test_saq_tasks.py` 的 `_enqueue_extract_task` 断言补上 `scan_round_id`
  （#1111 改签名后的既有失败，顺手收口——"测试与公式一致"同义）。

## Alternatives

- 任务拆分（每平台一个 merge SAQ job）：打破「merge 成功才链式 extract」与
  publish 顺序假设，改动面横跨 dedup_scan/前端汇总口径——预算重算是 4 行改动
  与语义零变化的最小方案；
- 上调单平台 300s：300 是厂商工具实测上限（#381 时代口径），动它需要新的实测
  依据，超出本单范围。

## Verification

- `pytest backend/agent/tests/test_saq_scan_pipeline.py backend/tests/tasks/
  test_saq_tasks.py`：57 passed —— 预算公式逐项断言（2 平台 / 1560s），
  `test_merge_task_mark_timeout_...`（main 上因 #1111 签名变更失败的既有用例）
  一并修正；
- `python -c` 实测：`_MERGE_PLATFORM_COUNT=2`、`_MERGE_TASK_SAQ_TIMEOUT=1560`；
- ruff 干净。

## Revisit

- 若新增第三平台（QCOM 等，#721/#724），`_MERGE_PLATFORM_COUNT` 自动跟随
  `DEDUP_PLATFORMS` 增长，预算公式无需再改——本单把它从魔数改成派生值正是
  为此；
- 1560s 是「预算 ≥ 完整链」的上界口径：实际超时仍可能来自 NFS 挂死（工具
  subprocess 内部不受 300 保护的文件 IO）——那属于 dedup_scan 的 timeout 粒度
  问题，如出现另立单；
- 预算与「隔离验证长耗时场景」：真机双平台 1440s 级场景受 inventory 限制
  （capacity:inventory-bound），先以公式+单测收口。
