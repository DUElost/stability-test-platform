# B8 U5 #4045 第 1 刀：compat_reruns 派生族登记与容量可见性

Status: implemented
Class: feature

日期：2026-11-18　基线：`origin/main@1e6be5502`　批次：#3800 / #4045
判据来源：#3800 §2.1 Owner 裁定（评论 6975368043）第 3 条；#4045「接口与不变量」第 2 条、
「真实入口与验收」第 2 条；接管登记见 #4045 评论 6979036821

## Decision

只落**不被 Owner 裁决阻塞**的一半：把一次受控兼容补跑的产物登记成第四种分桶维度
（派生族），并让它进容量读数；回收判据一律不动。

1. `backend/storage_families.py` 新增 `COMPAT_RERUNS_FAMILY` / `DERIVED_FAMILIES`，
   `ALL_FAMILIES` 由四类并列，族根归 `log`（15.4）。该族**不得**加入 `RUN_FAMILIES`：
   目录名带 run，但独立存活单元是「实际 PlanRun × 平台 × 一笔补跑登记」。
2. `backend/scripts/measure_center_storage.py` 新增 `_derived_unit_dirs` /
   `collect_derived_usage`，按 `run/platform/r{id}` 单列 `derived_family_usage`
   （units / distinct_runs / bytes / files / dirs / top_units），**不进** `run_counts`
   与 `top_runs_by_bytes`；叶子只认 `r` + 十进制，非登记形态的目录一律**少报**而不是
   当成单元（避免 #2188 把 `jobs/{job_id}` 报成 run 的第三种读数）。族根存在但为空时仍
   出现 `independent_units=0`——「族没被用过」与「族不在读数里」是两种事实。
   `not_covered` 增加一条：可回收性需要 `compat_rerun` 台账，本脚本不判。
3. 判别守卫两条：单源守卫新增
   `test_compat_reruns_is_derived_and_never_run_purged`（含对清理器与测量脚本两侧的
   `not in RUN_FAMILIES`、导入身份同一）；新增
   `backend/tests/scheduler/test_b8_4045_derived_family_survives_run_purge.py` 三条实测
   ——run 清理后派生族整棵子树字节不变、误并进 `RUN_FAMILIES` 立刻被删（变异自证，
   证明红线来自族清单而非巧合）、容量按单元且 `run_counts` 不含该族。
4. `docs/design/2026-scan-upload-merge-contract.md` 中心存储路径表补该族一行，并把
   「派生族回收须逐笔判据、锚点待裁决前不得接入自动窗口」写进正文。

本 PR 不做：回收判据与保留期来源、`compat_rerun` 台账行的任何删除、原件供给通路、
`backend/services/compat_rerun.py` 的源选择语义。

## Alternatives

- **把 `compat_reruns` 直接加进 `RUN_FAMILIES`**：省事，但一笔仍在重试或仍被引用的补跑
  产物会随 run 被无条件删掉，违反 Owner 第 3 条；且 run 清理器不需要为此改代码就会误删。
- **只加 `ALL_FAMILIES`、不设 `DERIVED_FAMILIES`**：总量可见但键维度被压平，Dry-run
  候选（按单元）与容量读数（按 run）无法逐条对拍，等于把「有清理、无观测」换成
  「有观测、无法对账」。
- **现在就把回收一起实现**（照搬在窗 WIP 的 `compat_rerun_retention`）：其到期锚点取
  `plan_run_retention_days`，在生产覆盖 `36500` 下候选恒空；`compat_rerun.plan_run_id`
  无外键、无人删该行，run 行被清后按 `plan_run_missing` 永不回收。裁决未下之前合入即
  合入一个「看起来安全的永不回收」，故拆成第 2 刀。
- **直接移植另一会话未提交的工作树内容**：无 HEAD 绑定、作者与复核人不可分，
  本单只把它作为 prior art 记录哈希（#4045 评论 6979036821），实现按契约重写。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/tests/scheduler/test_b8_4045_derived_family_survives_run_purge.py tests/test_center_storage_families_single_source.py backend/tests/test_measure_center_storage.py -q`
  → `37 passed`（含新三条实测）。
- 邻面回归：`backend/tests/services/test_b8_g0a_storage_roots.py
  backend/tests/services/test_b8_g7_partition_consumers.py
  backend/tests/scheduler/test_retention_cleanup.py -q` → `99 passed`。
- **判别力的现场证据**：文档行未加时，`test_path_contract_doc_lists_every_family`
  实测红（`路径契约表缺族：['compat_reruns']`），补表后转绿——红绿来自守卫本身，
  不是断言改写。
- `./scripts/project_python.sh scripts/run_gates.py check:quick` 结果与本 PR 的六项
  required CI 一并在 PR 评论里给出；CI 未回的结果一律记为 pending，不记为通过。
- 未执行：生产 15.4 上的实跑计量（本单不登录生产机、不连生产库）；任何清理动作
  （派生族目前只计量，无回收器）。

## Revisit

- 第 2 刀（回收判据）等 Owner 对 #4045 评论 6978340866 的 (a)/(b)/(c) 表态：
  本族独立保留参数 / 「run 行不存在即超期已证明」+ 登记时快照 run 状态 / 试点窗口挂到
  `plan-run-retention-disabled`（#3230 G4）之后。选 (b) 需要给 `compat_rerun` 加快照列
  （迁移 + 回填策略）。
- 两个口径待 Owner 裁决：attempt 用尽的 `FAILED` 是否可列候选；回收后台账行是否需要
  「已回收」标记，以免 `artifact_absent_or_unsafe` 噪音掩盖真实缺口。
- 在窗 Execution `B8-4045`（qoder）仍未提交，请 Owner 或该会话
  `ai_work.py finish --id B8-4045 --abandon` 收口；本会话不代他人收口在窗记录。
- 出口条件：第 2 刀落地并通过引用保护验收后，才讨论自动回收窗口。
