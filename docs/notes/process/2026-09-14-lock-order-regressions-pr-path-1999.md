# 锁序回归前移到 PR 路径（共享行加锁全序的可执行护栏）

Status: implemented
Class: process

## Decision

把 `#1959` / `#1980` / `#1985` 三条锁序回归挂进 **`pr-migrate-empty-db`** job，并新增
发现式接线守卫 `tests/test_lock_order_pr_path_contract.py`。

```
.github/workflows/ci.yml（pr-migrate-empty-db 末尾新增）
      - name: Run lock-order regressions (PostgreSQL)
        run: |
          env -u DATABASE_URL python -m pytest -q \
            backend/tests/services/test_abort_lock_order_1985.py \
            backend/tests/api/test_shared_row_lock_order_1980.py \
            backend/tests/scheduler/test_reconciler_renew_lock_order.py
```

### 为什么必须做

三条回归是 PostgreSQL-only（`DATABASE_URL` 为 sqlite 时整组 skip），而 PR 阶段此前
**没有任何 job 会跑它们**：`pr-agent-tests` 无 docker / 无 PG service（testcontainers
路径会硬失败），`pr-migrate-empty-db` 有 PG service 却不跑 pytest。于是「共享行加锁全序」
在**合入门禁里零覆盖**，只能等夜间 `backend-test`——而那条路径本身经历过 `#1547` 整段
ImportError 跳过的窗口（conftest 导入期 `db_url_guard` 同库拒载 → exit 4 → 同 job 后续
测试全部 skipped）。

与 `#1960` 的漏检根因同源：锁序环只在并发运行时成立，静态审查原理上产不出证据
（见 `docs/notes/architecture/2026-09-14-shared-row-lock-table.md`）。这类不变量必须由
**能真跑的护栏**承担。

### 为什么是 `pr-migrate-empty-db`

- 它是 PR 阶段**唯一有真实 PG service** 的 job，且**已经是 required check**——放进它才
  真正拦合入；
- 它已跑过 `alembic upgrade head`，用例需要的真实 schema 现成；
- **不新建 job**：新 job 不在分支保护里、不是 required check，等于不拦合入，还要多一个
  PG service；
- **不改 job 名**：分支保护按 check 名匹配，`pr-migrate-empty-db` 改名会让该 required
  check 永不汇报 → **所有 PR 卡死**。代价是锁序失败显示在这个名字下，故在步骤名与注释里
  写明「本步骤是锁序回归，不是迁移失败」。

### 为什么必须 `env -u DATABASE_URL`

该 job 级 `DATABASE_URL` 与 `TEST_DATABASE_URL` **同库**（都是 `stability_test`），而
`backend/tests/conftest.py` 导入期会调 `db_url_guard.guard_test_database_url(...,
runtime_database_url=os.getenv("DATABASE_URL"))`——同库直接拒载（pytest exit 4）。这正是
`#1547` 的原始成因，接线契约见 `tests/test_ci_test_db_guard_wiring.py`。

这些用例只认 `TEST_DATABASE_URL`（conftest 解析后会把 `DATABASE_URL` 覆盖为测试库），
所以剥掉该变量即可；用 `env -u` 与 `pr-agent-tests` 里 `env -i ...` 的既有写法同源。

### 守卫为什么用「文件名含 lock_order」而不是白名单

判据取**发现式命名约定**：新增第四个锁序回归只要按命名落位，守卫就要求它被接线——白名单
会在新增文件时静默失效，这正是本单要防的形态。代价与边界见 Revisit。

## Alternatives

- **新建 `pr-lock-order` job**：否决。不在分支保护里 → 不是 required check → 失败不拦合入，
  只剩「有人看仪表盘」；且要多起一个 PG service。
- **挂进 `pr-agent-tests`**：否决。该 job 无 docker、无 PG service，走 testcontainers 兜底
  会硬失败（`tests/test_offline_subset_guard.py` 记录的形态），等于把「零覆盖」换成
  「随机红」。
- **把整套 `backend/tests/` 前移**：否决。违反
  `docs/notes/process/2026-08-14-merge-path-attention-budget.md`（不得为合入前验证加长
  等待），实测该套件 ~9 分钟——而本单要的是**十秒级的窄子集**，与 `#1569`（根 `tests/`
  离线子集）前移同一取舍。
- **重命名 job 为 `pr-db-guards`**：否决。改名会让 required check 消失、所有 PR 卡死；
  若要改名，必须与分支保护同步，属独立裁决（见 Revisit）。
- **给三条用例打 marker、按 marker 选跑**：否决（本单）。要先改三条已合入的用例，且
  marker 同样要有人记得打；文件名判据在本仓已有 `test_offline_subset_guard.py` 的先例。
- **只写文档说明现状**：否决。现状正是「有回归、合入门禁不跑」的零覆盖本身，文档不能拦人。
- **顺带把 `plan_run × plan_run_host` / `device_leases × plan_run` 的新回归也加进来**：
  本单不加（后一对的形态反向被判定为可达性 ≈ 0，见共享行加锁表）；将来若加，按命名约定
  落位即可被守卫自动要求接线。

## Verification

- **守卫本体**：`pytest tests/test_lock_order_pr_path_contract.py -q` → **4 passed**。
- **三向负向对照**（证明守卫不是恒真，逐一破坏后恢复）：

  | 破坏方式 | 期望 | 实测 |
  |---|---|---|
  | 去掉步骤里的 `env -u DATABASE_URL` | `test_runner_step_neutralizes_same_db_database_url` 红 | ✓ 1 failed, 3 passed |
  | 把三个文件路径从 PR 步骤里移除 | 两条接线断言红 | ✓ 2 failed, 2 passed |
  | 新增第四个 `*_lock_order*.py` 但不接线 | 发现式覆盖断言红 | ✓ 1 failed, 3 passed |
  | 恢复后复跑 | 全绿 | ✓ 4 passed |

- **仓库面**：`pytest tests/ -q` → **566 passed**；`ruff check` 通过；
  `check_governance_surface.py --check` → `[OK]`；`check_invariant_diff.py --base origin/main`
  → 新增行无不变量违规；`yaml.safe_load(ci.yml)` 可解析且 job 集合完整。
- **GitHub Actions 实跑**（本 PR 的 `pull_request` 事件，job `pr-migrate-empty-db`
  104022241707）：新步骤输出 **`5 passed, 1 warning in 4.81s`** —— 用例**确实执行**，
  不是 sqlite skip 的假绿；`env -u DATABASE_URL` 有效绕开了 conftest 导入期的同库拒载。
  该 job 总耗时 52s；同 PR 的 `pr-agent-tests`（含新增根契约测试）3m33s、`lint` 38s、
  `pr-typecheck` 33s、`pr-compileall` 10s，全绿。
- **本机为何不做这一步**：本机无 docker，起不了 PG service 模拟该 job；而
  `workflow_dispatch` 不会触发 `if: github.event_name == 'pull_request'` 的 job，也无法
  用它验证。也就是说这次 PR 事件本身就是本单的验证手段，而不是事后补充。

## Revisit

- **命名约定之外的锁序回归**：判据只认文件名含 `lock_order`。若出现不按此命名的锁序
  回归（例如并进某个行为测试里），守卫看不到——届时改为 marker 或显式登记，别让它靠
  「有没有人记得」。
- **job 职责膨胀**：`pr-migrate-empty-db` 现承担「迁移可跑 + schema 同步 + 锁序不变量」。
  若再挂第三类 DB 行为检查，应评估拆出 `pr-db-guards` 并**与分支保护同步改名**（改名期间
  所有 PR 会卡在「等待 required check」，需先加新 check 再删旧 check）。
- **耗时红线**：本步骤现为十秒级。若锁序用例增长到分钟级，须回到
  `2026-08-14-merge-path-attention-budget.md` 重估，而不是默认「加了就一直加」。
- ~~**锁序测试自身的完成度**：三条回归在**默认本地配置**下仍会 skip（sqlite），因此不要
  依赖本地 `python -m pytest backend/tests/` 的绿——那是 skip 的绿。~~
  **（`#2022` 事实更正）本条断言不成立**：`backend/tests/conftest.py` 在导入测试模块**之前**
  已把 `DATABASE_URL` 覆盖为 testcontainers / CI 的 PG 库，`pytestmark` 里
  `startswith("sqlite")` 那条 skip 分支在本仓 harness 里**不可达**（实测：以
  `DATABASE_URL=sqlite:///…` 运行，用例照常真跑并通过）。无 docker 时是在 conftest 阶段
  报错，也不是 skip。所以本地 `pytest backend/tests/` 的绿是真跑过的绿；PR 阶段仍需本单的
  接线，理由只是 `pr-agent-tests` **没有 PG service**，与 skip 无关。
- **观测入口**：`stability_db_deadlock_total{engine}` 与 `StabilityDbDeadlockDetected`
  （`#1958`）应长期为 0；出现增量即回到共享行加锁表按行定位，先看本步骤是否已红。
