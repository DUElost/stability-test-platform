# #1664 CI 接线 × 测试库护栏的 PR 路径守卫

Status: implemented
Class: bug-fix

## Decision

在 PR 路径的 `tests/` 下新增 `tests/test_ci_test_db_guard_wiring.py`，把
`backend-test` 的 env 接线与 `db_url_guard` 的语义锚成 PR 可拦的契约。落实 #1547
自己提出、但 PR #1566 未采纳的「附带建议（防复发）」。

**覆盖两个方向**（7 例）：

1. **护栏语义**（5 例，直测 `backend/core/db_url_guard.py` 纯函数）：
   同库拒载 / 不同库放行 / `runtime_database_url` 缺省放行 / 库名不含 test 拒载 /
   非 PG scheme 拒载；
2. **CI 接线**（2 例，解析 `ci.yml`）：`backend-test` 的 **job 级 env 不得含
   `DATABASE_URL`**、必须含 `TEST_DATABASE_URL`；并加一例防止解析器静默失效。

**为什么落在 `tests/` 而非 `backend/tests/`**：`backend/tests/` 只在夜间
`backend-test` 跑（`ci.yml` `if: github.event_name != 'pull_request'`），
而本单要解决的恰是「该类回归在 PR 阶段零覆盖」。#1569（PR #1578）刚把根 `tests/`
的离线子集接进 `pr-agent-tests`，本文件因此**自动进入 PR 路径**——沿用同一判据
（纯离线 + 秒级：7 例 0.04s，无 docker、无 DB）。

**关键设计点：只认 job 级 env**。#1566 的正确形态是「job 级不设 `DATABASE_URL`，
确实需要的两处（alembic migrate / agent tests）改**步骤级**注入」。因此守卫必须
区分 job 级与步骤级，否则会把正确接线误判为违规。`_backend_test_job_env_keys()`
按缩进解析，只取 job 直属 `env:` 块的键：

- 正确接线 → `{JWT_SECRET_KEY, TESTING, TEST_DATABASE_URL}`（13 行处的步骤级
  `DATABASE_URL` 正确排除在外）；
- 撤销 #1566 → 集合含 `DATABASE_URL` → 红灯。

**为什么不是「加一个 `--collect-only` step」**：#1547 原文的两个备选之一是在 PR CI
加 `python -m pytest backend/tests --collect-only`。实测该命令**能**在无 DB 下跑
（2341 例 6.15s），但它验证的是「当前接线能否导入 conftest」，**不锁定接线本身**
——有人把 `DATABASE_URL` 加回 job 级后，只要没在同一 PR 触发该 step 的失败语义
（例如后续又把 `TEST_DATABASE_URL` 改掉），契约仍可能漂移。本文件直接断言接线
文本，红灯归因明确（错在 ci.yml 的哪一行），且不依赖 conftest 能否导入
（后者会启动 testcontainers，与「纯离线」判据冲突——实测确认本文件全程不 import
conftest）。

## Alternatives

- **在 PR CI 加 `--collect-only` step（#1547 原文备选）** → 否决：会 import
  `backend/tests/conftest.py`，在无 `TEST_DATABASE_URL` 时走 testcontainers 兜底
  启动真实容器（`conftest.py:93-100`），与「纯离线、秒级」判据冲突，且给 PR 路径
  引入 docker 依赖；同时它只验「当前接线可用」而非「接线正确」，归因弱。
- **在 `pr-agent-tests` 里 import conftest 的解析函数**（#1547 原文备选 1）→
  否决：同上，import conftest 即触发容器兜底；且需要为 PR job 额外铺设
  `TEST_DATABASE_URL`，扩大 PR job 的环境面。
- **把 `backend/tests/core/test_test_db_guard.py` 前移 PR 路径** → 部分否决：那批
  用例测的是护栏语义（本文件第 1 组已等价覆盖且更轻），但它测不到「ci.yml 接线」
  这一真正的回归源（#1547 的成因是接线，不是护栏逻辑）。
- **改 `run_gates` 加一个本地 gate** → 否决：本单缺口在 CI 侧（夜间 job 的 env），
  本地 gate 无 ci.yml 的 job 级 env 可断言；且 #1569 已确立「CI 与本地同口径」的
  形态，此处不需要新 gate。
- **直接在 ci.yml 加注释警告** → 否决：注释不拦回归，#1547 的注释（`:45-50`）
  已经写得很清楚，仍不足以防止再次误加——需要机器可判的红灯。

## Verification

- `python -m pytest tests/test_ci_test_db_guard_wiring.py -q` → **7 passed**（0.04s，纯离线）；
- **红绿双向（本单核心自证）**：临时把 `DATABASE_URL` 加回 `backend-test` 的 job 级
  env（模拟撤销 #1566）→ 测试**红灯**，报错直指
  `assert 'DATABASE_URL' not in {'DATABASE_URL', ...}`；还原后 **7 passed**；
- **无误报**：正确接线下解析出的 job 级键为
  `['JWT_SECRET_KEY', 'TESTING', 'TEST_DATABASE_URL']`——13/86 行处的**步骤级**
  `DATABASE_URL` 被正确排除；
- **护栏语义独立复现**：#1547 的失败形态
  （`DATABASE_URL == TEST_DATABASE_URL`）经 `pytest backend/tests --collect-only`
  实测仍 `exit 4` + `UnsafeTestDatabaseUrl`（证明守卫锁的是真实存在的失败模式，
  非假想契约）；
- 全量离线子集：`python -m pytest tests/ -q --ignore=tests/test_alembic_upgrade.py`
  → **217 passed**；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿；
- `ruff check tests/test_ci_test_db_guard_wiring.py` → All checks passed。

## Revisit

- **解析器的结构耦合**：`_backend_test_job_env_keys()` 依赖 `ci.yml` 的缩进与
  job 名（`^  backend-test:`）。已加 `test_ci_yml_is_parseable_and_job_found`
  兜底（解析为空即红），另对三种形态做过边界验证（合成 YAML 直接喂解析逻辑，
  不改文件）：①步骤级 `DATABASE_URL` 紧随 job 级 env 之后 → 正确排除；
  ②job 级自带 `DATABASE_URL` → 正确捕获；③job 级 env 之后直接接下一个 job
  （无 `steps:`）→ 正确取键。若将来 job 改名或引入 YAML 锚点/复用，应改为按
  YAML 解析（本单刻意用行解析以避免在 PR 路径引入额外解析依赖——
  `pyyaml` 已在 dev lock 中，届时可直接换）。
- **同类「接线 × 护栏」模式**：本单只锁了 `backend-test` 这一处。若后续
  `frontend-check` / `docker-build` 或新增 job 也出现「环境变量接线错误只在夜间
  暴露」的形态，应按同一模式补守卫，而不是逐个救火。
- **#1525（PR 门禁不跑 backend-test 的取舍）** 仍是开放决策：本单不改变「PR 不跑
  全量后端」的取舍，只是把其中**廉价可离线验证**的那部分（护栏接线）提前到 PR。
  若日后引入 Merge Queue 或调整门禁范围，本文件的归属（`tests/` 离线子集）应
  一并复核。
