# 开关机专项的中止与"链路解除"运维手册

> **流程时点**：下方过渡警告与 §1–§4 适用于 B6 激活前。自 B6 激活起，执行者必须按 §5 排障，
> 以登记组合的设备侧回读与欠账事实判断归还；原过渡内容及其「阶段 B 生效」出口保留到另一个文档收尾 PR。

> **警告（先读这条）**：`powercycle` 作业的 `COMPLETED` / `ABORTED` **不代表设备侧链路已解除**。
> 解除的唯一判据是设备侧回读到 `running=false`。账面完成而链路仍武装时，设备会在无监督下继续
> 自主重启（最长到 `test_times` 上限 1000 轮），并在随时把跑在它上面的其它专项打断。
> 背景与实证：[ADR-0061](../adr/ADR-0061-device-return-contract.md)、
> [#3714](https://github.com/DUElost/stability-test-platform/issues/3714)、
> [#3696](https://github.com/DUElost/stability-test-platform/issues/3696)。

> **过渡措施**（ADR-0061 D8 第 6 条）：本手册是 ADR-0061 D5（阶段 B：欠账不交付、自动清偿）生效前的人工替代，
> 出口为阶段 B 生效。阶段 B 生效后，作者必须把本手册退役，或改为只保留排障内容。
> 本手册只覆盖开关机专项。休眠、MTBF、GPU、Monkey 专项中止后同样可能留下设备侧执行体（ADR-0061 §1.1），
> 目前没有对应手册；载体见 [#3718](https://github.com/DUElost/stability-test-platform/issues/3718)。

适用：`specialty_key=power-cycle` 的任何计划（如 `MLD_NX2-开关机专项-18H`、`周期回归-开关机-1h`）。
主机 IP 一律写作 `172.21.x.x` + 末段；序列号按掩码 `AHFFNX69****0169` 形式记录。

## 1. 中止后必做的解除核验（每台中止过的设备）

1. 取该 host 上实际接入的设备（**以 host 的 sysfs 枚举为准，不信平台 `host_id`**——现场挪动过设备）：

   ```bash
   ssh android@172.21.x.x 'for d in /sys/bus/usb/devices/*/; do
     [ "$(cat $d/idVendor 2>/dev/null)" = 0e8d ] || continue
     echo "$(basename $d) pid=$(cat $d/idProduct) serial=$(cat $d/serial 2>/dev/null)"; done'
   ```

   预期：`pid=201c` 且 `serial` 可读 = 正常态；`2000/2001/0003/2026/3000` = 刷机态或异常态（另按刷机手册处置）。

2. 回读链路标志（**必须在 `adb root` 之后重连再读**，否则 adbd 重启瞬间读不到文件，会假报"无 prefs"）：

   ```bash
   A=/usr/bin/adb; S=<serial>
   $A -s $S root; sleep 4          # 等 adbd 以 root 重新起来
   $A -s $S shell "grep -o 'running\" value=\"[a-z]*' \
     /data/data/com.tinno.autotesttool/shared_prefs/powercycle_runner.xml"
   $A -s $S shell "cat /proc/uptime | cut -d. -f1"
   ```

   预期：`running" value="false`；且相隔 10s 以上两次 `uptime` **连续增长**（不归零）。
   若 `running=true` 或 uptime 反复归零 → 该设备仍在武装，进 §2。

3. 记录：`device_id / serial 掩码 / host 末段 / running / uptime / current_count`。这些字段是
   后续判断"要不要现场干预"的唯一依据，不要只记"已中止"。

## 2. 可达设备的解除（标准动作）

用现成清理计划（示例为 `.85` 的 `plan 79`，步骤只有 `powercycle_finish`）：

```bash
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Origin: http://127.0.0.1' \
  -H 'Content-Type: application/json' \
  -d '{"device_ids":[<单个设备id>],"note":"解除武装链路"}' \
  http://127.0.0.1:8000/api/v1/plans/79/run
```

> **警告（必须一台一个 run）**：准入是**整 run 原子**的
> （`admission_pump.py`：*"one PlanRun admits as a unit … no partial per-host admission"*）。
> 把"在线设备 + 离线设备"或"两台离线设备"塞进同一个 run，会导致任一设备不可达时**整个 run 反复重排队**，
> 结果谁也清理不了。混合派发的直觉在这里是错的。

预期：`cleanup COMPLETED`。之后回 §1 第 2 步复测 `running=false`——**以复测为准**，不以作业状态为准。

## 3. 不可达（关机/离线）设备的处置

1. 按 §2 给该设备单独派一个清理 run。它不会立即执行，而是**停在准入等待**：设备一上线即被认领执行。
   由于 `device_host_map` 在准入评估时从 Device 表现取，且归属会随上线自愈，
   **插回任意一台 host 都会被正确认领**，无需要求现场插回原机位。
2. 通知现场：**上电后先别交付任何计划**。武装链路会自己重启若干轮；等 §1 的 uptime 连续增长两次以上
   成立，或等清理 run 的 `cleanup COMPLETED`，再交接。
3. 若现场无法上电（电池深放、线材/座子故障、长时间无心跳），按"疑似硬件/供电问题"另计，
   不要归因到开关机专项——判据：该设备**从未出现在**被中止 run 的 job 列表里，
   或掉线时刻早于专项启动时刻（实测 `…0143/…0157/…0150/…0158` 四台即属此类，掉线于 10-02/10-03，
   早于 10-12 启动的专项）。

## 4. 交付前的一行拦截（建议）

任何计划派发前，若目标设备满足「近 24h 内被 power-cycle 专项使用过 且 无 §1 的 `running=false` 回读记录」，
先补一次 §1/§2 再派。在 ADR-0061 D5（阶段 B）落地成平台强制之前，这条只能靠人执行。

## 5. B6 激活后的排障（自 B6 激活起生效）

本节每项 B6 行为自 B6 激活起生效，生效证据见 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) §5。
上方警告、过渡标注与 §1–§4 原样保留到阶段 B 生效；激活并贴出生效证据后的删除由另一个 PR 交付。
自 B6 激活起，清偿不以「一台设备一个 PlanRun」为前提，排障先读归还事实。

依据：[ADR-0061](../adr/ADR-0061-device-return-contract.md) D1、D5、D6 第 1 条、D8 第 6、8 条；
协议与代码索引见 [`07-execution-protocol.md` §9](../design/07-execution-protocol.md#9-b6-执行与设备归还自-b6-激活起生效)。

### 5.1 读取归还事实（自 B6 激活起生效）

> 警告：执行者不得把 job 已终态、设备 ONLINE、单个停止标志或接口 200 当作已归还。
> 可信确认取决于登记组合与本次回读；误判会让残留执行体干扰后续 PlanRun。

按顺序执行以下步骤：

1. 按 [`production-diagnostics.md`](./production-diagnostics.md) 的只读连接方式，查询目标 job：

   ```sql
   SELECT id, device_id, status, disarm_results, disarm_guard
   FROM job_instance
   WHERE id = <job id>;
   ```

   预期：1 行。`disarm_results` 只含本 job 建立过足迹的专项；空值表示无解除记录。
   job 的测试结论不因解除失败翻转；平台墙钟/停滞/信号中断优先记 incomplete。
2. 查询设备的归还状态与欠账占用：

   ```sql
   SELECT d.id, d.return_state, d.retired_at, dl.id AS lease_id,
          dl.lease_type, dl.status AS lease_status, dl.holder, dl.reason
   FROM device d
   LEFT JOIN device_leases dl ON dl.device_id = d.id AND dl.status = 'ACTIVE'
   WHERE d.id = <设备 id>;
   ```

   预期：1 行。`return_state` 按专项记录 state/source/updated_at，空值表示未初始化。
   设备聚合顺序为 debt＞unverified＞returned；缺任何已登记专项也为 unverified。
3. 查询未关闭条目及来源：

   ```sql
   SELECT id, family, source_kind, source_job_id, cause, created_at
   FROM device_return_debt
   WHERE device_id = <设备 id> AND state = 'open'
   ORDER BY created_at, id;
   ```

   预期：条目来源只有 job 或 stock（阶段 B 初始化）。init 检查失败产生的条目来源仍为 job，cause 带 `init_check:`。
   同等只读接口为 `GET /api/v1/devices/{device_id}/return-debts`，登录用户可读、按建立时间升序，设备不存在为 404，响应不含 evidence 原文。
4. 如果设备未退役且有未关闭条目却没有 ACTIVE 租约，停止处置并报告占用不变量异常。
5. 如果没有未关闭条目但专项仍为 debt，停止处置并报告状态不一致。

代码入口：`backend/services/agent_completion.py::complete_agent_job`；
`backend/services/device_return.py::device_aggregate_return_state`、`open_debts_for_device_sync`；
`backend/api/routes/devices.py::list_device_return_debts_endpoint`；
`backend/models/device_return_debt.py::DeviceReturnDebt`。依据：ADR-0061 D1 第 2–8 条、D4、D5 第 9、10 条。

### 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）

按顺序执行以下步骤：

1. 用 §5.1 第 2、3 步区分退役、既有 JOB 占用、欠账 MAINTENANCE 租约与未接入专项。
   退役设备保留 open 条目但没有欠账租约；JOB 占用期间不清偿；本批只有 power-cycle 接入。
2. 如果已有获批留存的 `POST /api/v1/heartbeat` 响应，读取 `return_debts` 中该 device_id 的专项与令牌字段，记录时隐藏令牌值。
   未留存响应时，将「欠账下发是否送达」记为未验证，并交 Agent host 维护者补证。
   该字段未下发时 Agent 不动作；下发入口为 `backend/api/routes/heartbeat.py::_process_heartbeat_with_db`，
   条目由 `backend/services/device_return.py::open_debts_for_host_sync` 组装。
3. 在目标 Agent host 用设备 id 查询清偿异常日志：

   ```bash
   grep -E 'return_debt_execution_failed device_id=<设备 id>([^0-9]|$)' \
     /opt/stability-test-agent/logs/agent_error.log /opt/stability-test-agent/logs/agent.log
   ```

   `return_debt_execution_failed` 带设备 id，日志位置由 `backend/agent/stability-test-agent.service` 的 StandardOutput/StandardError 指定。
   `agent_post_(retry|failed|conflict)` 的日志行不含目标设备，只作请求异常的旁证：

   ```bash
   grep -E 'agent_post_(retry|failed|conflict)' \
     /opt/stability-test-agent/logs/agent_error.log /opt/stability-test-agent/logs/agent.log
   ```

   Agent 的 `ReturnDebtConsumer._execute` 只在设备在线、无本 Agent 活跃 job 且取得 OperationScheduler permit 后执行；
   设备在线状态与在途 job 见平台设备页、PlanRun 设备矩阵。
   清偿成功不写日志；本次是否成功以控制面该专项的 `source=discharge` 与所涉条目的 `state=closed`、`closed_reason=discharged` 为准，
   对照专项 updated_at 与条目 closed_at 判断是否属于本次处理。只读查法如下：

   ```sql
   SELECT d.id, d.return_state -> 'power-cycle' ->> 'source' AS source,
          d.return_state -> 'power-cycle' ->> 'updated_at' AS return_updated_at,
          debt.id AS debt_id, debt.state, debt.closed_reason, debt.closed_at
   FROM device d
   LEFT JOIN device_return_debt debt ON debt.device_id = d.id AND debt.family = 'power-cycle'
   WHERE d.id = <设备 id>
   ORDER BY debt.created_at DESC, debt.id DESC
   LIMIT 20;
   ```
4. 对照最近一次清偿回报的正式值与 HTTP 返回码判断下一次触发：
   - failed/incomplete/not_executed 或回报未送达：至少间隔 30 分钟后重试。
   - executed_unverified 或 422：不自动重试，等待下次重连或欠账 token 变化。
   - 409：该 token 已被丢弃，不再为其重试；用 §5.1 第 2、3 步复读当前租约与条目。
5. 如果设备长期不可达或专项尚未接入，转现场恢复设备后清偿，或转 §5.3 人工放行。
6. 查询组合核验的诊断审计：

   ```sql
   SELECT id, timestamp, resource_type, resource_id,
          details ->> 'entry_point' AS entry_point,
          details ->> 'family' AS family, details ->> 'reason' AS reason
   FROM audit_logs
   WHERE action = 'unverified_probe_result'
     AND ((resource_type = 'device' AND resource_id = '<设备 id>')
       OR (resource_type = 'job_instance' AND resource_id = '<job id>'))
   ORDER BY id DESC
   LIMIT 20;
   ```

   预期：每个被替换或被拒的专项一行；只读重放不增行。普通完成的记录锚定 job_instance，清偿/init 检查锚定 device。
7. 如果原始值为 absent/not_installed，或平台、型号、权限模型、APK SHA、探针包身份、boot_completed 不满足可信组合，
   保留欠账并转 §5.3 或交规划者补验证；解除成功得到 executed_unverified 不关闭条目。
   probe 的 absent 不是「无足迹」证明；已安装应用的设备先 disarm 后可信 clear 才能 confirmed，未安装应用的设备没有 APK SHA，最多未验证。

执行者不得伪造 verified 标记、修改登记组合或反复创建清理 PlanRun 绕过欠账。
执行者不得用紧急释放接口清除欠账占用；该接口返回 409 `LEASE_IS_RETURN_DEBT`。
代码入口：`backend/agent/device_return.py::ReturnDebtConsumer.receive`、`reconnected`、`_schedule`、`_execute`；
`backend/agent/heartbeat_bindings.py::build_heartbeat_thread` 的 `_on_devices_reconnected`；
`backend/api/routes/agent_device_return.py::agent_return_discharge`；
`backend/agent/contracts/device_return.py::probe_result_policy`、`probe_entry_response`；
`backend/services/device_return.py::apply_discharge_sync`、`audit_unverified_probe`；
`backend/services/device_lease_emergency_release.py::release_terminal_job_lease`。
依据：ADR-0061 D2 第 5、7、8 条、D5 第 4、5 条；#3723 §3.5 v1.7.15、§3.8。

### 5.3 人工放行与后续 init（自 B6 激活起生效）

> 警告：只有 Owner 或管理员可以执行人工放行；执行者必须先取得本次生产写授权。
> 放行关闭所选专项条目并记 unverified/override，不是设备侧可信确认；错误放行会允许残留设备再次派发。

按顺序执行以下步骤：

1. 用 §5.1 第 3 步确定本次放行的专项与证据引用。
2. 如果没有本次写授权、原因类别或证据引用，停止本流程。
3. 在控制面宿主上以管理员身份调用人工放行接口；`$AUTH` 按
   [`honor-flash-runbook.md`](./honor-flash-runbook.md)「鉴权（运维 curl 模式）」取得：

   ```bash
   curl -sS -w '\nHTTP %{http_code}\n' -X POST \
     "http://127.0.0.1:8000/api/v1/devices/<设备 id>/return-override" \
     -H "$AUTH" -H 'Content-Type: application/json' \
     -d '{"families":["power-cycle"],"reason_category":"manual_verified","evidence_ref":"<证据引用>"}'
   ```

   预期：HTTP 200；响应 `data.closed_count`、`data.debt_lease_released`、`data.open_debt_count` 可核对。
   本例只放行 power-cycle；API 的 families 省略或空列表表示全部专项。界面原因类别含 flashed、factory_reset、manual_verified、other，证据必填。
4. 用 §5.1 第 2、3 步复读状态与剩余条目。
   预期：所选专项为 unverified/override；关闭最后一个条目才释放欠账租约，部分放行仍保留 ACTIVE 租约。
5. 查询本次放行审计：

   ```sql
   SELECT action, resource_id, details
   FROM audit_logs
   WHERE resource_type = 'device' AND resource_id = '<设备 id>'
     AND action = 'device_return_override'
   ORDER BY id DESC
   LIMIT 1;
   ```

   预期：details 含 families、reason_category、evidence_ref、closed_count、debt_lease_released。
6. 对下一次已获授权的 job，读取 `GET /api/v1/runs/{job_id}/steps` 响应中 `name=return_check.power-cycle` 的最新状态。
   预期：`status=COMPLETED` 表示 init 检查通过，`FAILED` 表示失败，其余状态表示尚未确认通过。
   该接口由 `backend/api/routes/runs.py::list_run_steps` 按 step_id 返回最新状态；也可以按只读连接方式查询该 job 的 step_trace：

   ```sql
   SELECT step_id, stage, event_type, status, original_ts, error_message
   FROM step_trace
   WHERE job_id = <job id> AND stage = 'init' AND step_id = 'return_check.power-cycle'
   ORDER BY original_ts DESC, id DESC
   LIMIT 1;
   ```

   预期：有记录时返回该检查的最新状态；无记录时没有本次检查状态的证据。
   init 检查失败时计划 teardown 为 SKIPPED；patrol 断点恢复不重跑检查但照常 teardown。

Agent 在计划建立足迹前先 probe，不能可信确认无足迹时先 disarm；检查结果只走
`POST /api/v1/agent/jobs/{job_id}/return-check`，不混入 job 终态 disarm_results。
合法 confirmed 关闭专项全部欠账；失败建立本 job 来源的欠账。旧控制面未声明 `return_init_check_available` 时只检查/解除、不上报，失败仍使 init 失败。
代码入口：`backend/api/routes/devices.py::return_override_endpoint`；`backend/services/device_return.py::apply_override_sync`、`apply_init_check_sync`；
`backend/agent/device_return.py::run_init_checks`；`backend/agent/pipeline_engine.py::PipelineEngine._execute_lifecycle`；
`backend/api/routes/agent_device_return.py::agent_init_return_check`。
依据：ADR-0061 D5 第 7 条、D6 第 1 条、D8 第 8 条；#3723 §3.5 v1.7.15–v1.7.18。
