# 设备租约紧急释放

适用条件：设备的 ACTIVE 租约（`device_leases.status = 'ACTIVE'`）没有被释放，PlanRun 的正常释放路径
不可用，且必须立即复用该设备。

> 警告：本流程直接写生产业务库的 `device_leases` 表。执行者必须先确认当前 Requirement 明确授权本次
> 写入。执行者不得修改 `device` 表或 Agent 文件。数据库凭据与生产写操作边界见
> [`production-diagnostics.md`](./production-diagnostics.md)。

本文的「设备 id」指 `device.id`（整数主键），不是设备序列号 `device.serial`。
标有「缺口」的步骤还没有查法，见文末「待补缺口」；执行者遇到缺口时必须停下并报告缺口编号，不得自行编写查法。

## 前置检查

按顺序执行以下步骤：

1. 核对当前 Requirement 的原文。如果原文没有明确授权写入生产业务库，停止本流程。
2. 按 [`production-diagnostics.md`](./production-diagnostics.md)「凭据来源」，用只读角色 `stp_ro`
   连接生产业务库。
3. 用设备序列号查设备 id：

   ```sql
   SELECT id, serial, status
   FROM device
   WHERE serial = '<序列号>';
   ```

   正常结果：1 行。记下 `id`，即下文的 `<设备 id>`。
4. 查该设备的 ACTIVE 租约，以及租约关联的 job 和 PlanRun：

   ```sql
   SELECT dl.id, dl.lease_type, dl.job_id, dl.acquired_at, dl.renewed_at, dl.expires_at,
          ji.status AS job_status, ji.plan_run_id, pr.status AS plan_run_status
   FROM device_leases dl
   LEFT JOIN job_instance ji ON ji.id = dl.job_id
   LEFT JOIN plan_run pr ON pr.id = ji.plan_run_id
   WHERE dl.device_id = <设备 id> AND dl.status = 'ACTIVE';
   ```

   正常结果：0 行或 1 行（部分唯一索引 `uq_device_leases_active_per_device` 保证每台设备最多
   1 条 ACTIVE 租约）。记下租约 `id`。
5. 如果第 4 步返回 0 行，停止本流程：该设备的占用不来自租约。改按
   [`production-diagnostics.md`](./production-diagnostics.md)「状态机一致性核对」第 ⑤ 项排查。
6. 如果第 4 步的 `job_status` 为 `COMPLETED`、`FAILED` 或 `ABORTED`，这条租约属于
   `device_lease_reconciler` 的回收范围（「状态机一致性核对」第 ① 项）。确认回收器不可用后，才继续
   下一步（缺口 G1）。
7. 如果该设备有在途 PlanRun，停止本流程，改走 PlanRun 的正常释放路径（在途判据：缺口 G2）。

## 释放

> 警告：下一步只改 `device_leases` 中该设备的 ACTIVE 行。不得修改 `device` 表或 Agent 文件。
> 设备 id 写错会释放另一台设备的租约。

8. 执行释放（执行身份：缺口 G3）：

   ```sql
   UPDATE device_leases
   SET status = 'RELEASED', released_at = now()
   WHERE device_id = <设备 id> AND status = 'ACTIVE';
   ```

   预期：影响 1 行（psql 显示 `UPDATE 1`）。

## 后置验证

9. 查该设备最近的租约记录：

   ```sql
   SELECT id, device_id, status, released_at
   FROM device_leases
   WHERE device_id = <设备 id>
   ORDER BY id DESC
   LIMIT 3;
   ```

   预期：第 4 步记下的租约 `id` 那一行 `status` 为 `RELEASED`，`released_at` 非空。
10. 如果设备在平台上仍显示占用，停下并报告缺口 G4。

## 待补缺口

以下查法由设备租约领域 owner 补（ADR-0059 D4-6）。补齐后删除对应条目和正文中的缺口标记。

| 编号 | 位置 | 缺什么 |
|---|---|---|
| G1 | 第 6 步 | 判断 `device_lease_reconciler` 不可用的查法，以及需要等多久 |
| G2 | 第 7 步 | 「在途 PlanRun」的判据：第 4 步的 `job_status`、`plan_run_status` 取哪些值算在途；`lease_type` 为 `SCRIPT` 或 `MAINTENANCE`（`job_id` 为空）时怎么判断 |
| G3 | 第 8 步 | 执行 UPDATE 的数据库身份：`stp_ro` 会拒绝写入；`production-diagnostics.md` 不允许手工查询使用 `stp` 或 `postgres`，并要求写操作走代码、迁移和 PR 流程 |
| G4 | 第 10 步 | 释放后设备仍显示占用时的查法（改写前原文为「检查 Agent heartbeat 是否仍在续租，以及设备在线状态」，没有给出命令或页面） |
