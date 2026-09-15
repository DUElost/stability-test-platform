# 锁等待观测回归前移到 PR 路径（守卫覆盖两类并发回归）

Status: implemented
Class: process

## Decision

两件事，都是承接 `#1999` 的同一类盲区（「只能被并发回归抓住的缺陷，其兜底只在夜间跑」）：

1. **接线**：把 `backend/tests/api/test_metrics_lock_wait_gauges.py`（`#2104` 的回归）挂进
   已有的 PR 路径步骤 `pr-migrate-empty-db` —— 与四条锁序回归**同一个步骤、同一次 pytest
   会话、同一个库**，沿用 `env -u DATABASE_URL` 剥离同库 `DATABASE_URL`（否则
   `conftest.py` 导入期 `db_url_guard` 拒载，pytest exit 4 = `#1547` 原样复发）。
   步骤名从 `Run lock-order regressions (PostgreSQL)` 改为中性的
   `Run concurrency regressions (PostgreSQL)`：**步骤名不参与分支保护**，可安全改名；而
   **job 名**仍不能改（分支保护按 check 名匹配，改名会让 required check 永不汇报）。
2. **守卫扩成两档**：`tests/test_lock_order_pr_path_contract.py` 的覆盖判据由「文件名含
   `lock_order`」扩为「命名判据 **∪** `_REGISTERED` 登记表」，并给登记表配两条**真检查**
   （`TestRegistry`）：登记项必须①文件存在、②确实是 PG-only（含 `startswith("sqlite")`
   惯用法）。少了这两条，登记表既可能指向已消失的文件（覆盖无声消失），也可能在 PR 路径
   白跑一个与 PG 无关的用例。

### 为什么第二档必须是登记表，而不是自动发现

这是**实测结论**，不是偏好（`#2138`）：按「PG-only + 真会话制造锁争用」的内容信号
（`pg_locks` / `pg_backend_pid` / `threading`）扫描 `backend/tests/`，**只命中锁等待这一个
文件，漏掉四条 `lock_order` 回归**——它们用别的方式开额外会话，信号不在这个集合里。
即内容信号对「这一类」没有判别力。而放宽成「凡含 sqlite-skip 惯用法」（9 个文件）就会把
PR 路径变成一个小时的活，直接违反 `2026-08-14-merge-path-attention-budget.md`。

更根本的是：**「值不值得进 PR 路径」是 PR 耗时预算的判断，不是可机读属性**。所以这里用
「显式登记 + 对登记项的两条真检查」，而不是假装能自动发现。

## Alternatives

- **内容自动发现**（上述）：实测判别力不足（漏四条已知必须覆盖的文件），否决。
- **按 sqlite-skip 惯用法全量发现**：命中 9 个文件，等于把 PR 路径变成半个夜间全量，
  与注意力预算冲突，否决。
- **新建 job**：新 job 不在分支保护里 → 不是 required check → 失败不拦合入，否决（同 `#1999`）。
- **改 job 名**（让失败显示在更贴切的名字下）：分支保护按 check 名匹配，改名会让 required
  check 永不汇报、所有 PR 卡死，否决（同 `#1999`）；只改步骤名。
- **pytest marker 选择**（`@pytest.mark.pg_concurrency` + CI `-m` 选择 + 守卫断言「每个带标记
  的文件都被某个 PR 步骤选中」）：**这是更好的终局**（可自动发现、无需登记表），但要动
  5 个测试文件 + conftest 注册 + 选择机制；当前只 1 个登记项时不成比例。已写入 Revisit。
- **把整组 `backend/tests/` 前移**：实测约 9 分钟，违反注意力预算，否决（同 `#1999`）。

## Verification

| 项 | 结果 |
|---|---|
| 守卫（离线） | **7 passed** |
| 负向对照 A：把锁等待文件移出 PR 步骤 | 红：`以下并发回归不在 PR 路径的任何 pytest 命令里：['backend/tests/api/test_metrics_lock_wait_gauges.py']` |
| 负向对照 B：登记项指向不存在的文件 | 红：`登记表指向不存在的文件：['…_TYPO.py']——文件被改名/删除后，本守卫会静默不再要求它` |
| 负向对照 C：登记一个非 PG-only 文件 | 红：`以下登记项不含 sqlite-skip 惯用法 'startswith("sqlite")'…['backend/tests/conftest.py']` |
| 恢复后复绿 | 7 passed |
| **整个 PR 步骤本地复现**（5 个文件、同一 pytest 会话、同一库） | **8 passed in 5.73s**（新增用例只多约 1 秒，仍在「窄而秒级」预算内） |
| 锁等待回归单独跑（本地 PG） | 1 passed in 0.77s |
| 根 `tests/` | **749 passed** |
| `ruff` / 治理面 / 差异面不变量 / ci.yml 可解析 | All checks passed / `[OK]` / `[OK]` / `jobs=8` |

**过程中被真跑抓住的一处自查**：我给新检查写的断言极性写反了（`assert not_pg_only` 应为
「断言为空」），正确代码下它**假红**。修掉后三向负向对照才有意义 —— 记录在此，因为它说明这
条检查是被执行过的，而不是只落在纸面上。

**CI 实跑证据（待本 PR 的 CI 证实）**：可确证的是机制本身——`env -u DATABASE_URL` 在同一
job 上的有效性已由 `#1999`/`#2003` 两轮证实（步骤输出 `5 passed, 1 warning in 4.81s`，非
sqlite skip 的假绿）。本轮新增的是「这个文件也在那里真跑」，其证据只能来自本 PR 的
`pull_request` 事件（`workflow_dispatch` 不触发 PR job，故无法用它验证）；合入后补记，
若已被 auto-merge 合入则走一笔纯文档补记 PR（同 `#2003` 的做法）。

## Revisit

- **终局形态：pytest marker**。当登记表长到约 3–5 项，或再出现一次「同类回归被漏接线」时，
  改为 `@pytest.mark.pg_concurrency` + conftest 注册 + CI `-m pg_concurrency` 选择，守卫断言
  「凡带该标记的测试文件，其标记都被某个 PR 步骤选中」。那是可自动发现的形态，比登记表更抗
  腐化；当前规模不值得。
- **命名判据的盲区**：第一档仍只看文件名是否含 `lock_order`。将来若有锁序回归没按该约定命名，
  只有登记表能救它 —— 反过来，**新登记项应尽量用命名约定落位**，减少对登记表的依赖。
- **PR 步骤的耗时预算**：本次步骤 5.73s（本地）/ 上次 CI 52s 为整个 job。若继续加文件，先量
  步骤耗时再决定是否拆分或改用 marker 选择。
- **告警阈值与多库 label**：仍挂在 `docs/notes/process/2026-09-15-lock-wait-observability-2104.md`
  的 Revisit 里（30s 阈值需按 `stability_retention_txn_seconds` 实测分布校准；多库场景
  `pg_stat_activity` 是全实例视图，已按当前库过滤，若将来跑多库需给 gauge 加 label）。
