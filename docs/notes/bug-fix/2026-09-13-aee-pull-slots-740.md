# 主机级 AEE 拉取槽位限流（#740）

Status: implemented
Class: bug-fix

## Decision

单宿主机 20+ 设备并发崩溃时，各 Job 的 Reconciler 线程无主机级闸门，
机械盘上并发 `adb pull` / mobilelog / bugreport 写放大寻道延迟。

引入进程级 `host_extraction_slot`（`STP_AEE_MAX_CONCURRENT_PULLS`，默认
**2**，对齐 EventUploader 上传并发）：

- MTK：`processor.process_device_logs` 在单条 pull→verify→side exports
  路径外包裹；
- UNISOC：`UnisocUniviewReconciler._pull_event_dir` 同槽。

## Alternatives

- 仅限流 upload：边缘落盘仍打满 HDD；否决。
- 按 Job 串行化整个 reconciler tick：吞吐过低；按「单事件提取」持槽更贴
  I/O 峰值。

## Verification

- `pytest backend/agent/tests/test_aee_extraction_slot.py -q`
- `pytest backend/agent/tests/test_aee_processor.py -q`（回归）
- `python3 scripts/run_gates.py check:quick`

## Revisit

SSD 主机可调高 `STP_AEE_MAX_CONCURRENT_PULLS`；若需按盘类型自适应，另开
探测 `rotational` 的默认值策略。
