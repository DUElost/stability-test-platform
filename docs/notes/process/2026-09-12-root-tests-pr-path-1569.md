# #1569 根 tests/ 离线子集前移 PR 路径

Status: implemented
Class: process

## Decision

把根 `tests/` 的**离线子集**前移到 PR required check `pr-agent-tests`，取代原先
逐步挑选的三个 step（lock 卫生 ×2 + Prometheus 契约），并在 `scripts/run_gates.py`
的 `repo-tests` gate 取同一口径。

```yaml
- name: Run repo-level tests
  run: |
    python -m pytest tests/ -q \
      --ignore=tests/test_alembic_upgrade.py \
      -p no:cacheprovider
```

**触发本单的事实**：#1541 的两例红测试随 #1482 合入 main 后长时间无人拦截。
根因不是「测试写错」（已由 #1541/#1542 收口），而是**这个错误没有任何自动环节
能发现**：PR 路径只跑 3 个根测试文件，设计上的夜间兜底又自身红着（#1547
收集期 ImportError，后端/agent/仓库级测试全被跳过）。

**为什么能整目录搬（推翻原注释的判据）**：`ci.yml:255-259` 原注释称「`tests/`
其余文件与迁移/部署契约耦合，需 PG 的依赖归夜间」。经实测**该判断不成立**：

- `tests/` 无根 conftest，无文件引用 `TEST_DATABASE_URL`；
- 除 `test_alembic_upgrade.py` 外**全部纯离线**，177 例 **8.2s**（剥离
  `DATABASE_URL`/`TEST_DATABASE_URL` 亦可跑）；
- 原文所举「只挑这两个文件，不整目录搬」的理由因此转为不成立——判据仍是既有的
  「纯离线 + 秒级」（lock 卫生测试前移时立的），只是按实测把范围放大到实际满足
  该判据的全集。合并路径 ~2 分钟预算未破（净增约 8s，且两个旧 step 被取代）。

**为什么排除 `test_alembic_upgrade.py`**：真实起 `postgres:16` testcontainer
（~5-6s）且**无 skip 兜底**——runner 无 docker 时会硬失败。归夜间 `backend-test`
（该 job 本就有 PG service 与 docker）。

**排除粒度取文件名而非白名单**：新增测试文件**默认进入** PR 路径。已实测验证——
临时放入一个必失败的 `tests/test_zzz_probe_newfile.py`，`177 passed` 变
`1 failed, 177 passed`，证明新文件确实被自动覆盖，本单缺口不会以「新文件默认
无人看守」的形态复发。（黑名单式 `--ignore` 的固有代价是**新引入的需 docker
文件会被误判**；以注释写明该约定，供后续新增时按需补 `--ignore`。）

**同步修改**：

1. `tools/dev/check_governance_surface.py` 的 `GATE_TO_CI_ANCHOR["prom-alerts"]`
   锚点由 `"Prometheus 告警规则契约"` 改为 `"Run repo-level tests"`——该 step 已
   删除，锚点必须随 S5x 双向断言更新（否则治理门禁当场红）；
2. `scripts/run_gates.py` 的 `repo-tests` 由 `pytest tests/ -v` 改为与 CI 同口径的
   离线子集——本地与 CI 取同一集合，避免口径漂移造成「本地绿、CI 红」。

## Alternatives

- **继续逐步挑选文件（维持原姿态）** → 否决：本单缺口正是「挑剩的文件无人看守」
  的产物；每漏一个就要一次红测试事故来发现，且新增文件的默认状态是「不被覆盖」。
- **把整个 `tests/` 都搬进 PR 路径（含 `test_alembic_upgrade.py`）** → 否决：
  该文件真实起 testcontainer，runner 无 docker 时硬失败（无 skip 兜底），会把
  PR 路径变成环境依赖敏感的红灯源，且违背「纯离线」判据。
- **给 `test_alembic_upgrade.py` 补 skip 兜底后一并前移** → 否决（本单范围内）：
  给一个迁移正确性守卫加 skip 会让它「无 docker 即静默不跑」，而它恰是空库迁移
  守卫的同类；`pr-migrate-empty-db` 已独立承担 PR 侧的迁移守卫，真正的 testcontainer
  回放归夜间更合适。若日后要前移，应作为独立裁决（需先解决 docker 可用性协商）。
- **改为按 diff 触发（只跑受影响子集）** → 保留为备选但本次不采纳：`tests/` 全集
  实测仅 8.2s，按 diff 触发的判据维护成本（路径→文件的映射会漂移）高于其节省，
  且会重新引入「改动未映射到位即漏跑」的同类缺口。若日后 `tests/` 显著膨胀再议。
- **只改 CI 不改 run_gates** → 否决：本地 `check:pr`/`check:full` 与 CI 口径分叉，
  会出现本地全量绿、CI 子集红（或反向）的判读混乱。

## Verification

- **CI 命令原样实测**（剥离 `DATABASE_URL`/`TEST_DATABASE_URL`）：
  `python -m pytest tests/ -q --ignore=tests/test_alembic_upgrade.py -p no:cacheprovider`
  → **177 passed in 8.22s**；
- **排除有效性**：`--collect-only` 中 `test_alembic_upgrade` 命中数 **0**；
- **新增文件默认受覆盖**（本单核心性质）：临时放入必失败的
  `tests/test_zzz_probe_newfile.py` → `1 failed, 177 passed`，证明黑名单式排除下
  新文件自动进入 PR 路径；探针文件已删除；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿
  （含 `GATE_TO_CI_ANCHOR` 双向断言：新锚点 `"Run repo-level tests"` 在 ci.yml 命中）；
- `python tools/dev/check_governance_surface.py --self-test` → 14 条规则红/绿双向通过；
- `ruff check tools/dev/check_governance_surface.py scripts/run_gates.py` → All checks passed；
- ci.yml YAML 解析通过，`pr-agent-tests` steps 为
  `Install dependencies / Run agent tests / Run repo-level tests`；
- **未完成项（如实标注）**：`scripts/run_gates.py repo-tests` 在本 worktree 跑到
  后续 `eslint` 时因无 `frontend/node_modules` 而 not found（worktree 不共享依赖，
  属环境缺口、与本改动无关）——该 gate 的 pytest 部分已单独按同上命令验证通过。

## Revisit

- **本单未修 #1547**（夜间 `backend-test` 因 `db_url_guard` 与 ci.yml 同库接线
  冲突而收集期 ImportError、全量跳过）：那是独立 P0，修复它才能让「需 PG 的
  测试」真正回到夜间防线。本单只解决 PR 路径侧；两者都需收口，
  #1547 修好前，需 PG/网络的文件仍处于无人看守状态。
- **黑名单粒度**：若后续新增真实需 docker/PG 的根测试文件，须同步补 `--ignore`
  并在本注释登记；出现第二个此类文件时，应重估为按 marker（如
  `-m "not needs_docker"`）表达，而非继续堆 `--ignore`。
- **夜间兜底恢复后**：应复核本单前移是否与夜间 `backend-test` 形成合理分工
  （PR 拦离线、夜间跑全量），并删除 `ci.yml` 中「其余文件归夜间」的过期判据。
