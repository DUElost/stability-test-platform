# B6 U3：控制面欠账（统一入口、欠账条目与占用不变量）

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) **v1.7.5**（§3.4.4 文档事实按 **v1.7.7** 修正）§1.2 / §3.0 / §3.4 / **§3.4.1** / **§3.4.2** / **§3.4.3** / **§3.4.4** / §4 U3 / 4.1 / 4.2
与 ADR-0061 D1.3–8、D4.2、D5、D7.2（Accepted v1.1）实施 [#3728](https://github.com/DUElost/stability-test-platform/issues/3728)。
Draft PR #3748 待规划确认与独立复核；合入不等于生效（阶段 B 初始化与控制面部署按 §5）。
Owner #3751：五项接受 + M1–M3；共享服务类别政策与平台终止消费交 **U8**，U3 **不改** Agent/共享契约/包/模板。

## Decision

1. 迁移 `u3b6c7d8e9f0`：新表 `device_return_debt`（单数）+ `device.return_state` JSONB；
   部分唯一索引 `uq_device_return_debt_open_source`（`COALESCE(source_job_id,0)`）。
2. 新服务 `backend/services/device_return.py` 为 §1.2 全部 11 处 JOB 租约释放的统一入口：
   同事务映射 `disarm_results` → 专项状态 / 欠账条目，有未关闭条目则把 JOB 租约**原地转换**
   为 `MAINTENANCE` 欠账租约并推进 `lease_generation`；否则释放 JOB 租约。
3. 欠账租约：`holder=platform`、`reason=归还欠账`、`host_id=设备真实 host`；
   **不**用虚构 host，也**不**靠超长 TTL 充当「不设到期」——`expires_at` 仅为 NOT NULL
   列填充（year-9999 占位 + 类型保护）；reconciler / 紧急释放按 `lease_type` + `is_debt_lease()`
   拒绝释放。本轮**未**独立证明全部回收/host 迁移/无 host 场景。
4. 清偿 `POST /api/v1/agent/devices/{id}/return-discharge`、人工放行
   `POST /api/v1/devices/{id}/return-override`（admin + 审计）；心跳响应新增 `return_debts`。
5. 退役允许仅欠账租约（D5.8）：退役事务内释放欠账租约、条目保持 open；取消退役重建租约。
6. 准入在 `active_lease` 之前插入 `device_return_debt`；派发预览告警同码。
7. 指标 `stp_device_return_debt_open{family}` / `stp_device_return_debt_oldest_seconds` /
   `stp_device_return_override_total` 与三条 warning 告警；`/metrics` 刮取时
   `refresh_device_return_debt_gauges` 为真实生产者；`init_device_return.py`
   默认 `--dry-run`，`--apply` 才写库，`--release-all-debt-leases` 为回退。
8. 消费共享 `device_return` 契约机器值；**不**改 `ONBOARDED`（U5）、**不**重做 U1/U2；
   **不**改 Agent/共享契约/包/模板（U8 边界）。
9. 历史 teardown 推导抽出 `backend/services/disarm_derive.py`（叶模块）：
   `device_return` / `agent_completion` 均顶层依赖它，打破 C5 环并去掉函数体内 import。

### §3.4.1 补齐（Planner 回派 `7b3d43f09`）

1. **无终态回报 + 足迹**：统一入口按 init STARTED 等足迹补 `incomplete` 并建欠账；无足迹
   PENDING 仍释放，不一律造欠账。
2. **初始化历史（D1.7）**：已接入专项取最近建立足迹/解除的 job 映射存量状态与 stock 条目；
   成功解除覆盖更早残留；历史不得初始化为 returned。预阶段 A `disarm_results=NULL` +
   teardown COMPLETED → 经 StepTrace 推导，落 unverified/stock（非 incomplete/debt）。
3. **D5.10 重算**：事务完成前按真实 open 条目重算专项状态——有 open 则必须为 debt。
4. **迟到 `/complete` confirmed（D5.6）**：`try_apply_late_confirmed` 接到真实终态分支；
   严格核验条目保存的原 job / fencing token；只关同源；不改原 job 终态；错误 token 仍 409。
5. **CI**：审计 action 保留期登记、`DeviceReturnOverrideOut` 双向契约、欠账 Gauge 生产者与
   `stp_device_return_` 告警前缀。

### §3.4.2 CLI 逐设备事务（v1.7）

1. 设备 id **keyset 分页**（每页 100，id ASC，游标最后 id）；分页短读事务，处理前结束；
   不得 `.all()` 全量 Device ORM，不得全批同一外层事务。
2. **每台独立 Session/顶层事务**：dry-run 完整 rollback+close；apply 逐台 commit+close；
   保存点≠跨设备边界；单台失败只回滚本台。
3. claim 唯一冲突 / 代次 CAS：回滚并结束本台事务，**新 Session** 重取锁与最新占用后重试；
   每台最多 3 次，退避 0.1s / 0.2s；已有其它占用只记条目不抢占；3 次失败计冲突继续后续。
   任何失败/重试耗尽 → 退出码 **1**；正常成功/已占用 → **0**；统计区分
   ok / occupied / skipped / failed / conflict_exhausted。
4. dry-run 零业务写且实际释放锁；`--limit` 跨页累计；重复运行幂等；历史判定保持 §3.4.1。
5. **真实 CLI `main(argv)` 测试**（不可用 service helper 代替）：
   - 第二台入口前独立 PG 连接 **NOWAIT** 锁第一台（dry-run 与 apply 均须成功）；
   - 第一台已提交、第二台注入真实失败后第一台仍保存、第二台无部分状态。


### review-r1 F1/F2（权威复核 `b3bee2809` → 本 tip）

1. **F1（P1）**：生产 `SessionLocal(autoflush=False)` 下，清偿/放行关闭欠账后须 `flush`
   再查 open / 释租约；否则 `_open_debts_sync` 仍见未刷写行，重算强制 debt、租约不释放。
   修复：`_close_family_debts_sync` 与迟到 confirmed 关闭后 flush；回归覆盖真实
   `return-override` / `return-discharge`（仅还原 get_db→SessionLocal）。
2. **F2（P2）**：`_lock_device_sync` / `_lock_device_async` 在 `FOR UPDATE` 上加
   `populate_existing=True`，获锁后刷新 identity-map 中的 Device（含 return_state JSONB），
   串行另一专项已完成事实不被旧缓存覆盖。

### §3.4.3 规划补充（复核第 1 轮返修后，v1.7.3 / v1.7.4）

1. **加锁前 flush**：`populate_existing` 会丢弃未 flush 的 Device 改动；生产
   `SessionLocal(autoflush=False)` 下，统一入口加锁前必须先 `db.flush()` /
   `await db.flush()`，再保留 `populate_existing`。回归：调用方改 `status=OFFLINE`
   经统一入口后仍保存。
2. **异步预读回归**：异步 Session 预读 Device 后，另一事务放行专项；去掉异步
   `populate_existing` 须红，恢复后绿。
3. **I1 断言迁移**：`_FAMILY_STEPS` 已迁入 `disarm_derive`；
   `test_shared_onboarded_registry_i1_u2_3727.py` 断言改到
   `disarm_derive.FAMILY_STEPS is device_return.FAMILY_STEPS` 且
   `agent_completion` 无本地副本；本地 `dict(FAMILY_STEPS)` 变异须红。
4. **全量 backend/tests/**：最终 HEAD 须跑完整套件或同 HEAD informational SUCCESS。

### §3.4.4 加锁全序 I5（复核第 2 轮 F3；v1.7.7 文档事实修正）

不变量 **I5**（v1.7.7）：`job_instance` → `device` → `device_leases`；`device_return_debt`
只在持有该设备 `device` 行锁时写入/加锁，持锁期间与该设备租约行的先后不限。
不得改 `acquire_lease` / 准入 / 心跳；不新增重试语义。

**4 处调用方改序（Job 之后、Lease 之前锁 Device）：**

| 入口 | 实际顺序（文件:行，本 tip） |
|---|---|
| 紧急释放 | Job `:61-65` → Device `:68-72` → Lease `:74-79` → 统一入口 `:102`（`device_lease_emergency_release.py`） |
| `_reconcile_expired_leases` | Job `:148-153` → Device `:162-166` → Lease `:168-173` → 统一入口 `:206/:217/:249`（`device_lease_reconciler.py`） |
| `sync_agent_recovery` | Job `:325-329` → Device `:331-335` → Lease `:336-343` → 统一入口 `:408/:475/:486`（`agent_recovery.py`） |
| completion UNKNOWN 分支 | Job `:245-249` → Device `:419-423` → Lease `:424-433` → 统一入口 `:543`（`agent_completion.py`） |

**U3 自有入口：按每个事务的实际顺序分行（不得并称同一完整序列）**

| 事务 | 实际顺序（本 tip） | 位置 |
|---|---|---|
| 统一入口 `handle_job_lease_release_sync` | Device → Lease → debt | `device_return.py:532` → `:539` → `:547` |
| 统一入口 `handle_job_lease_release`（async） | Device → Lease → debt | `:819` → `:826` → `:832` |
| 人工放行 `apply_override_sync` | Device → 欠账条目（关闭 + flush）→ Lease | `:995` → `:1013`（`_close_family_debts_sync:900/:910`）→ `:1020`（`_active_lease_sync:917`） |
| 存量初始化 `init_device_return_sync` | Device → 欠账条目（INSERT）→ Lease | `:1338` → `:1403`（`_upsert_open_debt_sync` flush `:380-381`）→ `:1426`；无 ACTIVE 租约时 `:1433` → `_acquire_debt_lease_sync:474/:475` |
| 清偿 `apply_discharge_sync` | Device → Lease → 欠账条目（关闭 + flush） | `:936` → `:939` → `:963`（`_close_family_debts_sync:900/:910`） |
| 迟到 confirmed `apply_late_confirmed_sync` | Device → 欠账条目（关闭 + flush）→ Lease | `:1042` → `:1049-1071` → `:1072`（`_active_lease_sync:917`） |
| 退役 `release_debt_lease_for_retirement_sync` | Device（调用方）→ Lease | `device_retirement.py:154` → `device_return.py:1191`；不写欠账条目 |
| 取消退役 `restore_debt_lease_on_unretire_sync` | Device（调用方）→ 欠账条目普通读 → Lease | `device_retirement.py:209` → `device_return.py:1203`（普通 SELECT）→ `:1205` → `:474/:475` |
| 认领 `acquire_lease`（未改） | Device 代次 CAS → Lease | `lease_manager.py:98-106` → `:122-129` |

无欠账条目可写时，相应事务不产生 debt 步骤；Device 锁与 Lease 的相对顺序不变。

**11 处统一入口释放点（每个事务的实际顺序）**

| # | 释放点 | 实际顺序（文件:行，本 tip） |
|---|---|---|
| 1 | normal completion | Job `:245-249` → 入口 `:543` → Device `device_return.py:819` → Lease `:826`（`agent_completion.py`） |
| 2 | recovery boot mismatch | Job `:325-329` → Device `:331-335` → Lease `:336-343` → 入口 `:408`（`agent_recovery.py`） |
| 3 | recovery UNKNOWN grace | 同上 → 入口 `:475`（`agent_recovery.py`） |
| 4 | recovery terminal ACTIVE | 同上 → 入口 `:486`（`agent_recovery.py`） |
| 5 | reconciler orphan（job 行缺失） | Job（有 `job_id` 时）`:148-153` → Device `:162-166` → Lease `:168-173` → 入口 `:206`；`job_id` 为空时 `:195-196` 直接释放，不经入口（`device_lease_reconciler.py`） |
| 6 | reconciler terminal/expired | Job `:148-153` → Device `:162-166` → Lease `:168-173` → 入口 `:217`（`device_lease_reconciler.py`） |
| 7 | reconciler UNKNOWN grace | 同上 → 入口 `:249`（`device_lease_reconciler.py`） |
| 8 | reconciler stale UNKNOWN | Job `:368-373` → 入口 `:392`（入口内 Device → Lease）；`:382-389` 的 Lease 为普通 SELECT（`device_lease_reconciler.py`） |
| 9 | reconciler terminal ACTIVE | 普通读 `:464-472` → 入口 `:478`（Device → Lease，无 Job 行锁；`device_lease_reconciler.py`） |
| 10 | recycler PENDING timeout | Job CAS `recycler.py:455-474` → 入口 `:482`（Device → Lease） |
| 11 | 紧急释放 | Job `:61-65` → Device `:68-72` → Lease `:74-79` → 入口 `:102`（`device_lease_emergency_release.py`） |

**父聚合 / 准入不成环（v1.7.7）**

`on_job_terminal` 在 `job_terminalization.py:153` 只插入 `plan_run_pending_aggregation`，`:155`
提交后才 `:156` 唤醒父聚合；父 Run/PRH 的更新在之后的聚合事务中，Device/Lease 锁在
`:155` 提交时释放。pending 插入经 `plan_run.py:243-245` 的外键对 `plan_run` 取 **KEY SHARE**，
该 KEY SHARE 与 Device/Lease 锁同处 `:153-:155` 的短事务，但父 Run/PRH 的更新不在其中。

准入先在 `admission_pump.py:711` 调 `_lock_admission_resources`（`:82-105`，Host/Device 按 id
升序 `FOR UPDATE`），`:714` 才做忙闲分类。不成环依据：准入泵只对 **QUEUED** run 取
`FOR UPDATE SKIP LOCKED`（`:156-168`），job 在准入事务中才物化（`:796` →
`plan_dispatcher_sync.py:1097`），`retry_plan_run_dispatch` 拒绝已有 job 的 run 重新准入
（`precheck/runner.py:386-394`）；释放路径只对已有 job 的 run 取 KEY SHARE，两侧不会落在同一
`plan_run` 行上。详见共享行锁表。

## Alternatives

- 先释放 JOB 再提交后登记欠账：ADR-0061 已否决（claim 窗口）。
- 用超长 TTL 表达「不设到期」：开工指引禁止以此替代契约，改用类型守卫。
- 每个欠账条目一条租约：违反每设备唯一 ACTIVE 索引。
- 另建独立设备占用表：遗漏任一认领路径即失效；租约承载复用既有互斥。
- 放宽全部旧 token / 载荷冲突以接受迟到 confirmed：违反 D5.6 严格来源核验，已否决。
- 全批同一 Session + 保存点：违反 §3.4.2 跨设备事务边界与 dry-run 实际释锁要求，已否决。
- 仅测 service helper 代替 CLI NOWAIT / 失败隔离：§4.1 明确禁止，已否决。
- 反方向统一为 Lease → Device（改认领与 U3 全部入口）：范围更大，规划者否决，采用 I5。

## Verification

见本 PR 正文「门禁」。§3.4.4 的 b/c/d 按规划者核对改用 **NOWAIT 判据**
（#1959/#1980/#2015 同款）：第三会话 `FOR UPDATE` 锁 Device；后台启动被测入口，
`pg_locks` 等到未授予的 `transactionid`；第四会话对租约行 `FOR UPDATE NOWAIT`。
修复后试锁成功，修复前（Lease→Device）试锁失败（55P03）。a 组保持不变。

环境：worktree `.wt/3728-device-return-debt`；testcontainers 隔离 PG；
`scripts/run_pytest.py` 内存保护（日志确认 `memory.max=6442450944` / `swap.max=0`）；
`env -i`（无 `TEST_DATABASE_URL`）；未跑 #3739 危险变异或真实宿主信号。变异在临时
detached worktree（`/tmp/u3-mut-3728`、`/tmp/u3-mut-3728b`）中逐文件撤回，运行后
`git checkout --` 恢复并已移除；本 PR 的代码改动仅为测试文件（本轮 F4 只改两份文档）。

全量套件复现（与 CI 同 pytest 版本）：首轮 d 失败——`_seed_*` 的 `_NOW` 是模块导入
时刻，全量套件中 d 约 16 分钟后才执行，UNKNOWN→RUNNING 的 300s 宽限已过，completion
抛 `INVALID_OR_EXPIRED_FENCING_TOKEN`。已按**测试执行时刻**重锚 `job.ended_at` 与
`lease.expires_at`；此后全量 **4208 passed / 1 skipped**，原先 76% 处的挂起不再出现。

| 项 | 结果 |
|---|---|
| §3.4.4-a 初始化×紧急释放（两种先后） | 未改；全文件基线 **5/5 次均 5 passed** |
| §3.4.4-b reconciler×过期租约（NOWAIT） | 撤回 reconciler 的 Device 锁 → **5/5 failed**（55P03，命中「锁序仍是 Lease→Device」）；恢复 → **5/5 passed** |
| §3.4.4-c recovery×清偿（NOWAIT） | 撤回 recovery 的 Device 锁 → **5/5 failed**（55P03）；恢复 → **5/5 passed**；终态 recovery=ABORT_LOCAL/terminal_job_active_lease，清偿 `ok=True`、`debt_lease_released=True`、`open_debt_count=0` |
| §3.4.4-d completion UNKNOWN（NOWAIT） | 撤回候选 Lease 先于 Device → **5/5 failed**（55P03）；恢复 → **5/5 passed**；终态 job=COMPLETED、JOB 租约 RELEASED |
| 全量 `backend/tests/`（pytest 9.1.1 / pytest-asyncio 1.4.0） | **4208 passed / 1 skipped（15m49s）** |
| `test_device_return_lock_order_3728.py` + 既有锁序 + 全序/接线守卫 | **42 passed** |
| U3 定向 + §3.4.3（含 F1/F2/flush/异步预读/I1） | **72 passed** |
| 紧急释放 / 退役 / 心跳 / retry-exit API | **47 passed** |
| `check:quick` | **OK（16 gates）** |
| 共享行锁表 I5 回填 | 已按 v1.7.7 修正：I5 定义、父聚合/准入依据、自有入口逐事务顺序 |
| 本轮 F4 文档事实修正 | `check:quick` **OK（16 gates）**；`tests/` 文档/锁序守卫 **68 passed**；未改代码与测试 |
| #3739 危险 cancel 变异 | **未跑（禁止）** |
| required CI + informational（最终 HEAD） | 见 PR checks / 回告 |

复现命令（b/c/d 每组；`-k` 依次为 `reconciler` / `recovery` / `completion`）：

```sh
env -i PATH="$PATH" HOME="$HOME" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=test-secret \
  /home/debian13/stability-test-platform/.venv/bin/python scripts/run_pytest.py \
  backend/tests/services/test_device_return_lock_order_3728.py -q -k "<group>"
```

## Revisit

- §3.4.4 交规划者核对后由原复核面做第 3 轮（绑定新 HEAD）。
- U4 消费心跳 `return_debts` 做自动清偿；U5 写入 ONBOARDED 后，失败→欠账映射对开关机生效。
- U8 承接共享服务类别政策与平台终止消费；U3 不越界改 Agent/契约/包/模板。
- 阶段 B 激活时跑 `init_device_return.py --dry-run` → Owner 确认 → `--apply`（须为 §3.4.2 逐设备事务）。
- 回退控制面前必须先 `--release-all-debt-leases`（§5 第 7 步）。
- year-9999 `expires_at` 仅占位：host 迁移 / 无 host / 全回收路径若需独立证明，另开验收。
