# 中心存储族清单收成单一来源：`_meta` 有清理、无观测（#2188 D 步切片）

Status: implemented
Class: bug-fix

## Decision

**本质问题不是「测量脚本少一族」，而是同一个事实被抄了三份、三份互不相同。**
tip `e40f7a13` 复核（`24da1b9e` 那条 commit 自述「漏桶=E-2 必挂」）：

| 位置 | 当时自带的清单 | 缺什么 |
|---|---|---|
| `backend/scheduler/cron_scheduler.py:295`（retention 写侧） | devices / dedup / jira / `_meta` | — |
| `backend/scripts/measure_center_storage.py:47`（E-1/E-2 读侧） | devices / dedup / jira / **jobs** | **缺 `_meta`** |
| `docs/design/2026-scan-upload-merge-contract.md` 路径表 | devices / dedup / jira / jobs | 缺 `_meta` |

于是 `_meta/{run_id}/{host}.json`（#2188 D 步的上传清单分片）**被 retention 清、却不出现在
任何测量口径里**：该族的残留对 E-2 运维对账结构性不可见——写侧新增一族时读侧没有任何机制
会喊。反向也错：`jobs` 是 **job 主键**（`jobs/{job_id}/`，#2031），却被读侧按 run 分解进
`run_counts` / `top_runs_by_bytes`，与「DB 里应已清理的 run 清单」对账时产出既非漏删也非
干净的第三种读数。

裁决：**收成一份正本，而不是在两处各补一行。**

- 新增 `backend/storage_families.py`：`RUN_FAMILIES`（run 主键族，含 `_meta`）、
  `JOBS_FAMILY`（job 主键族，**刻意不在 `RUN_FAMILIES` 内**）、`ALL_FAMILIES`（派生 =
  run + job）、`MERGE_REPORT_FAMILIES`（E-1b 的「同一份 merge 报表双落点」两族，与
  `RUN_FAMILIES` 是不同概念）、`NON_KEY_ENTRIES`（族下非主键保留名，当前只有
  `devices/unassigned`）。
- 写侧 `cron_scheduler.purge_run_storage_dirs` 与读侧 `measure_center_storage` 改为 import
  正本；purge 循环与 `collect_family_usage` / `collect_run_usage` 不再自带字面量。
- 读侧新增 `collect_job_usage()` 与两条报告轴 `job_dir_count` / `job_dir_bytes`；
  `run_counts` / `top_runs_by_bytes` **自此只含 run 族**（口径变更见 Verification）。
- 契约文档「中心存储路径」表补 `_meta` 行，并写明正本是 `backend/storage_families.py`。

**正本为什么放 `backend/` 包根而不是 `backend/core/`**（实测，非偏好）：
`backend/core/__init__.py` 是 eager `from .database import engine`，缺 `DATABASE_URL` 时
**整个包拒绝导入**（`env_source.resolve_database_url()` 直接 `RuntimeError`）。放进
`backend.core` 会让 `python -m backend.scripts.measure_center_storage --center-root /mnt/center`
这台「只挂了 NFS、没配库」的机器上跑不起来——该脚本的文档承诺恰恰是「只读、不查库」。

**判别力（三层，全部可红）**：

1. `tests/test_center_storage_families_single_source.py`——写侧/读侧引用的必须是**同一对象**
   （`cron_scheduler.RUN_FAMILIES is sf.RUN_FAMILIES`，本地重绑一份即红）；`jobs` 不得进
   `RUN_FAMILIES`；`_meta` 必须在；
2. 同文件的**源扫描**判据（按 #2639 用 `SourceGuard`，锚点先行）：两个已知消费方必须走正本，
   且生产面（`backend|tools|scripts`，排除测试面/已发布脚本版本/alembic 历史/vendored）
   除正本外不得再出现 `"dedup", "jira"` 这种族清单字面量；扫描面塌陷（<300 文件）与
   根消失都判红；并配**变异自证**（造一份抄清单的文件必须被抓到、正本必须被豁免）；
3. 文档对拍：契约表「中心存储路径」节必须逐族出现 `{root}/<族>/`——**只在该节内找**，
   别的章节提到同名目录不算数（#2643/#2639 都是行级 grep 少算的实例）。

行为侧同族覆盖：`backend/tests/scheduler/test_retention_cleanup.py` 的 NFS 夹具与两条断言
改为**对 `RUN_FAMILIES` 参数化**，新增一族自动获得「被清 + 未被清」两侧覆盖；
`backend/tests/test_measure_center_storage.py` 同样按正本参数化断言每一族的可见性。

## Alternatives

- **两处各补一行（读侧加 `_meta`、写侧不动）**：否决。修的是这一次的读数，安全网仍在——
  下一族照样静默漏。本单的失效形态与 #2691（告警条数抄进文字）、#2639（守卫锚点漂移）同族：
  **派生真值被手抄**。
- **把清单搬进 `backend/core/storage_families.py`**：否决，理由见上（实测会把 stdlib-only
  诊断工具绑到数据库配置上；`backend/core/__init__.py` 的 eager engine 是既成事实）。
- **顺手把 `dedup_extract` / `dedup_scan` / `upload_manager` 里的 `"/dedup"`、`"/jira"`、
  `"/_meta"` 路径字面量也收敛**：不在本单。那是「路径构造」面，涉及 services 与 agent 双侧，
  且与在窗 #736/#2187 切片同文件域；本单只收「族清单」这一份事实。已记入 Revisit。
- **给 E-2 直接加 DB 对账**：不在本单。#2188 的 E-2 判据要求「应已清理 run 清单」来自控制面
  DB，而本脚本的职责边界明写「不查库」——本次只保证**族这一维不再缺**，对账仍属 D 步后续。

## Verification

- `.venv/bin/python -m pytest tests/test_center_storage_families_single_source.py -q` → **9 passed**
  （含变异自证与文档对拍两条判据自身的红侧）
- `.venv/bin/python -m pytest backend/tests/test_measure_center_storage.py -q` → **21 passed**
  （原 16 条 + 参数化 run 族可见性 + jobs 口径两条；`run_counts["jobs"] == 1` 这条旧断言
  改判为 `job_dir_count == 1` 且 `"jobs" not in run_counts`——它守的正是本次要消除的混读）
- `.venv/bin/python -m pytest backend/tests/scheduler/test_retention_cleanup.py -q` → **41 passed**
- `.venv/bin/python -m pytest tests/ -q` → **1554 passed**（仓库级门禁全量，含本单新文件）
- `tools/dev/check_inner_imports.py` → 607 处 ≤ 基线 607（本次新增 import **全在模块顶层**，
  函数体内 import 一条未加）
- `tools/dev/check_governance_surface.py --check` → 阻塞项全绿（S1–S14、S5x）
- `scripts/run_gates.py check:quick` → ruff / env-inventory / schema-at-head 通过；
  **eslint 在本 worktree 未跑起来**（该 worktree 无 `node_modules`，本机无 `eslint` 可执行）。
  本单 diff 不含任何 `frontend/` 文件，前端门禁按 pending 计，不当作通过。
- 未做：真机 NFS 上的 `measure_center_storage --json` 实跑（生产中心存储读取需挂载与运维窗口，
  本单只改口径不改数据）——标 pending。

## Revisit

- **路径构造面**：`backend/services/dedup_extract.py:285`、`dedup_scan.py:195/223/1271`、
  `backend/api/routes/dedup.py:752`、`backend/agent/upload_manager.py:102/129` 仍各自拼
  `/"dedup"`、`/"jira"`、`/"_meta"`。它们是「单族单点」而非「清单」，本次判据不误报也不收它；
  真要收，需在 `storage_families` 之上做一层 run/job 路径 helper，属 #2188 A 步的范围。
- **E-2 收口**：族这一维齐了，但「应已清理 run 清单 vs 实际残留」仍需 DB 侧配方；
  #2188 验收要求的效果对比数字（D→A 前后）仍待采。
- 若将来出现第三个主键维度（如按 host 分桶），必须先在正本里显式表达，再让读侧加轴——
  不要复用 `run_counts` 塞非 run 条目。
