# 设备租约紧急释放

适用条件：设备的 ACTIVE 租约（`device_leases.status = 'ACTIVE'`）没有被释放，关联 job 已终态，
租约回收器（`device_lease_reconciler`）不可用，且必须立即复用该设备。

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
          ji.status AS job_status, ji.ended_at, now() - ji.ended_at AS ended_ago,
          ji.plan_run_id, pr.status AS plan_run_status
   FROM device_leases dl
   LEFT JOIN job_instance ji ON ji.id = dl.job_id
   LEFT JOIN plan_run pr ON pr.id = ji.plan_run_id
   WHERE dl.device_id = <设备 id> AND dl.status = 'ACTIVE';
   ```

   正常结果：0 行或 1 行（部分唯一索引 `uq_device_leases_active_per_device` 保证每台设备最多
   1 条 ACTIVE 租约）。记下租约 `id` 和 `job_id`。
5. 如果第 4 步返回 0 行，停止本流程：该设备的占用不来自租约。改按
   [`production-diagnostics.md`](./production-diagnostics.md)「状态机一致性核对」第 ⑤ 项排查。
6. 如果 `lease_type` 不是 `JOB`，停止本流程并报告。当前代码只有
   `backend/services/agent_claim.py` 创建租约，且只创建 `JOB` 租约；`SCRIPT` 或 `MAINTENANCE`
   租约没有生产者，出现即属异常。
7. 如果 `job_status` 不是 `COMPLETED`、`FAILED` 或 `ABORTED`，停止本流程。`PENDING`、`RUNNING`、
   `UNKNOWN` 都属在途 PlanRun，由正常路径释放：job 结束时由 Agent 完成路径释放；租约过期后由回收器
   先把 job 置为 `UNKNOWN`，超过 `UNKNOWN_GRACE_SECONDS`（默认 300 秒）后释放租约并把 job 标为 `FAILED`。
8. 如果 `ended_at` 为空，停止本流程并报告：终态 job 缺少结束时间，属状态不一致。
9. 如果 `ended_ago` 小于 5 分钟，停止本流程，5 分钟后从第 4 步重查。回收器每
   `reconciler_interval_seconds`（默认 15 秒）一轮，会释放「job 已终态、租约仍为 ACTIVE」的租约
   （「状态机一致性核对」第 ① 项）。
10. 在控制面宿主的 Prometheus 中查询回收器最近 5 分钟内成功运行的次数：

    ```promql
    sum(increase(stability_reconciler_runs_total{check="terminal_job_active_lease",outcome="success"}[5m]))
    ```

    结果为 0 表示该序列在这 5 分钟内有采样、但回收器没有成功运行过一次，判定回收器不可用。
    该计数只在当选调度主节点的进程中、检查成功执行时增长，`sum` 已汇总所有控制面进程。
    只看成功次数，不看 `outcome="error"`：窗口内的历史报错不能说明回收器当前不可用。
11. 如果第 10 步的结果不是 0，停止本流程并报告：
    - 结果大于 0：回收器在运行却没有释放这条租约，属回收器缺陷，执行者不得手工绕过；
    - 无结果：该序列在这 5 分钟内没有采样（从未出现、采集中断或控制面进程不在），无法判定回收器状态。
      执行者不得把「无结果」当作 0，也不得用其他指标有值来代替本项判定。

## 释放

> 警告：下一步只改 `device_leases` 中该设备的 ACTIVE 行。不得修改 `device` 表或 Agent 文件。
> 设备 id 写错会释放另一台设备的租约。

12. 执行释放（执行身份：缺口 G3）：

    ```sql
    UPDATE device_leases
    SET status = 'RELEASED', released_at = now()
    WHERE device_id = <设备 id> AND status = 'ACTIVE';
    ```

    预期：影响 1 行（psql 显示 `UPDATE 1`）。

## 后置验证

13. 查该设备最近的租约记录：

    ```sql
    SELECT id, device_id, status, released_at
    FROM device_leases
    WHERE device_id = <设备 id>
    ORDER BY id DESC
    LIMIT 3;
    ```

    预期：第 4 步记下的租约 `id` 那一行 `status` 为 `RELEASED`，`released_at` 非空。
14. 等该设备所在主机的下一次心跳，然后在平台设备页查看该设备状态。
    预期：状态从 `BUSY` 变为 `ONLINE`。主机每次心跳都按 ACTIVE 租约重算设备状态
    （`backend/api/routes/heartbeat.py`），没有 ACTIVE 租约且 ADB 已连接的设备记为 `ONLINE`。
15. 如果设备仍为 `BUSY`，从第 4 步重查。如果查到一条 `id` 与第 4 步不同的 ACTIVE 租约，说明设备已被
    新的 job 认领，属正常占用，结束本流程。
16. 如果没有 ACTIVE 租约而设备仍为 `BUSY`，查所在主机的心跳：

    ```sql
    SELECT h.id, h.status, h.last_heartbeat, now() - h.last_heartbeat AS heartbeat_age
    FROM host h
    JOIN device d ON d.host_id = h.id
    WHERE d.id = <设备 id>;
    ```

    正常结果：`heartbeat_age` 小于 `HOST_HEARTBEAT_TIMEOUT_SECONDS`（默认 300 秒）。如果超过，主机
    心跳没有上报，设备状态不会刷新，改按 `diagnose-device-stall` skill 排查该主机。如果心跳正常，
    停止本流程并报告：无 ACTIVE 租约、心跳正常而设备仍为 `BUSY`，属状态不一致
    （「状态机一致性核对」第 ⑤ 项）。

## 待补缺口

以下事项待补；补齐后删除对应条目和正文中的缺口标记。

| 编号 | 位置 | 缺什么 | 跟踪 |
|---|---|---|---|
| G3 | 第 12 步 | 执行释放的身份：`stp_ro` 会拒绝写入；`production-diagnostics.md` 不允许手工查询使用 `stp` 或 `postgres`，并要求写操作走代码、迁移和 PR 流程 | [#3646](https://github.com/DUElost/stability-test-platform/issues/3646)（方案：改为管理端 API） |
