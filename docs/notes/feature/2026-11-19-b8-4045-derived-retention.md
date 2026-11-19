# B8 U5 #4045 第 2 刀：compat_reruns 派生族的逐笔留存判定与人工确认回收

Status: implemented
Class: feature

日期：2026-11-19　基线：`feat/4045-b8-u5-derived-family-capacity@64299c40f`（#4050，第 1 刀）　批次：#3800 / #4045
判据来源：#3800 §2.1 第 3 条、#4045「接口与不变量」第 2 条与「真实入口与验收」第 2 条、
第 2 刀裁决单（#4045 评论 6979734214）。裁决按 Owner 裁定的推荐组合 **(a)+(c)、FAILED 一律保留、3a** 落实。

## Decision

1. **判定与回收只有一套**：`backend/services/compat_rerun_retention.py`。候选必须同时满足六条，
   缺一即保留，且**每条理由串独立**、不合并成一句话：
   `in_flight`（PENDING/RUNNING）、`terminal_failed_evidence`、`completion_time_unknown`、
   `rerun_within_retention`、`plan_run_missing_unprovable`、`plan_run_not_terminal`、
   `plan_run_within_retention`、`i4_protected`、`chain_referenced`、`artifact_absent_or_unsafe`。
   run 终态集合与到期锚点口径**照抄现行 run 级清理**（`cron_scheduler.py:455` 的
   `{SUCCESS, FAILED, PARTIAL_SUCCESS}` + `started_at < cutoff`），不另造判据。
2. **到期锚点 = (a)**：新增 `COMPAT_RERUNS_RETENTION_DAYS`（默认 `3`，与 run 级代码默认同值）
   与 `COMPAT_RERUN_PURGE_BATCH_LIMIT`（默认 `20`，下界 `1`），两值都登记进
   `backend/.env.example` 并由 `env_inventory` 生成块收录。**不得**继承
   `PLAN_RUN_RETENTION_DAYS`：生产覆盖为 36500，沿用同一旋钮会让候选恒空。
3. **空清单必须可解释**：报告固定带 `anchor_days` / `anchor_note`。锚点 ≥3650 天或为 0 时
   输出显式警告；`0` 沿用 PlanRun/audit 家族既有语义（cutoff=now，该层全量到期），
   不用它表示「关闭」。
4. **`FAILED` 一律保留**（裁决 2）：理由串为 `terminal_failed_evidence`，纠正被误标的
   `completion_time_unknown`——失败是事故证据，不是「时间未知」的例外分支。
5. **回收不留噪音 = 3a**：`compat_rerun.artifact_purged_at`（迁移 `b8u5a1c2d3e4`，
   `down_revision=b8u2h1c2a3p4`，基线衔接时从 `b8g8c1d2e3f4` 改挂到 #4044 链后）记录回收时间；
   登记行**不删**、状态**不改**；
   已回收项从「保留」栏移到独立「已回收」栏，重复标记不覆盖第一次时间。
   该列**不建索引**：表体量是 Owner 逐次发起的补跑数，且判定本就是全表读；建索引只会在
   `check_schema_sync` 里多出基线外漂移项（实测过：加索引 → `remove_index|compat_rerun|...` 判红）。
6. **回收只能逐笔点名**：`python -m backend.scripts.compat_rerun_retention
   --purge-rerun-id <id> --operator <name>`。缺 operator 即拒（审计要落人）；空列表与超批次上限
   **整笔拒绝**，不做部分执行；每笔删除前用当前库状态重判定（**Dry-run 输出不是删除授权**）；
   只删 `r{id}/` 叶子与腾空后的 `{platform}/`、`{run}/`，族根与 `evidence/`、`devices/`、
   `dedup/`、`source_records[].source_path` 指向的共享源一律不动；审计
   `action="compat_rerun_purge"` 以 `strict=True` 写（ADR-0038 D2：写不进审计就不算完成）。
7. **顺序取舍**：先删目录、后落标记与审计。提交失败时报文显式给出
   `already_removed=true` 并说明「下一次 Dry-run 会以 `artifact_absent_or_unsafe` 呈现」——
   失败形态是**响亮**的。反向顺序（先标记后删）会在提交失败时留下「说已回收、文件还在」
   的静默泄漏，故不采用。
8. 本刀**不做**：调度任务/自动窗口（(c)：登记为过渡台账 `compat-reruns-auto-window`，
   出口 `issue:#3230`，due 2026-12-31）、API 端点与前端类型、`compat_rerun.py` 源选择语义、
   原件供给通路（等 §3 签字）。

## Alternatives

- **(b) 登记时快照 `run_status` / `run_started_at`**：能根除 `plan_run_missing_unprovable`
  （run 行被清理后仍可从登记行证明超期），代价是迁移 + 存量回填 + 老行永久保留。Owner 选 (a)+(c)，
  所以本刀把「不可证明」做成**可观测计数**而不是静默保留。
- **沿用 run 级保留天数**：最省代码，但生产下候选恒空，报表会被读成「没有要回收的产物」。
- **回收即删登记行**：最省磁盘，但破坏审计链与 I1 累计发生数可追溯（且与 §2.1 的
  「登记不覆盖原交付权威」相冲）。
- **把回收状态塞进 `coverage_scope` JSONB**：零迁移，但「已回收」是生命周期事实而不是
  统计快照，塞进 JSONB 等于放弃可查询性与约束。
- **自动窗口现在就接上**：与 (c) 冲突——引用保护闭环在生产尚未跑通过一次四路对拍。

## Verification

- `backend/tests/services/test_b8_4045_derived_retention.py`：**17 条用例**，逐条对应
  #4045 验收第 2 条——双超期候选 + 四路对拍字段齐全；**单边超期**两种各一条；完成时间未知；
  run 非终态；**在途**（PENDING 与 RUNNING 各一）与 `FAILED` 理由串区分；I4 两种形态
  （代表发布 RETRYING / 补充轮未闭）；**链上引用**与 run 行缺失（后者同时断言登记行仍在、
  run 行已无，把累积根因钉住）；目录缺失与 symlink 目录；已回收栏与重复标记；回收只动点名
  叶子且共享源/`evidence/`/`devices/`/兄弟补跑产物字节不变、父目录腾空时机、审计落库；
  空列表与超上限整笔拒绝；**Dry-run 不构成授权**（看到候选后改成未到期 → 回收被拒、目录仍在）；
  锚点 36500/0 的 `anchor_note`；**变异自证**（同一份合格数据逐项打开五个条件，理由串逐一翻转，
  末尾反证：条件全不开时新的一笔仍是候选）。
- 邻面：`60 passed`（第 1 刀 survival 测试、G8 补跑与路由探针、族清单单源守卫）；
  全量相关面此前 `124 passed`。
- 门禁：`check:quick` 16 gates 通过；`tools/dev/check_pr_migrate.py` 本地真跑通过
  （postgres:16 空库 `alembic upgrade head` + schema 比对回到基线 5 项、无新增漂移 + seed 身份）。
- 基线衔接（2026-11-19，合入 `origin/main=a902a3183` 后重跑）：`b8u5a1c2d3e4` 的
  `down_revision` 由 `b8g8c1d2e3f4` 改挂 `b8u2h1c2a3p4`（#4044），`alembic heads` 全仓单 head；
  一次性 postgres:16 空库上 `upgrade head → downgrade -1 → upgrade head` 往返通过，
  `artifact_purged_at` 列随 downgrade 消失、随 upgrade 恢复（nullable）；迁移契约四件套
  `20 passed`、本刀留存测试 `17 passed`、邻面（G8 补跑/路由探针/族清单单源/调度设置）
  `58 passed`、`check:quick` 16 gates 复跑通过。
- 基线衔接暴露并补齐的登记缺口（CI `pr-migrate-empty-db` 第 10 步判红，非改链引入）：写侧
  `record_audit(action="compat_rerun_purge", resource_type="compat_rerun")` 此前只落
  调用点、未过治理面登记。按 ADR-0049 D2 补两处——`compat_rerun` 进
  `core/audit.py::AUDIT_RESOURCE_TYPES`（#2778 写侧词表），`compat_rerun_purge` 进
  `scheduler/audit_log_cleanup.py::BUSINESS_ACTIONS_ALLOWLIST`（故意留 business 90d，
  与 G8 `compat_rerun_trigger` 同族先例）；CI 同款 9 文件守卫套件本地 `100 passed`。
- 清单：`env_inventory --write` 后 281 个读取名一致；`transitions` 11 条 / 5 在途。
- **未执行**（不得当作已完成）：生产 15.4 的计量与 Dry-run 实跑；任何一次真实试点回收
  （需 Owner 逐次确认）；#4050/#本 PR 的独立复核；required CI（合并前以 CI 为准）。

## Revisit

- 若 Owner 改选 **(b)**：本刀的 `plan_run_missing_unprovable` 计数应转为「超期已证明」的
  正向路径，需要一次迁移加两列 + 存量回填策略，并同步改判定与测试。
- 自动窗口放开的前提（`compat-reruns-auto-window` 出口 #3230）：生产跑通一次四路对拍、
  逐条保留判据在生产各命中过一次、以及 `COMPAT_RERUNS_RETENTION_DAYS` 短于 run 级保留时
  「run 仍在而补跑产物已回收」对报表 `Path` 消费方的影响已评估。
- 裁决 2 若改为「`FAILED` 可回收」：删除范围须二选一（整棵 `r{id}/`，或只删 `events/**`
  保留 `Result_MergeFiles_Compat_*.xls` 与 `compat_manifest.json`），并调整理由串。
- `artifact_purged_at` 是否暴露到 `CompatRerunOut` 与前端类型：本刀不动 API 面，等留存
  结果在 UI 有真实消费方时按 add-api-endpoint 流程同步。
