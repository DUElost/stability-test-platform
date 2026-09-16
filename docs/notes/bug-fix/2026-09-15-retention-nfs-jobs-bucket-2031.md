# retention NFS 轨补齐 `jobs/{job_id}/` + 共享根容器化校验（#2031）

Status: implemented
Class: bug-fix

## Decision

### 1. `jobs/{job_id}/` 纳入 NFS 回收（按 job 分桶）

retention 的 NFS 轨原先只清 `devices|dedup|jira/{run_id}/`（#1521/#1698），但**同一个
共享根**下还有 `jobs/{job_id}/`——`backend/agent/aee/paths.py` 的 artifact promote
（`resolve_artifact_promote_dir`）与 Watcher LogPuller 的落盘根。这些文件的唯一索引是
`StepTrace.output.artifact.storage_uri` 与 `JobArtifact` 行，而**它们在同一批 retention 里
被删**：行保留期 `plan_run_retention_days` 默认 3 天，依赖行的 `_prune_steptrace_artifacts`
却是 30 天（`artifact_retention_days`）。索引比清理器早 27 天消失 → 目录**永不可回溯**，
随 job 数线性累积。

改法：`purge_run_storage_dirs(run_ids, jobs_by_run)` 增加第二类桶——`jobs/{job_id}/`
按**job** 分桶，由调用点已收集的 job 清单展开（`{run_id: [job_id, ...]}`）：

- 调用点把 job 清单的收集**前移**到 purge 之前（原在 purge 之后），用
  `select(JobInstance.id, JobInstance.plan_run_id)` 一次取回 id + 归属；
- job 目录删除失败按**所属 run** 归因 → 与 run 目录失败同语义：该 run 整批推迟
  （文件与行都留到下轮，先文件后行的顺序保证可自愈）；
- run 出批后收缩 `stale_job_id_list`（提交后的 console.log 清理不得清掉仍在库的 run
  的 job）。

### 2. 共享根容器化校验

`target = base / sub / str(run_id)` 直接 `is_dir()` + `rmtree()`：若 `{root}/devices` 等
本身是指向根外的符号链接目录，会跟随删除根外数据（与 #1825 收口的威胁模型同类，需共享根
写权限才可利用）。新增 `_within_shared_root()`：解析符号链接后必须仍在共享根内，越界
**一律不删**，计入 `failed` 并告警（宁可推迟该 run 的 DB 行删除，也不误删根外数据）。
run 目录与 job 目录共用同一条校验。

### 3. 顺带复核：还有哪些按 id 分桶的共享根前缀

`git grep` 共享根写入端，桶只有四类：`devices/{run_id}`、`dedup/{run_id}`、
`jira/{run_id}`（均已覆盖）、`jobs/{job_id}`（本单补齐）。另外两处**不在**回收面、
且**不应**回收：

- `mtbf/{export_dir}/`（`suite_binding.py`）——按**项目 key**（非 id）分桶，是运维维护的
  套件资产树（runtask.xml / Global 文件），没有 run/job 生命周期；
- `firmware/`——固件仓库，运维维护。

**观察项（未在本单处理，见 Revisit）**：`devices/unassigned/{event_id}/`
（`event_uploader.py` 的未关联上送落点）也不在清理集合里。它与 `jobs/` 不同——其生命周期
应由 DLE 记录（`remote_path` 指向该目录）+ `associate_unassigned_events_to_plan_run`
（extract 时把未关联事件并入 run）决定，需要一个明确的 TTL 口径，不在本单的「补一个
前缀」范围内。建议另开一单。

## Alternatives

- **把 `jobs/` 当作 run 分桶处理**（沿用 `run_ids` 展开）：`jobs/{job_id}` 的目录名是
  **job id**，用 run id 拼路径只会得到一堆不存在的路径（静默 no-op），这正是本单被漏掉的
  原因（#1698 的 Revisit 只写了「新增 run_id 分桶前缀要扩枚举」）。
- **单独加一个 `purge_job_storage_dirs()` 在 DB 删除前调用**：功能等价，但失败归因要跨两个
  函数回传，且「run 出批 → 其 job 也不得清」的收缩逻辑会散在两处；合成一次调用更内聚。
- **删除状态迁移表/校验只在 run 目录做**：job 目录同样由 id 拼接，同样可被符号链接带出根，
  只护一半没有意义。
- **越界时不计入 failed（静默跳过）**：会让该 run 的行删除继续推进 → 行没了、文件还在
  （且位置未知），正是 R-01 那类「不可回溯」；宁可推迟并告警。
- **顺手给 `devices/unassigned/` 加清理**：其 TTL 口径未定（可能仍有待关联事件），
  贸然清理会删掉尚未并入 run 的上送产物，越出本单范围。

## Verification

worktree `.wt/stp-2031-retention-jobs`（base `6b21e3b2`），解释器
`/home/debian13/stability-test-platform/.venv/bin/python`：

```bash
TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest backend/tests/scheduler/ -q   # 108 passed
python scripts/run_gates.py check:quick   # [OK] 10 gates
python scripts/run_gates.py check:pr      # [OK] 18 gates
```

新增三条守卫（**逐条还原 `cron_scheduler.py` 到 HEAD 后重跑，3/3 全失败**，恢复后 21 passed）：

- `test_nfs_job_dirs_purged_with_db_row`：到期 run 的 `jobs/{job_id}/` 被清理，未到期 run
  的 job 目录保留；
- `test_job_dir_purge_failure_defers_owning_run`：job 目录删除失败 → **所属 run** 的行保留
  （推迟重试），目录仍在；
- `test_purge_refuses_target_outside_shared_root`：`{root}/devices` 为指向根外的符号链接时，
  根外目录**未被删除**且该 run 被推迟。

既有 4 处 `purge_run_storage_dirs` 桩（`lambda run_ids: ...`）随签名更新为
`lambda run_ids, jobs_by_run=None: ...`——桩签名不同步会让整批使用例报 TypeError，属本次
变更的必需同步。

## Revisit

- **`devices/unassigned/{event_id}/`**：等 TTL 口径定下来（跟随 DLE 记录的生命周期？跟随
  关联时点？）再单独立单；本轮只在共享根前缀复核里点了名。
- `mtbf/` 与 `firmware/` 明确**不进**回收面（运维资产，非 run/job 产物）；若将来它们的
  生命周期被纳入平台管理，需先有各自的 TTL 设计。
- 越界拒绝会让该 run **永久推迟**（每轮都失败、每轮告警）：这是有意 fail-safe——需要共享根
  写权限才能制造该形态，属部署被破坏的信号，应由人工纠正符号链接后自愈。若将来出现
  「误判越界」（例如共享根本身经 symlink 挂载且 `resolve` 行为与预期不同），改为按
  「记录一次失败 + 跳过该前缀」而不是整 run 推迟。
