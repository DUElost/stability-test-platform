# 退役设备仍可被 claim 落 ACTIVE 租约：acquire_lease generation 步进改带 `retired_at IS NULL` 的 CAS（#3481）

Status: implemented
Class: bug-fix

## Decision

退役↔claim TOCTOU 的收口点放在**租约获取的最后一步**：`acquire_lease` 的
generation 步进从无条件 `UPDATE device SET lease_generation=lease_generation+1
WHERE id=:id` 改为带 `retired_at IS NULL` 条件的 CAS——0 行（设备已退役或已
消失）即抛内部哨兵 `_DeviceRetired`、回滚该 savepoint、返回 None；claim 循环
走既有 `LockAcquireFailed` 通路跳过该设备、继续 claim 其余设备。

为什么选这里而不是在 claim 循环里补一次 `retired_at` 复读：claim 的设备清单
是无锁快照，从读到落租约之间任何交错都必须被拦，而 `acquire_lease` 是唯一
「落租约」的写点（非测试调用点唯一，`agent_claim.py`）；在 UPDATE 谓词里做
CAS 让 PostgreSQL 在 READ COMMITTED 下于行锁等待后按**最新已提交行**重评谓词
（EvalPlanQual），天然闭合「退役先锁行、claim 的 UPDATE 排队在后」与「退役
在 claim 读快照后提交」两种交错。反过来若退役后于 claim 落租约，退役侧既有
`_assert_no_active_work` 会 409——两个方向合起来，E2「退役时无 ACTIVE 租约」
的事后态在任何交错下都成立。无谓词 UPDATE 时 0 行本会退化为 `old_gen+1`
继续插入（这正是 #3470 引入的缺口），现在 0 行一律拒绝。

注释/docstring 同步：`agent_claim.py` 设备清单过滤处注明该过滤只是准入快照、
执行点在 acquire_lease 的 CAS；`device_retirement.py::_locked_device` 注明
「锁内复检」只闭合退役事务自身的窗口，claim 侧窗口由 CAS 闭合——两侧合起来
才是完整不变量。

测试三层：lease_manager 单元用例（已退役设备 → None、无租约行、generation
不步进）；claim 级交错反例（在 claim 的设备清单快照读之后、PENDING Job 排名
之前，经**生产 `retire_device` 路径**提交退役，再以 dispatcher 视角落下该设备
的 PENDING Job——退役前置 `_assert_no_active_work` 把 PENDING 计为活跃，故
生产里的合法交错必然是「Job 后于退役到达」，测试按此时序构造）；以及「CAS 0
行只跳过该设备、同 host 其余设备照常认领」的正例。交错钩子落 Job 时不带
`host_id`：claim 事务持有 host 行 `FOR UPDATE`，带外键的 INSERT 会在 host
外键检查上等该锁、与「claim 等钩子返回」成环（生产 dispatcher 是独立事务，
无此环）；claim 认领时本就回填 `job.host_id`。

## Alternatives

- **claim 循环内复读 `retired_at`**：只覆盖 claim 一条通路；`acquire_lease`
  是所有租约获取的收口（JOB/SCRIPT/MAINTENANCE 共用），CAS 一处全覆盖且对
  未来新增调用方默认安全。复读还多一次往返，且仍窄于「快照→落租约」全窗口。
- **退役侧持锁跨到 claim 提交**（如 advisory lock 串行化）：把两类事务的锁序
  耦合到一起，扩大死锁面；E2 是事后态不变量，用谓词 CAS 在写点表达最直接。
- **回滚失败 savepoint 后同时 `db.expire(job)`**：实证（最小脚本 + 变异自证）
  证明多余——SQLAlchemy 2.0.52 对「savepoint 前已加载、savepoint 内改脏」的
  对象，savepoint 回滚会同时还原内存态与 DB（会还原失败的形态是「savepoint
  前**已**改脏」，claim 不属于该形态），变异自证也杀不死该行，按最小方案删除。

## Verification

- `backend/tests/services/test_lease_manager.py`（含新增
  `test_acquire_lease_rejects_retired_device`）、`backend/tests/api/
  test_agent_api_watcher.py`（含新增两条交错用例）等受影响 7 文件：
  `systemd-run ... MemoryMax=6G pytest <7 files> -q` → **71 passed**（隔离
  testcontainers PG，`TEST_DATABASE_URL` 未设，未触生产库）。
- 变异自证：去掉 CAS 的 `retired_at IS NULL` 条件 → 新增 3 用例全红
  （租约落库、Job 被误 claim）；还原后全绿。
- `python scripts/run_gates.py check:quick`（worktree，16 gates）→ 全过
  （worktree 缺 `frontend/node_modules`，软链主树后 eslint 门禁恢复）。
- 根级 `tests/` 全量：结果见 PR 正文（提交前最后一轮运行）。

## Revisit

- 交错反例的「退役后 Job 才到达」时序依赖 dispatcher 不查设备退役位的现状
  （ADR-0057 D4 只收口了 claim 与派发侧 host 位）；若后续 dispatcher 增加设
  备退役过滤，本用例仍是合法交错（Job 在退役提交后落库），不受影响。
- `claim_lease_failed_total` 指标现同时承载「冲突」与「退役跳过」两类 None，
  日志侧已可区分（`reason=conflict` / `reason=retired`）；若运维需要拆分面板，
  再立单加 label，不在本单范围。
