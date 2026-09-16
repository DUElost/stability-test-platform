# `devices/unassigned/{event_id}/` 纳入 retention（#2262 方案 A）

Status: implemented
Class: bug-fix

## Decision

**方案 A（跟随 DLE 行）落地为：行被 retention 删除的同批，清掉它引用的
`{root}/devices/unassigned/{event_id}/`。**

机制（为什么以前漏掉）：该目录不随 run 分桶，而**关联也不搬文件**——
`associate_unassigned_events_to_plan_run`（#213 B3）只把 DLE 行的 `plan_run_id` 填上，
docstring 明说 "Does not move NFS paths"。于是 run 级 purge（按 `run_id` 拼路径）
永远命中不到它，行删后目录成为永不可回溯的孤儿。

实现（`backend/scheduler/cron_scheduler.py`）：

- `_collect_unassigned_dirs(db, safe_run_ids, stale_job_ids, job_run_of)`：取**本批将要删除的
  DLE 行**（谓词与随后的 DELETE 逐字一致）中 `remote_path` 落在 `devices/unassigned/` 的目录，
  并映射到所属 run（`plan_run_id`，缺失则用 job→run 反查）——失败要按 run 归因；
- `purge_unassigned_event_dirs(paths_by_run)`：逐目录 `rmtree`，只接受
  `{root}/devices/unassigned/{event_id}` 的**直接子目录**（`resolve()` 后父目录必须恰为
  unassigned 根 + `_within_shared_root` 容器校验）——`remote_path` 被污染时宁可留孤儿也不
  让 `rmtree` 引到别处；越界计入 failed 并告警（fail-safe）；
- 调用顺序：**先清 unassigned，再清 run/job 目录**，两轮各自「失败 → 该 run 出批
  （`_retention_safe_ids`）」。顺序有讲究：unassigned 轮出批的 run 在随后那轮里不会被清掉
  任何目录；反过来若 run 轮先失败，unassigned 轮就已经把文件删了而运行还在——那是
  「文件没了、行还在」的坏方向；
- `stale_job_id_list` 改为两轮之后**统一收缩**（出批 run 的 job 仍留在库里，其 console.log
  不得清理）。

**与 issue 原文的一处收窄（有意）**：issue 的 A 写的是「行进入终态（ARCHIVED/PRUNED）**或**
行被删除时清」。本轮只做**后者**——行还活着时删文件会破坏 `remote_path` 的可回溯性
（extract 仍按它取件），而「行删即清」已把增长限制在**保留期内**（默认 3 天），
这正是本单要的性质。前者要等「终态即删」这件事被单独裁决（见 Revisit）。

### 存量采数（只读，2026-09-16）

| 事实 | 值 |
|---|---|
| 生产库中 `remote_path` 指向 `devices/unassigned/` 的 DLE 行 | **1 行**（state=ARCHIVED，2026-08-12） |
| 其中 `plan_run_id`/`job_id` 双 NULL（现有删除谓词匹配不到，需另立口径） | **0 行** |
| 盘上 `{nfs}/devices/unassigned/` 目录数 / 占用 | **1 个 / 20K**（即那行引用的目录，非孤儿） |
| 同根大头对照 | `devices/{run_id}` 单 run 达 107G（已由既有 purge 覆盖） |

结论：**当前 0 个孤儿**，本单是**潜在**泄漏的收口（该行删掉的那一刻就会产生第一个孤儿）。
无需一次性存量清理；双 NULL 行当前不存在，故本轮不为它设计 TTL（见 Revisit）。

## Alternatives

- **关联时把目录搬到 `devices/{run_id}/`（issue 的 C 方案）**：终态最干净，但改动落在关联
  路径 + DLE `remote_path` 改写，且存量仍需一次性处理；本轮不做，记在 Revisit。
- **纯时间 TTL（B 方案）**：与关联/归档状态无关，可能删掉尚未关联但仍有价值的件；A 已把
  增长限制在保留期内，B 无必要。
- **行进终态即删（A 的完整形态）**：行还活着就删文件 → `remote_path` 悬空、extract 取件失败；
  收益只是把窗口从「保留期」缩到「extract 之后」，不值这个风险。
- **把 unassigned 目录并进 `purge_run_storage_dirs` 的某一轮**：两类目录的失败归因与顺序语义
  不同（前者按行→run，后者按 run→job），合并会让「哪一轮先跑」变得不可读；保持两个函数、
  调用点显式排序。
- **删除谓词改为「remote_path LIKE」直接删行**：会让「行是唯一索引」的既有原则失效
  （行没了、目录还在，且再也没人知道它属于谁）——正是本单要消除的形态。

## Verification

worktree `.wt/stp-2262-unassigned-ttl`（base `7620e60b`），解释器
`/home/debian13/stability-test-platform/.venv/bin/python`：

```bash
TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest backend/tests/scheduler/ -q   # 111 passed
python scripts/run_gates.py check:quick   # [OK] 10 gates
python scripts/run_gates.py check:pr      # [OK] 19 gates
```

新增三条用例（**还原 `cron_scheduler.py` 到 HEAD 后 2/3 失败**，恢复后全绿）：

- `test_unassigned_event_dir_purged_with_row`：到期行的 unassigned 目录随行清理，**未到期 run
  的目录保留**（HEAD 上红）；
- `test_unassigned_dir_purge_failure_defers_run`：目录清不掉 → 该 run 的行保留（HEAD 上红）；
- `test_unassigned_purge_rejects_non_child_path`：`remote_path` 借 `unassigned/..` 指向别处 →
  **不删**（HEAD 上平凡通过——它是新代码路径的反回归守卫，不是 HEAD 可判定的回归）。

## Revisit

- **行终态即删**（A 的完整形态）若要启用，先回答：extract 之后是否还有任何路径会按
  `remote_path` 回读该目录（若没有，窗口可以从保留期缩到 extract 完成时刻）。
- **双 NULL 行**（`plan_run_id` 与 `job_id` 皆空）当前为 0，但机制上存在：这类行既不被现有
  删除谓词命中、也不会被本单清理。若将来出现，需要一个「未关联事件」的独立 TTL 口径。
- **C 方案（关联时搬移）**若落地，本单的 A 路径应随之退役（同一目录不应有两个回收入口）。
- 存量采数是一次性的：后续如需复核，复用「DLE 行 `remote_path LIKE` + 盘上目录数」两条只读
  查询即可（命令见本单 PR 描述）。
