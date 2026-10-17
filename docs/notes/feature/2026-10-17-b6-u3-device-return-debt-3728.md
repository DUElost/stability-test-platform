# B6 U3：控制面欠账（统一入口、欠账条目与占用不变量）

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) **v1.6** §1.2 / §3.0 / §3.4 / **§3.4.1** / §4 U3
与 ADR-0061 D1.3–8、D4.2、D5、D7.2 实施 [#3728](https://github.com/DUElost/stability-test-platform/issues/3728)。
Draft PR #3748 待规划确认与独立复核；合入不等于生效（阶段 B 初始化与控制面部署按 §5）。

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
8. 消费共享 `device_return` 契约机器值；**不**改 `ONBOARDED`（U5）、**不**重做 U1/U2。

### §3.4.1 补齐（Planner 回派 `7b3d43f09`）

1. **无终态回报 + 足迹**：统一入口按 init STARTED 等足迹补 `incomplete` 并建欠账；无足迹
   PENDING 仍释放，不一律造欠账。
2. **初始化历史（D1.7）**：已接入专项取最近建立足迹/解除的 job 映射存量状态与 stock 条目；
   成功解除覆盖更早残留；历史不得初始化为 returned。
3. **D5.10 重算**：事务完成前按真实 open 条目重算专项状态——有 open 则必须为 debt。
4. **迟到 `/complete` confirmed（D5.6）**：`try_apply_late_confirmed` 接到真实终态分支；
   严格核验条目保存的原 job / fencing token；只关同源；不改原 job 终态；错误 token 仍 409。
5. **CI**：审计 action 保留期登记、`DeviceReturnOverrideOut` 双向契约、欠账 Gauge 生产者与
   `stp_device_return_` 告警前缀。

## Alternatives

- 先释放 JOB 再提交后登记欠账：ADR-0061 已否决（claim 窗口）。
- 用超长 TTL 表达「不设到期」：开工指引禁止以此替代契约，改用类型守卫。
- 每个欠账条目一条租约：违反每设备唯一 ACTIVE 索引。
- 另建独立设备占用表：遗漏任一认领路径即失效；租约承载复用既有互斥。
- 放宽全部旧 token / 载荷冲突以接受迟到 confirmed：违反 D5.6 严格来源核验，已否决。

## Verification

见本 PR 正文「门禁」。本轮补齐后：Planner 四反例 + 负向（PENDING 无足迹 / 错 token 409）
落于 `backend/tests/services/test_planner_u3_contract_probes.py`；原 U3 行为 9 + SourceGuard 7；
`test_alert_metric_producers` / `test_api_response_shape_contract` / audit retention guard。
未跑 #3739 危险 cancel 变异。迁移 `down_revision=c3d4e5f6a7b8`（U2 head）。

## Revisit

- U4 消费心跳 `return_debts` 做自动清偿；U5 写入 ONBOARDED 后，失败→欠账映射对开关机生效。
- 阶段 B 激活时跑 `init_device_return.py --dry-run` → Owner 确认 → `--apply`。
- 回退控制面前必须先 `--release-all-debt-leases`（§5 第 7 步）。
- year-9999 `expires_at` 仅占位：host 迁移 / 无 host / 全回收路径若需独立证明，另开验收。
