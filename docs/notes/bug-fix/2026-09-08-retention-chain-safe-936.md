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

与 #781（retention 后 job_log_signal 孤儿）根因不同；该删除顺序后续已由
[终态生命周期修复](2026-09-12-terminal-lifecycle-781.md) 覆盖。
CASCADE 被否——会误删仍需保留的后续 Run。

**#1827（最近 7 天审计 F06）有界推进**：旧实现先 LIMIT 100 再算保留集，
101 节点的到期链或前 100 个均被活跃子节点引用时，可能永远重选同一批。
现在每层候选在 SQL 中先排除仍被批外节点引用的 Run，再 LIMIT 剩余预算；
已入批的叶子视作待删除，继续收集因此解锁的父/root 节点，直到无可选叶子
或总数达到 100。全到期短链仍一轮清完，101 节点链两轮清完，不全量载入
链族，也不把批大小变成无界。按 started_at/id 稳定排序；自引用 root 不算
外部阻碍；`FOR UPDATE SKIP LOCKED` 固定候选并跳过其他清理者持有的行。

保留集算法抽为共享 helper：NFS 清理失败剔除节点后再计算一次祖先保留集，
否则删其祖先会触发 FK 回滚，连可安全删除的兄弟节点也无法推进。先文件
后 DB、失败下轮重试、活跃/未到期引用保护及已有子表清理顺序保持不变。

## Alternatives

- FK 加 `ondelete=CASCADE`：误删仍需保留的后续 Run（验收明文禁止）且需迁移；
- 批次选择改为「按链整族选取」：改动选取语义且族内仍有运行中成员时同样
  卡整族，保留集方案让可删部分继续推进；
- 删除前逐 Run 单删 + 跳过失败者：N 次往返性能差，且失败后重试语义模糊；
  闭包集一次计算等价且确定。
- 只把 LIMIT 改大或反转 id 排序：#1827 否决，仍可能反复撞到受保护前缀。
  叶子筛选在 LIMIT 前进行，每轮最多 100 个候选、至多 100 次非空层查询，
  最终仍一次事务批量删除，而非逐 Run 提交。

## Verification

#1827 本次验证：

- `python -m pytest backend/tests/scheduler/test_retention_cleanup.py -q` → **16 passed**。
  首轮新分叉夹具违反 `(parent_plan_run_id, plan_id)` 唯一约束，改为合法的不同
  Plan 分叉后通过，并补强为多级祖先保留场景。
- `python -m pytest backend/tests/scheduler/test_retention_cleanup.py backend/tests/models/test_plan_run_snapshot_tables.py backend/tests/scheduler/test_cron_overlap_policy.py -q`
  → **27 passed**，覆盖关联快照表与 Cron 防重叠契约。
- `python scripts/run_gates.py check:quick` → **7 gates 通过**；变更文件 Ruff
  与 diff check 通过。
- 新回归覆盖 101 节点链两轮推进、101 个受保护前缀后连续三轮回收、105 个
  无引用 Run 的 100 上限、自 root 引用、已锁行让路、NFS 失败保留多级祖先
  但仍删除安全兄弟并在下一轮重试成功。
- 测试固定使用 testcontainers Postgres，fixture 将共享存储与 console 根
  显式指向 `tmp_path`，不依赖宿主环境中的真实路径。

此前 #936 历史证据（不代表本次重跑）：

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

- 若长链批次的最多 100 次有界选层查询成为可观测瓶颈，再评估递归 CTE；
  不以扩大无界批次或级联删除换取吞吐。
- 保留期仍沿用 started_at 与原终态集合，本修复不改变 TTL 业务语义。
