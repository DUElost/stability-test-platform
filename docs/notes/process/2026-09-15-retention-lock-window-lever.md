# 保留清理的持锁窗口：批大小做成可调旋钮（并否决「把 purge 移出事务」）

Status: implemented
Class: process

> 关联：`#2022`（保留清理取锁顺序对齐 I1/I2）、`#2104`（窗口与等待的观测面）。
> 本单原计划单独立 issue，但立单时 GitHub API 连续返回 `Post … EOF`（环境侧临时故障），
> 故先以本 Note 记录决策与改动；issue 号在 API 恢复后补登。

## Decision

1. **新增设置** `plan_run_retention_batch_size`（env `PLAN_RUN_RETENTION_BATCH_SIZE`，默认
   `100`），由 `run_retention_cleanup` 传给 `_retention_candidate_ids(limit=…)`
   —— 它是**持锁窗口的杠杆**：窗口 ∝ 每 tick 处理的 PlanRun 数。
2. **登记进运维面**：`backend/.env.example` + `docs/development/environment-variables.md`
   的生成块（`tools/dev/env_inventory.py --write`），使运维能据此收缩窗口。
3. **明确否决**「把 `purge_run_storage_dirs` 移出事务」这条最直觉的改法（见下）。

### 为什么否决「把 purge 移出事务」

`#1521`/`#1698` 的「先文件后行」不是顺序洁癖，而是**自愈**语义：purge 失败要让该 run
**留在 DB 删除之外**，下一轮重试。当前实现把 purge 放在同一事务内、且把它的失败结果喂回
`_retention_safe_ids` 复算，于是「文件删了但事务回滚」→ 下轮重删（幂等）→ 自愈 ✅。

若把 purge 提前到取锁之前：purge 成功、随后该 run 因**并发变化**（例如被新的子 run 引用而进入
保留闭包）而不被删除 → 得到**「文件已删、行仍在」**：文件回不来，这不是自愈而是**永久不一致**，
比「窗口长一点」严重得多。所以正确方向是**压窗口长度**，不是挪 phase。

同一条 Revisit 里也写明：**不要**只把 deletes 挪到锁之前（候选选择依赖「锁内复核」与热路径
互斥）。

## Alternatives

- **调小保留期 `plan_run_retention_days`**：不解决窗口问题（窗口由批大小与单 run 的 NFS 成本
  决定），且与 ADR-0020 的清理目标冲突。
- **给 purge 加超时/分片**：会让「purge 半途失败」成为常态，把自愈语义变成部分删除；NFS 侧本就
  有失败重试路径，不再叠加一层。
- **默认就把批大小调到很小（如 10）**：会拉长清理总时长（积压），而窗口问题尚未被数据证实。
  先给旋钮 + 观测（`stability_retention_txn_seconds`），按分布调，而不是预先保守。
- **不登记进 `.env.example`**（只写进 Settings 当内部旋钮）：否决。`env_inventory` 的判据是
  「登记进示例 ∪ 声明内部」二选一；这个旋钮的价值就在于**运维能调**，声明内部会把它藏起来。
- **顺带把 `limit=100` 的调用点改成动态预算**（按时间片而不是条数）：本单不做——先要数据，
  再决定是否需要更细的预算模型。

## Verification

| 项 | 结果 |
|---|---|
| 新增回归 `test_batch_size_setting_bounds_one_tick`（批大小=2、3 个到期 run → 单 tick 只删 2） | **通过**（含在 35 passed 内） |
| 环境清单守卫 `tools/dev/env_inventory.py --check` | `[OK] 环境变量清单一致（213 个读取名）` |
| 设置默认值/类型契约 `tests/test_settings_scheduler.py`（`_PRE_MIGRATION_DEFAULTS` 逐键断言） | 通过（已加入 `plan_run_retention_batch_size: 100`） |
| retention + settings + env-inventory 面 | **35 passed** |
| `backend/agent/tests/test_cron_scheduler.py`（覆盖 `run_retention_cleanup`） | **13 passed** |
| 根 `tests/` | **599 passed** |
| `ruff` | All checks passed |

## Revisit

- **按数据调阈值**：上线后看 `stability_retention_txn_seconds` 分布与
  `stability_db_lock_wait_max_seconds` 峰值；若窗口常在秒级以上，先把批大小降到 20–50 观察，
  而不是直接上「预算模型」。
- **若真要把 purge 移出事务**：必须先解决「文件已删、行仍在」的不可自愈面（例如先写
  tombstone 再删文件，使两阶段都可重入）——那是一次独立设计，不是重排代码顺序。
- **积压监控**：批大小调小后，若清理跟不上产生量，会表现为 `PlanRun` 行数增长（而非报错）；
  需要时补一条积压 gauge（当前无）。
