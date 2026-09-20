# 源扫描守卫第七批：部署面与 CI 面 11 处迁移，并把「循环形态」收敛成 per-file 锚点表（#2639 第七批）

Status: implemented
Class: testing

- 日期：2026-09-20
- 关联：`#2639`（本族立单，已 CLOSED；无单推进）、第四批
  `docs/notes/testing/2026-09-20-source-scan-batch4-playbook-priv-2639.md`、第五批（口径轴二）
  `docs/notes/testing/2026-09-20-source-scan-tmp-path-axis2-2639.md`、第六批（三类不可照迁形态）
  `docs/notes/testing/2026-09-20-source-scan-batch6-three-classes-2639.md`
- 落点：`backend/tests/test_deployment_files.py:1`、`backend/tests/test_ci_and_test_harness_files.py:1`、
  `tests/test_source_scan_anchor_ratchet.py:1`
- 堆叠关系：基于第六批分支（`#2928` → `test/2639-source-scan-batch6`）之上，须排在两者之后合入

## Decision

**迁移 11 处**（部署文件 6 + CI/测试脚手架 5），锚点仍一律编在「取代被禁形态的那一样东西」上：

| 位点 | 锚点（替代物） | 禁词 |
|---|---|---|
| `backend/tests/test_deployment_files.py:35` | `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD` | `POSTGRES_PASSWORD: password` |
| `backend/tests/test_deployment_files.py:168` | `location /assets/`（三个 nginx 模板各命中 1 次） | `…immutable" always` |
| `backend/tests/test_deployment_files.py:183` | `http://server:8000` | `http://backend:8000` |
| `backend/tests/test_deployment_files.py:210`、`:214` | `<server-name>` / `<tls-cert-path>` | `stp.example.com`、`/etc/letsencrypt/live/` |
| `backend/tests/test_deployment_files.py:279` | **per-file 锚点表** `_DOC_ANCHORS` | 四条 `cp …` 原样拷贝形态（#1256） |
| `backend/tests/test_ci_and_test_harness_files.py:29` | `PostgresContainer` | `ALLOW_SQLITE_TESTS` |
| `backend/tests/test_ci_and_test_harness_files.py:54` | `## 测试`（人工清单取代已下线的 PR Agent 文案） | `PR-Agent`、`PR Agent`、`/review`、`security concerns 会阻断合入` |

`BASELINE` **14 文件/31 处 → 12 文件/20 处**。

**第六批留下的「循环多位点需要 per-script 锚点表」，本批做出来并把它收窄成一条判据**：
循环里的守卫要不要锚点表，取决于**被扫集合有没有共同的替代物**——

- 有共同替代物时**不需要表**：三个 nginx 模板都以 `location /assets/` 为替代物（实测各 1 次），
  一个锚点就够；
- 没有共同替代物时**必须有表**：两份部署文档表达同一个「渲染而非拷贝」契约用的是**不同变量**
  （`$STP_DEPLOY_ROOT` 只在 checklist、`$CONTROL_DIR` 只在 runbook，实测彼此 0 命中）。
  于是 `_DOC_ANCHORS` 逐文件带锚点，且**表里缺项就大声失败**（`assert anchor, "…不在 per-file 锚点表里…"`）——
  没有锚点的循环判据正是本族要消灭的恒真空守，宁可红也不静默放过；新增被扫文档时会立刻被这条要求拦住。

**`SITE_FLOOR` 30 → 10，并给出本族的终止条件。** 地板的作用不是陪每批迁移一路下调，而是「判据被大面积削弱」
的报警器。剩余 20 处里有 **11 处本就不该迁**（A 类不可变已发布脚本版本 7、i 类去注释后判 1、
循环无共同锚点 3），那就是**永久残余**；地板取 10 落在残余之下，意味着 `BASELINE` 收敛到那 11 处即为
本族终点——**棘轮的合格线不是清零，而是「残余之外没有一处裸断言」**。继续把 30 这样的数字留着，
下一批一迁就撞红，只会逼人多改一处判据数字（那正是最容易改漏的地方）。

## Alternatives

- **`SITE_FLOOR = 20`（贴现状）**（否决）：等于把兜底闸当第二个棘轮养，每批都要顺手改，
  且永远在真实残余之上——一旦判据静默失效到残余以下（例如某扫描根失效）它能响，但平时就是催人多改一处。
- **给 `SourceGuard` 加「多锚点/按视图断言」的 API 来解决 i 类**（否决，本批不做）：
  第六批那 1 处用现成 API 表达不了是**事实**，但为一个位点扩公共面不划算；扩能力需自带夹具与变异，另批评估。
- **把两份文档拆成两个独立测试**（否决）：循环是**有意**的覆盖面（新增部署文档应自动被扫）；
  拆开后新增文件会静默脱离检查，比表缺项更糟。
- **用 `set -euo pipefail` 当 nginx/脚本的通用锚点**（否决）：它只证明「文件还在」，不证明
  「判的是同一段契约」；替代物锚点的价值恰恰在后者（第四批定下的编法）。
- **顺手修 `backend/tests/test_ci_and_test_harness_files.py` 里模板禁词的漏检**（否决）：
  本批只改形态不改语义，`## 测试` 这条锚点是否过宽留作 Revisit 观察。

## Verification

- 两个目标文件：`env -u DATABASE_URL /home/debian13/stability-test-platform/.venv/bin/python -m pytest backend/tests/test_deployment_files.py backend/tests/test_ci_and_test_harness_files.py -q` → **23 passed**
- 连同棘轮三文件：`… tests/test_source_scan_anchor_ratchet.py` → **33 passed**
- 剩余存量实测 **12 文件 / 20 处**，与 `BASELINE` 逐文件注释之和 20 相等、集合与 `BASELINE` 完全一致
  （`test_offenders_equal_baseline_no_growth_no_staleness` 双向通过）
- **变异 7 条，逐条红且红在正确的因上**，4 个被改文件逐字节还原（`restored-identical: True`），
  还原后复跑 **33 passed**：
  1. compose 锚点改成不存在的串 → `AnchorDrift`
  2. 往 `docker-compose.yml` 塞回写死口令 → `FormRegression`
  3. 从 `_DOC_ANCHORS` 抽掉一项 → 报 `docs/preprod-drill-runbook.md 不在 per-file 锚点表里`（**表缺项大声失败**）
  4. 把三个模板共用的锚点换成只在单个模板存在的 `listen 443 ssl` → `AnchorDrift`（证明循环里**逐文件**建了守卫）
  5. 忘记下调 `BASELINE`（把 `test_deployment_files.py` 加回去）→ staleness 分支点名它——证明迁移是真的
  6. 一条退回裸 `assert "ALLOW_SQLITE_TESTS" not in conftest` → growth 分支点名该文件（测试自身仍绿）
  7. 去掉 `.anchored()` → `GuardMisuse: 守卫写法不合法（空守）`
- 门禁（rebase 到含 #2944 的 `origin/main` 后复跑）：`scripts/run_gates.py check:quick` → **12 门通过**；
  `scripts/run_gates.py check:pr` → **21 门通过**；`tools/dev/check_governance_surface.py --check` → 阻塞项全绿
- 根 `tests/` 全量 → **1704 passed**；`backend/tests/ --collect-only -q` → **3428 collected** 无错
  （本批两个目标文件属夜间面，整目录全量未跑——那是夜间窗口的事，不留成本在本单）

## Revisit

- **常规可迁的只剩 9 处 / 7 文件**：`backend/tests/services/test_agent_installer.py`(3)、
  `test_dedup_scan_merge.py`(1)、`test_job_log_signal.py`(1)、`tests/test_dev_bootstrap_seed.py`(1)、
  `tests/test_pg_restore_drill.py`(1)、`tests/test_script_seed_static_guards.py`(1)、
  `tests/test_seed_revision_version_guard.py`(1)。下一批做完，`BASELINE` 就落到「11 处永久残余」，
  本族到此**该停**——剩下的不是债务，是三类被论证过不该照迁的形态。
- **`test_deploy_scripts.py` 那 3 处**：本批复核后其中 2 处其实有可用共同替代物
  （`>&2` 与 heredoc 目标形态），只有 `:125` 的站点标识禁词确实无共同锚点；另批按「有替代物的先迁」处理，
  并顺带查 `tests/test_deploy_scripts.py:123` 的 `city-b` 死分支。
- **`## 测试` 作为锚点是否过宽**：它覆盖整份模板。若哪天模板改名或章节重构，判据会以 `AnchorDrift`
  报「用例过期」——那是**正确**的行为，只是提醒下一个人重挑替代物，不必现在收紧。
- 正向形态 `assert "<字面量>" in 源码`（锚点漂移必恒真）仍无判据，需单独立单；
  它是本族唯一还没有解的缺口。
