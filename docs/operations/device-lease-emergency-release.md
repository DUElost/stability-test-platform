# 设备租约紧急释放

> **生效时点**：B6 代码合入不等于激活。执行者必须按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723)
> §5 的生效证据选择流程：激活前执行 §1，自 B6 激活起执行 §2。本文没有授予生产写权限。

适用条件：设备的 ACTIVE 租约（`device_leases.status = 'ACTIVE'`）没有被释放，关联 job 已终态，
租约回收器（`device_lease_reconciler`）不可用，且必须立即复用该设备。

> 警告：§1 第 12 步与 §2 的释放接口会写入生产业务库。执行者必须先确认当前 Requirement 明确授权本次
> 写入。执行者不得手工 `UPDATE` `device_leases`，也不得修改 `device` 表或 Agent 文件。数据库凭据与生产写操作边界见
> [`production-diagnostics.md`](./production-diagnostics.md)。

本文的「设备 id」指 `device.id`（整数主键），不是设备序列号 `device.serial`。
命令中的 `$STP_API_BASE` 表示执行者已确认的控制面根 URL；`$AUTH` 的取法见
[`honor-flash-runbook.md`](./honor-flash-runbook.md)「鉴权（运维 curl 模式）」。

## 1. B6 激活前

本节仅适用于 B6 激活前；自 B6 激活起，执行者不得用本节的直接释放预期处置欠账。

### 前置检查

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
6. 如果 `lease_type` 不是 `JOB`，停止本流程并报告。激活前流程不处理 `SCRIPT` 或 `MAINTENANCE` 租约；
   激活后的欠账租约处置见 §2。
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

### 释放

> 警告：下一步只释放路径中的那一条 JOB 租约。执行者不得手工 `UPDATE` `device_leases`。
> 执行者不得修改 `device` 表、`job_instance` 行、PlanRun 或 Agent 文件。
> 路径中的设备 id 与租约不一致时，接口返回 404，不会释放其他设备的租约。

执行者必须把 `<原因>` 写成非空字符串。

12. 以管理员身份调用释放接口。`$AUTH` 的取法见 [`honor-flash-runbook.md`](./honor-flash-runbook.md)「鉴权（运维 curl 模式）」。把 `<设备 id>`、`<租约 id>` 换成第 4 步记下的值：

    ```bash
    curl -sS -X POST "$STP_API_BASE/api/v1/devices/<设备 id>/leases/<租约 id>/release" \
      -H "$AUTH" -H "Content-Type: application/json" \
      -d '{"reason":"<原因>"}'
    ```

    预期：HTTP 200，且 `audit_logs` 新增 1 行。响应 JSON 的 `data.status` 为 `RELEASED`，`data.lease_id` 等于 `<租约 id>`。
    审计行的查法（只读角色 `stp_ro`）：

    ```sql
    SELECT action, resource_type, resource_id, username, details
    FROM audit_logs
    WHERE resource_type = 'device'
      AND resource_id = '<设备 id>'
      AND action = 'emergency_release_lease'
    ORDER BY id DESC
    LIMIT 1;
    ```

    预期：1 行。`details` 含 `lease_id`、`job_id`、`job_status`、`reason`、`operator`。

### 后置验证

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

## 2. B6 激活后（自 B6 激活起生效）

本节每项 B6 行为自 B6 激活起生效。`MAINTENANCE` 租约由平台承载归还欠账
（holder=`platform`、reason=`归还欠账`），不再以「没有生产者」判异常。
紧急释放欠账租约返回 HTTP 409、`detail.code=LEASE_IS_RETURN_DEBT`；日常出口为清偿或人工放行。
JOB 租约仍经同一路径请求，但控制面在终态事务的统一入口决定释放或原地转换。

代码入口：`backend/api/routes/devices.py::release_device_lease_endpoint`；
`backend/services/device_lease_emergency_release.py::release_terminal_job_lease`；
`backend/services/device_return.py::is_debt_lease`、`handle_job_lease_release_sync`。
依据：[ADR-0061](../adr/ADR-0061-device-return-contract.md) D3 第 5 条、D5 第 2–5、7 条。

> 警告：执行者不得手工改写租约或归还状态，也不得把释放全部欠账租约当作日常处置。
> 直接释放会绕过欠账对派发的阻断；回退动作另受 #3723 §5 的 Owner 授权约束。

按顺序执行以下步骤：

1. 核对当前 Requirement 的生产写授权。
2. 如果没有该授权，停止本流程。
3. 按 [`production-diagnostics.md`](./production-diagnostics.md) 的只读连接方式查询目标设备的 ACTIVE 租约：

   ```sql
   SELECT dl.id, dl.lease_type, dl.status, dl.holder, dl.reason, dl.job_id,
          ji.status AS job_status, ji.ended_at, now() - ji.ended_at AS ended_ago
   FROM device_leases dl
   LEFT JOIN job_instance ji ON ji.id = dl.job_id
   WHERE dl.device_id = <设备 id> AND dl.status = 'ACTIVE';
   ```

   预期：0 或 1 行。查询不读取 fencing token。
4. 如果没有 ACTIVE 租约，停止本流程，按 §1 第 5 步排查非租约占用。
5. 如果租约为 `MAINTENANCE` 且 holder/reason 匹配上述欠账载体，停止紧急释放流程，转
   [`开关机手册 §5`](./powercycle-chain-disarm-runbook.md#5-b6-激活后的排障自-b6-激活起生效) 读取归还状态与未关闭条目。
6. 如果租约类型不是 JOB 且不符合欠账载体，停止本流程并报告状态异常。
7. 如果 JOB 未终态、结束时间为空或结束不足 5 分钟，停止本流程；在途 job 按正常协议收尾。
8. 用 §1 第 10 步的 PromQL 查询回收器可用性。
9. 如果结果不为 0（包括无结果），停止本流程并按 §1 第 11 步报告。

> 警告：下一步只请求处理路径指定的 JOB 租约。执行者不得把 HTTP 200 当作设备已归还；
> `data.status=MAINTENANCE` 表示设备仍由 ACTIVE 欠账租约占用。

10. 以管理员身份请求释放，`reason` 填本次非空处置原因：

    ```bash
    curl -sS -w '\nHTTP %{http_code}\n' -X POST \
      "$STP_API_BASE/api/v1/devices/<设备 id>/leases/<租约 id>/release" \
      -H "$AUTH" -H 'Content-Type: application/json' -d '{"reason":"<原因>"}'
    ```

    预期：HTTP 200，`data.lease_id` 为目标租约；`data.status=RELEASED` 表示无剩余欠账，
    `data.status=MAINTENANCE` 表示原地转换、租约仍 ACTIVE。成功请求写 `emergency_release_lease` 审计，查询见 §1 第 12 步。
    设备与租约不匹配为 404；已转成欠账的租约为 409 `LEASE_IS_RETURN_DEBT`。
11. 用第 3 步的 SQL 复读 ACTIVE 租约。
    预期：释放后无原 ACTIVE 租约，或同一租约 id 已变为 MAINTENANCE。后一种情况下，执行者不得按 §1 第 14 步期待设备变 ONLINE。
12. 如果出现欠账租约或 409 `LEASE_IS_RETURN_DEBT`，停止重复释放，转开关机手册 §5 的清偿或人工放行流程。
13. 如果返回 RELEASED，执行 §1 第 13–16 步的租约与心跳回查。

人工放行接口为 `POST /api/v1/devices/{device_id}/return-override`，请求字段为 `families`、`reason_category`、`evidence_ref`。
只有 Owner 或管理员可以执行放行，API 使用管理员鉴权；操作与审计回查见开关机手册 §5.3。
放行仅记 `unverified/override`，不代表已确认归还；部分专项仍有条目时租约继续 ACTIVE。
代码入口：`backend/api/routes/devices.py::return_override_endpoint`、`backend/services/device_return.py::apply_override_sync`；
依据：ADR-0061 D5 第 4、7 条。本段同样自 B6 激活起生效。
