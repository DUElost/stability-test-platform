# HddSpill 临界水位放大单批（#741）

Status: implemented
Class: bug-fix

## Decision

在 #1522 追打（30s）之上补临界水位策略：`usage ≥ 98%` 时单批预算
升到 `_MAX_SPILL_CRITICAL`（默认 100，env `STP_HDD_SPILL_CRITICAL_BATCH`），
常态仍 20 写放大节流。

#1522 已把净腾退从 4 目录/分钟提到约 40 目录/分钟，覆盖 issue 中的常态
崩溃积压模型；本单收「≥98% 边缘」——单轮 20 仍可能赶不上下一 catch-up
前的写失败/OOM 风险。

未做「无 LOCAL / 上传阻塞时硬删本地 dump」：会丢取证且与 EventUploader
语义冲突；临界放大 + 追打已把水位驱动闭环拉紧。

## Alternatives

- **只关 #741 指向 #1522**：追打解决主模型，但 issue 明示的 98% 硬保护
  仍空缺；否决。
- **按水位差额动态算预算**：单目录字节不可靠（#1522 已否）；临界固定
  放大更可预测。

## Verification

- `python -m pytest backend/agent/tests/test_local_disk_monitor.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

若现场仍打满，查 EventUploader 队列是否堵死（放大 enqueue 无济于事）；
那时再评估本地紧急 prune，需单独产品裁决。
