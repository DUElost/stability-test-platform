# retention 清理链式引用安全化——引用闭包保留集（#936）

Status: implemented
Class: bug-fix

## Decision

#936（R03-F03）：`run_retention_cleanup`（`backend/scheduler/cron_scheduler.py`）
按终态 + `started_at < cutoff` 批量直删最多 100 个 PlanRun，但
`parent_plan_run_id` / `root_plan_run_id` 自引用 FK 无删除级联——父到期而子
未到期/仍在运行时删除违反 FK，**整批事务回滚**；且下一轮必然重选同批 →
僵尸积压（日志反复 `retention_cleanup failed`）。

修复（删除前计算引用闭包保留集，不用 CASCADE）：

1. **外部引用扫描**：批内 id 被任何「不在本批」的 Run（运行中 / 未到期 /
   不在本批窗口）经 `parent` 或 `root` 引用 → 入保留集；
2. **祖先链传播**：保留 R 后，R 自身引用的批内祖先（parent / root）同样
   保留——不动点迭代至收敛；
3. `safe_run_ids = 批内 − 保留集`，子表（StepTrace / DeviceLease /
   ResourceAllocation / JobArtifact / JobInstance）清理同步改用
   `safe_run_ids`（否则会误删保留 Run 的 job 数据）；全部保留时显式
   `skipped` 日志返回；`deleted/kept_chain_referenced` 计数入日志。

与 #781（retention 后 job_log_signal 孤儿）根因不同，未在本单处理；
CASCADE 被否——会误删仍需保留的后续 Run。

## Alternatives

- FK 加 `ondelete=CASCADE`：误删仍需保留的后续 Run（验收明文禁止）且需迁移；
- 批次选择改为「按链整族选取」：改动选取语义且族内仍有运行中成员时同样
  卡整族，保留集方案让可删部分继续推进；
- 删除前逐 Run 单删 + 跳过失败者：N 次往返性能差，且失败后重试语义模糊；
  闭包集一次计算等价且确定。

## Verification

- `pytest backend/tests/scheduler/test_retention_cleanup.py`：5 passed（新
  文件——父到期子运行中父保留 / 全链到期全删 / 孙运行中祖先链传播保留 /
  root 被运行中 Run 引用根保留 / 无链正常删回归）+ snapshot 契约 4 passed；
- 测试基建注意（note 留痕）：cleanup 经模块级 `SessionLocal()`（with 退出
  close 会回滚未提交事务）——fixture 数据必须先 commit，否则被 cleanup 的
  session close 一并丢弃（调试时经 `--log-cli-level=INFO` 的 skipped 日志
  实证 keep 集工作正常后定位）；
- ruff、gov-surface S1–S12 全绿；
- Registry：fix-936-retention-chain-safe 全程登记（--issue 936）。

## Revisit

- #781（job_log_signal 孤儿）仍开放，若清理顺序需统一设计时与本保留集
  一并考虑；
- 批量 100 上限下保留集偏大时每轮推进量下降——观测 `kept_chain_referenced`
  日志，若长期占比高再评估按链整族选取。
