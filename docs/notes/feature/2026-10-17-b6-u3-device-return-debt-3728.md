# B6 U3：控制面欠账（统一入口、欠账条目与占用不变量）

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) **v1.7.5** §1.2 / §3.0 / §3.4 / **§3.4.1** / **§3.4.2** / **§3.4.3** / **§3.4.4** / §4 U3 / 4.1 / 4.2
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

### §3.4.4 加锁全序 I5（复核第 2 轮 F3，v1.7.5）

不变量 **I5**：`job_instance` → `device` → `device_leases` → `device_return_debt`。
不得改 `acquire_lease` / 准入 / 心跳；不新增重试语义。

**4 处调用方改序（Job 之后、Lease 之前锁 Device）：**

| 入口 | 实际顺序（文件:行，本 tip） |
|---|---|
| 紧急释放 | Job `:62` → Device `:69` → Lease `:75` → 统一入口（`device_lease_emergency_release.py`） |
| `_reconcile_expired_leases` | Job `:149` → Device `:163` → Lease `:169` → 统一入口（`device_lease_reconciler.py`） |
| `sync_agent_recovery` | Job `:326` → Device `:332` → Lease `:337`（`agent_recovery.py`） |
| completion UNKNOWN 分支 | Job `:246` → Device `:420` → Lease `:425`（`agent_completion.py`） |

**U3 自有入口与其它释放点（保持 / 已符合 I5）：**

| 事务 | 顺序 |
|---|---|
| 初始化 / 清偿 / 放行 / 退役 / `_acquire_debt_lease_*` | Device → Lease → debt（`device_return.py`） |
| 统一入口 `handle_job_lease_release[_sync]` | Device → Lease → debt |
| normal completion / recycler PENDING / stale UNKNOWN / terminal ACTIVE | Job →（入口内）Device → Lease |
| 认领 `acquire_lease`（未改） | Device 代次 CAS → Lease（`lease_manager.py:99/:128`） |

共享行锁表已回填 I5 与 `device` × `device_leases` 全表；`on_job_terminal` 新边与准入不成环依据见该表。

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

见本 PR 正文「门禁」。本轮 §3.4.4（绑定推送 HEAD）：

| 项 | 结果 |
|---|---|
| §3.4.4-a 初始化×紧急释放（两种先后） | 修前 F3 强制交错 **40P01**（剥 Device 锁）；修后 **2 passed** |
| §3.4.4-b reconciler×acquire_lease | 修后 **passed**（无 40P01） |
| §3.4.4-c recovery×discharge | 修后 **passed** |
| §3.4.4-d completion UNKNOWN×init | 修后 **passed** |
| `test_device_return_lock_order_3728.py` + 既有锁序 + 全序/接线守卫 | **40 passed** |
| U3 定向 + §3.4.3（含 F1/F2/flush/异步预读/I1） | **90 passed** |
| 共享行锁表 I5 回填 | 已写 |
| #3739 危险 cancel 变异 | **未跑（禁止）** |
| required CI + informational（推送后） | **pending（以 PR checks 为准）** |

## Revisit

- §3.4.4 交规划者核对后由原复核面做第 3 轮（绑定新 HEAD）。
- U4 消费心跳 `return_debts` 做自动清偿；U5 写入 ONBOARDED 后，失败→欠账映射对开关机生效。
- U8 承接共享服务类别政策与平台终止消费；U3 不越界改 Agent/契约/包/模板。
- 阶段 B 激活时跑 `init_device_return.py --dry-run` → Owner 确认 → `--apply`（须为 §3.4.2 逐设备事务）。
- 回退控制面前必须先 `--release-all-debt-leases`（§5 第 7 步）。
- year-9999 `expires_at` 仅占位：host 迁移 / 无 host / 全回收路径若需独立证明，另开验收。
