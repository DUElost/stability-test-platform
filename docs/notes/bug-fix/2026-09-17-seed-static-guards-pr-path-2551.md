# 种子迁移的静态契约守卫拆出容器文件 → 进 PR 路径（#2551，裁决 A）

Status: implemented
Class: bug-fix

## Decision

**#2551 的裁决是「只前移守卫类」**（用户裁定）。落地时先钉准缺口，结果与 issue 正文的假设**不同**：

- 根 `tests/` **本来就在 PR 路径上跑**（`pr-agent-tests` 的 "Run repo-level tests" 跑
  `pytest tests/`，只 `--ignore` 两个**起 testcontainers** 的文件）；
- 所以真正漏掉的不是「根 tests 全量」，而是**那两个被 ignore 的文件里可以离线跑的部分**——
  `tests/test_script_seed_governance.py` 的命中恰好是这一形态：它的红（#2527）**一天无人收口**，
  而它的失败用例 `test_new_seed_migrations_deactivating_versions_check_references` 是**纯静态分析**
  （只读迁移源码做 AST/文本判定，不碰数据库）。

**做法：拆文件**（而不是改 CI）。

1. 新增 `tests/test_script_seed_static_guards.py`：把原文件里**只读源码**的 5 条判据连同
   其辅助（`deactivation_sites` / `_seed_files_with_deactivation` / `_LEGACY_SEEDS_WITHOUT_REF_CHECK`
   / `_UPDATE_DEACTIVATE_RE` / `SEED_VERSIONS_DIR`）整体迁出——**无容器依赖**，因此自动被
   PR 路径的 `pytest tests/` 覆盖；
2. 原 `tests/test_script_seed_governance.py` 只留**真正需要 Postgres** 的 3 条（三分支的带数据行为），
   继续留在两份 `--ignore` 名单里（离线准入判据不变）；
3. **不加棘轮式的 CI 改动**：不动 `ci.yml`/`run_gates.py` 的名单——拆分本身就让新文件落进 PR 路径。

**顺带修掉一个自指坑**：拆出的文件里「本文件不得引入容器」这条守卫，第一版用**子串**判定，
被自己文件里出现的那个词命中而误红；改为判**导入行**（`^\s*(from|import)\s+testcontainers\b`），
与 `tests/test_offline_subset_guard.py` 的既有做法一致。

## Alternatives

- **A. 把整个容器文件加进 PR 路径**：不可行——PR 路径无 docker/PG（离线准入判据明文禁止，
  见 `tests/test_offline_subset_guard.py`），整包前移会硬失败。
- **B. 让 `pr-migrate-empty-db`（有 PG service）跑容器文件**：否决。该文件用的是
  `PostgresContainer(...)`（自己起容器）而非 job 提供的 PG，仍会硬失败；改造成本远高于拆分。
- **C. 按 issue 的候选 1/2/4（改动面映射 / 根 tests 分片 / SLA）**：均未采纳——实测证明
  「根 tests 已在 PR 路径」且缺的只是「被 ignore 文件里的离线部分」，A 的代价最小、覆盖面最准。
- **D. 只前移这一条用例（在同一文件里加 marker）**：否决。文件级 `--ignore` 与 marker 无关，
  仍不会跑；必须换文件。

## Verification

- **拆分后**：`tests/test_script_seed_static_guards.py` → **10 passed / 0.25s**（纯离线）；
  `tests/test_script_seed_governance.py` → **3 passed / 2.34s**（本机有 docker，按原样保留）。
- **接线证据（真跑 CI 的命令）**：
  `pytest tests/ --collect-only -q --ignore=tests/test_alembic_upgrade.py --ignore=tests/test_script_seed_governance.py`
  → 采集 **1435 条**，其中 **13 条来自新文件**（10 条判据 + 3 条拆分/接线守卫）。
  **拆分前**：该文件名只出现在两份 `--ignore` 名单里（`ci.yml:392`、`run_gates.py:256`）——
  即那 5 条判据在 PR 路径上**零信号**（#2527 实测代价：main 上常红一整天）。
- **新增 3 条守卫**（钉住拆分与接线）：本文件无容器**导入**；那 5 条静态判据不得回到容器文件；
  本文件名不得出现在两份 `--ignore` 名单里（反向断言容器文件仍在名单里）。
- **预算**：本文件在 PR 路径上的增量为 **0.25–0.36s**（#848 的 2 分钟预算不受影响）。
- `ruff check`、`check:quick` 通过；`tests/test_offline_subset_guard.py` 一并绿（准入判据未被破坏）。

## Revisit

- **同类形态**：`tests/test_alembic_upgrade.py`（另一个被 ignore 的文件）是否也混着**离线可跑**的判据？
  本单只按 #2551 的实例处理 seed 治理那一个；若下次有人再撞到「契约红只在夜间」，先按同一判据
  看那个文件里哪些用例真的需要容器。
- **未前移的面仍然只在夜间**：`backend/tests/`（控制面全量）与 `backend/agent/tests/` 的**一部分**
  仍只在夜间/`pr-agent-tests` 的窄集里跑——它们的可见性靠夜间 backstop 红自动开 issue（#2441 即该机制产出）。
  #2551 的候选 1（改动面映射）仍是唯一能覆盖它们的路子，本单未采纳（维护成本）。
- **拆分的边界判据**：本单按「是否需要 testcontainers」拆。若将来某条判据需要真实 PG 才能成立，
  它应留在容器文件，并在 Revisit 里说明「为什么不能静态化」。
