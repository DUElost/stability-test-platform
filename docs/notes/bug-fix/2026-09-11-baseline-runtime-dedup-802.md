# baseline pending 摘除 runtime 已处理行（#802）

Status: implemented
Class: bug-fix

## Decision

baseline 快照分片用独立 prefix（`watcher_baseline:{job_id}`）记 processed/
pending；runtime pass（无分片上限）把 baseline 未覆盖的积压行一并处理并写
入共享 `watcher:aee` processed。这些行仍留在 baseline 的 pending 里——后续
baseline 分片轮重放（processor pending 循环只查**本 prefix** 的 processed）
时对同一行再次 `_on_baseline_entry` → 再次 emit，**新 seq_no** 使控制面
`(job_id, seq_no)` 幂等失效：watcher-summary/异常率双倍计、DLE 重复 LOCAL
（积压上百行时重复数百条）。

修复（`reconciler.py`）：`_run_baseline_snapshot` 每轮开始前调用
`_drop_runtime_processed_from_baseline_pending`——加载共享 processed（本
job 的 `watcher:aee` prefix），把其中已含的行从 baseline pending 摘除；
只动 **pending 重放路径**，baseline 首次发现的「不复用共享 key 做可见性
判定（否则设备历史问题被静默吞掉）」约束保持不变。

## Alternatives

- **baseline 与 runtime 共用 processed key 并按 job 记账**——放弃：会改变
  baseline 首轮全量补拉语义（注释明示约束），且需给共享集引入 job 维度
  迁移；
- **控制面按内容去重**——放弃：服务端契约是 (job_id, seq_no) 幂等，内容级
  去重是另一层设计（跨端成本大），Agent 侧去重才是根因面。

## Verification

- **反例实证**：回退 reconciler 实现保留测试 → 用例失败（helper 缺失）；
  修复版全绿；
- 新增用例（`test_aee_reconciler.py` +1）：共享 processed 含 A、baseline
  pending 含 A/B → 摘除 A、**保留 B**（未处理行不误删）；
- `backend/agent/tests/` 全套 **1646 passed**（2m37s，含 baseline 双节奏/
  runtime 判定既有回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 摘除在每轮 baseline 开头执行（pending 集合量级=积压行数，SQLite 小读
  写）；若未来 baseline chunk 极大可评估增量维护；
- 本单不触碰 #803 第 1 处（emit/落盘原子性）——不同介质/不同面。
