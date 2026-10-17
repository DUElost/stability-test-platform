# 批次 B6：设备归还契约首批（阶段 A + 阶段 B + 开关机接入）——规划方案 v1.7（已裁决，待发布）

> **状态：已裁决，待发布。** Owner 于 2026-10-17 授权规划者裁决（[记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234602253)），裁决结果已并入本稿与 [ADR-0061 v1.1](../adr/ADR-0061-device-return-contract.md)。
> 本稿必须在 PR #3753 合入后整篇发布到 #3723，才成为实施规范；发布前任何实施者不得据此编码。当前 GitHub #3723 的有效版本仍为 v1.6。
> 本轮事实基线：`origin/main@945fcd4406cefa6299caa688376c9a54c2482868`（2026-10-17）；U3 #3748 `46ba633ee5e56afea836d9cb92bc2f16a68f95c4`；U5 #3747 `13a77c7fdd1097c70651a1961b20d0b8a063ded0`。
> 原 §1 扫描、§3.2.1/§3.3.1/§3.3.2 的历史复核记录保留其原基线；新语义以本稿 §3.0、§3.4.2、§3.6.2–3.6.5、§3.8、§4 与 §5 为准。冲突必须退回，不得拼接旧新规则。

> 历史基线：`origin/main@2c4906311`（2026-10-14）；§1 的缺陷与 §1.1–§1.3 的调用点均在该基线直读确认，行号以该基线为准。
> 派单复查：`origin/main@f1bc61990`（2026-10-15，#3719 合入后）；相对基线只改依赖锁（`backend/requirements*.lock`、`backend/requirements.txt`），本批源码锚点不变。
> 输入：ADR-0061 Accepted v1.0（[#3719](https://github.com/DUElost/stability-test-platform/pull/3719)）；Epic [#3718](https://github.com/DUElost/stability-test-platform/issues/3718)；#3714、#3696（开关机证据）；#894、#118（相关历史）。
> Appetite（Owner 2026-10-14 批准，ADR-0061 §8）：阶段 A 全部；阶段 B 全部；阶段 C 的开关机接入。激活成本：一次控制面部署（含迁移）、一次 Agent 全机队热更新、开关机族新版本发布与在用计划重指（重指另请 Owner 确认）、一次阶段 B 初始化。停止扩面：需要新增欠账条目与 job 解除结果之外的数据模型；需要改动准入核心算法（ADR-0026）；需要接入开关机之外的专项。
> 开工前置：#3719 已合入（`f1bc61990`）。实施者以 Accepted 的 ADR-0061 为依据；本方案与 ADR 冲突时以 ADR 为准，冲突本身按 §7 退回。

## 1. 形态压缩：一个 Epic → 五种缺陷形态

| 形态 | 缺陷判据 | 覆盖 | 同族扫描 | 参照实现 |
|---|---|---|---|---|
| F1 平台丢弃或终止解除 | 中止、manual_exit、停机路径上，解除步骤未执行或被平台自身终止（M1、M2、M5） | #3714、#3696 | §1.1 | `pipeline_engine.py:1840` 已只对 teardown 放行取消后的子进程（设计意图） |
| F2 解除结论不可见 | 终态只看 `termination_reason`；teardown 结论不进终态载荷（M3） | #3714 | `pipeline_runner.py:101-110`、`api_client.py:250-270`、`schemas/run.py:77` | `watcher_summary` 作为可选终态载荷字段的先例 |
| F3 释放租约不看归还 | JOB 租约在终态时被直接释放（M4），共 11 处 | #3718 | §1.2 | reconciler Check 1 / Check 3 的 `lease_type == JOB` 过滤 |
| F4 墙钟小于等待预算 | teardown 步骤墙钟小于脚本内部等待预算（M6） | plan 54/78/71/66/35、模板 | §1.3 | — |
| F5 开关机解除与收取耦合 | `powercycle_finish` 先等设备、再停、再收取，失败原因与解除是否完成不可区分 | #3714 | `powercycle_finish.py:236-281` | `_lib.py` 的 `_verify_stop_flags`（回读校验） |

### 1.1 F1 调用点（按来源追溯）

| 位置 | 来源 | 现状 | 本批处置 |
|---|---|---|---|
| `pipeline_engine.py:1551-1557` `_run_step_with_permit` | permit 等待超时且 `_canceled` 为真 | teardown 步直接返回 False：脚本不执行、不写 trace | U1 |
| `pipeline_engine.py:1078` `cancel()` | `control_handler.py:189`（abort 指令）；`recovery_runtime.py:39`（ABORT_LOCAL / CLEANUP）；`recovery_executor.py:85`（lease lost）；`graceful_shutdown.py:49`；`job_runner.py:73,89` | 无条件终止 `_active_process`，包括 teardown 子进程 | U1 |
| `pipeline_engine.py:2126` | patrol `EXIT_REQUESTED` | manual_exit 跳过整个 teardown | U1 |
| `graceful_shutdown.py:42,49` | SIGTERM（`stability-test-agent.service:22` `TimeoutStopSec=30`） | 先 `operation_scheduler.shutdown()` 再中止，teardown 取不到 permit | U1 |
| `recovery_executor.py:408-413` ABORT_LOCAL 分支 | recovery sync 下发 `ABORT_LOCAL` | 请求中止后**立即** `delete_active_job` 与 `clear_fencing_token`：teardown 仍在执行时租约停止续期，到期后被 reconciler 回收（间接终止解除） | U1：worker 存活时推迟到 worker 退出（`_cleanup_after_job_exit`）再清理；CLEANUP 分支（402–407）只在 boot 不一致、worker 已不存在时触发，保持现状 |

v1.5 补充：新执行状态的控制面消费者及通用 recycler 解除终止路径属于本节追加扫描，逐项清单、处置归属与边界见 §1.4。

### 1.2 F3：JOB 租约释放点（11 处，全部收进统一入口）

| # | 位置 | 路径 |
|---|---|---|
| 1 | `agent_completion.py:440` | Agent 终态回报 |
| 2 | `agent_recovery.py:410` | recovery：boot 不一致 |
| 3 | `agent_recovery.py:475` | recovery：UNKNOWN 宽限已过 |
| 4 | `agent_recovery.py:484` | recovery：终态 job 遗留租约 |
| 5 | `device_lease_reconciler.py:188` | 孤儿租约（job 行不存在） |
| 6 | `device_lease_reconciler.py:197` | 租约过期且 job 已终态 |
| 7 | `device_lease_reconciler.py:227` | UNKNOWN 宽限到期 → FAILED |
| 8 | `device_lease_reconciler.py:368` | 陈旧 UNKNOWN job |
| 9 | `device_lease_reconciler.py:452` | Check 3：终态 job 遗留 ACTIVE 租约 |
| 10 | `recycler.py:428` | PENDING 超时（未建立足迹） |
| 11 | `device_lease_emergency_release.py:87` | 管理员紧急释放接口（`devices.py:564`） |

`lease_manager.py:213` `release_lease` 与 `:245` `release_lease_sync` 保留为底层原语，只由统一入口调用。claim 侧无需改动：`agent_claim.py:220-232` 已排除任何 ACTIVE 租约的设备；准入侧 `plan_dispatcher_sync.py:223` 的 `active_lease` 原因前插入欠账原因（U3）。

### 1.3 F4：违规墙钟（生产只读，2026-10-14）

| plan | 名称 | teardown 墙钟 | `wait_device_online_seconds` | 最近运行 |
|---|---|---|---|---|
| 54 | 周期回归-开关机-1h | 300 | 600 | 2026-10-14 |
| 78 | MLD_NX2-开关机专项-18H | 300 | 600 | 2026-10-13 |
| 71 | B1-L2-canary-开关机-最短 | 300 | 600 | 2026-10-10 |
| 66 | MOD-LX3D-周期-Power-3H | 300 | 600 | 2026-10-09 |
| 35 | chain-powercycle-6h | 300 | 600 | 2026-09-14 |
| 模板 | `backend/schemas/pipeline_templates/powercycle.json` | 600 | 600（默认）+ 收取 3×120 | — |

非在用的开关机计划（23/26/28/30/32/37）记入 §8 证据，不重指。

### 1.4 v1.5：EXECUTING_DISARM 消费者与回收路径追加扫描

追加扫描基线：`origin/main@1e1f63e5a`、U2 `8372cc1d9dd5ae1a0671adde9ae7914d97cf4b43`（2026-10-16）。权威复现绑定旧 head `310a8803a`；规划者已核对新旧提交的相关回收、完成推导与 Plan 校验源码无差异。本节为生产路径静态核对，不冒称在新 head 重新运行了复核者的 PG 探针。

1. **状态生产与接收**：Agent 的状态 / PROGRESS 生产与 peer 判断由 U1 负责；U2 的 `agent_lease_extend.py` 白名单、执行心跳 / progress 写库，以及导入同一白名单的 `agent_coordinator_heartbeat.py` 为接收端。状态接收和执行心跳逻辑已接入；实施者必须保留「执行心跳、协调心跳、租约、进度分别取证，updated_at 不证明活性」的时钟纪律。
2. **abort reaper**：`device_lease_reconciler.py` 的 abort 主体时钟、`_disarm_grace_defers_reap`、行锁复读与转换，是本单已有处置点。其保护必须覆盖到下面所有会竞争写 UNKNOWN 的通用回收入口，不能只在局部 helper 生效。
3. **RUNNING timeout 漏扫点，U2 纳入修改**：`recycler.py` 的 `_KNOWN_EXECUTION_STATES` / 局部 `_known_states`、`_running_liveness_anchor`、`recycle_once` 候选 SQL / Python 判定、`_mark_running_timeout` 的 execution / unreported CAS 均尚未识别新状态。权威 PG 探针已确认这条路径会提前回收新鲜解除 job，属于 F1 的间接解除终止路径。
4. **同一 recycler 的相邻 UNKNOWN 写入口，U2 纳入有界保护与回归**：patrol-stall 的 Python / SQL collector 已只选 NULL / PATROL_SLEEP，通常不会选正在解除的 job；但 `_mark_patrol_stall` 的 CAS 未复核该子状态。step-trace-stall collector / `_mark_step_trace_stall` 依据旧 trace 和 patrol 心跳，不认识解除宽限。实施者必须让它们在候选到写入之间转入受保护解除时放弃回收，并验证旧 trace 晚到、进度变新时不会绕开保护。本节不把这两处静态竞态候选冒称为规划者已取得的新独立 PG 复现。
5. **只读活性投影，U2 同步**：`services/plan_run_devices.py::running_heartbeat_deadline` 与 recycler 有明确的基础时钟同步约定，目前也只识别 EXECUTING_STEP。实施者必须同步新状态的基础执行心跳分支并验证；不得用两小时前的 started_at 展示新鲜解除为从未上报。本批不新增界面交互或专门的 abort 宽限展示，U6 范围不变。
6. **透明接线与状态清理，无需改写语义**：`agent_recovery.py` 冻结恢复载荷直接透传 execution_state；`state_machine.py` 对离开运行态清理子状态；job 模型列是字符串，没有漏加的数据库枚举。实施者必须保留上述行为与现有回归，不得把旧 job 复活或改变终态语义。
7. **其它回收权威边界保留**：`session_watchdog` 的 host 失联、reconciler 的租约真实过期 / boot 不一致 / UNKNOWN 宽限、PENDING 回收及 §1.2 租约释放链不获得永久豁免。本轮不改其阈值、状态机或 U3 的欠账转换范围；有效解除保护不等于无视真实租约 / host 失效。

## 2. 模型级问题（v1.7 已裁决）

ADR-0061 已裁决为 Accepted v1.1（Owner 2026-10-17 授权规划者裁决，[记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234602253)），正文随 PR #3753 合入生效。
裁决接受[修订审议稿](2026-adr0061-v11-proposal.md)的五项：Q=7200s、每步 E、整 job 尾段 24h 上限、历史缺墙钟 300s、15s / 60s 证据；
`job_instance.disarm_guard` JSONB、可选阶段请求 / 响应与 LocalDB 字段；D2.5 统一核验；平台终止优先与按实际版本判定步骤角色；部署冻结。
裁决附三处修改，本稿 §3.0.5、§3.8、§4.1 已同步：
- M1：阶段证据必须经协调心跳以不超过 30 秒的间隔送达，按服务器接收时刻判新鲜；extend-batch（60 秒）不能单独满足 60 秒新鲜度。
- M2：Agent 取得 permit 后立即启动解除，不得等待控制面确认；控制面在首次收到执行阶段证据时冻结 D_i。
- M3：终态回报不得因不可信探针结果被整份 422 拒绝（outbox 会转入死信，测试结论丢失）；不可信专项改按 step trace 推导，结构化拒绝只用于清偿与 init 检查。
新增模型的 Appetite 扩展见 ADR-0061 §8.2。U8 以及本轮 U3/U5 返修必须等 #3723 发布 v1.7 后开工，不能自行补设计。

D2.5 的统一核验按 ADR-0061 v1.1 D2 第 7、8 条执行，普通 / 迟到 / 清偿 / init 四入口见 §3.8。部署继续冻结；不新增阶段 B 开关。
Owner 要求持续部署时必须另提开关、迁移与回退裁决，不得由实施者临时实现。

另有一项批次级裁决，不改 ADR 正文：旧登记包 `powercycle_disarm` 1.0.0–1.0.2 的处置（§3.6.3）。
Owner 已于 2026-10-17 选定方案 A：退役，并按 git 树对象恢复发布（[裁决记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234224273)）。
方案 B（保留缺包诊断并改部署 SOP 预期）未采用。

## 3. 实施分组、版本与档位

### 3.0 本批确定的实现约定（ADR-0061 授权方案确定的事项）

1. **机器值**：
   - 解除结果（D1 第 2 条）：`confirmed`、`executed_unverified`、`failed`、`readback_failed`、`not_executed`、`incomplete`。
   - 专项归还状态（D1 第 3 条）：`returned`、`debt`、`unverified`；来源 `job`、`init_check`、`discharge`、`override`、`stock`。
   - 欠账条目（D5 第 10 条）：状态 `open`、`closed`；关闭原因 `discharged`、`overridden`。
2. **登记表**（D2 第 6 条）：`backend/agent/contracts/device_return.py`（ADR-0054 共享契约包，Agent 与控制面共用）。两层：
   - `FAMILY_STEPS`：五个专项「建立足迹的 init 步骤」与「承担解除的 teardown 步骤」，用于阶段 A 判定足迹与映射解除结果：

     | 专项 | 建立足迹 | 承担解除 |
     |---|---|---|
     | power-cycle | `powercycle_setup` | 接入前 `powercycle_finish`；接入后 `powercycle_disarm` |
     | sleep | `sleep_setup` | `sleep_finish` |
     | mtbf | `mtbf_setup` | `mtbf_finish` |
     | gpu | `gpu_setup` | `gpu_finish` |
     | monkey | `monkey_setup`、`monkey_launch`、`monkey_test` | `monkey_teardown` |

   - `ONBOARDED`：已接入专项登记、预算和可信验证报告；本批只有 power-cycle。全局接入不改变旧 job 的实际脚本角色。
   - **job 级步骤角色**：U5 必须使共享 helper 接受实际 step/version（及冻结包 SHA），供 Agent 选择、结果归属与控制面推导共用。
     `powercycle_finish@1.0.x`（manifest 可解析的旧版本，含1.0.8）仍承担解除；`powercycle_finish@1.1.0`只收取；
     `powercycle_disarm@1.0.0–1.0.3`承担解除，但旧版本不因此获得可信探针资格。
     同 job 混合旧 finish 与新 disarm 时，manual_exit/abort 必须按 pipeline 顺序执行所有实际承担解除的启用步骤，不能只取一个；
     自然结束还执行收取步骤。新 finish 的收取失败不得覆盖已完成的 disarm。
     同专项多次解除按发生顺序取最后真实执行结果；后续 SKIPPED 不覆盖已经完成的结果。
   - **未知/缺版本**：新派发必须先从 manifest 解析并冻结版本/包 SHA；解析不了则拒绝派发并报告，不猜默认最新版本。
     现有派发已满足本句，不新增派发实现：lifecycle 组装把步骤 `version` 写入 `pipeline_def`（`plan_dispatcher_core.py` 的
     `build_lifecycle_from_steps` / `build_lifecycle_from_snapshot`）；保存入口 `_validate_script_refs` 与派发取元数据只接受 `is_active` 的版本；
     包 SHA 由（name, version）在 append-only manifest 中唯一确定；U8 政策必须按此派生，`disarm_guard` 记录的包 SHA 必须等于该派生值。
     历史 job 缺版本或未知版本的 `powercycle_finish` 必须保守保留为潜在解除义务，执行其完整 teardown，记录 `unknown_legacy_version`；
     未知 `powercycle_disarm` 同样保留解除义务。成功最多 executed_unverified，无执行证据则 not_executed/已开始被中断则 incomplete。
     已知只有 finish@1.1.0 且无解除步骤的有足迹 job 必须记 not_executed，不把收取当解除。
     控制面必须在 Plan 保存入口拒绝不符合接入形态的已接入专项计划（缺登记解除步骤，或仍用旧合并版 finish），由 U5 按 §3.6.5 实施；
     该校验不得阻断步骤未修改的存量计划派发。

3. **足迹判定**：job 的 `init` 中，`FAMILY_STEPS` 所列步骤一旦开始执行，即视为建立了该专项足迹（保守取「开始」而非「完成」）。
4. **D3 第 7 条冲突处理**：Agent 在 teardown 前或期间发现本 job 租约已失（`extend_lock` 返回 409）时，不得执行解除，回报 `not_executed`（原因 `lease_lost`）。清偿与 init 检查只在持有对应租约（欠账租约令牌或本 job 租约）时执行。
5. **有界保护**：U8 必须逐项实现 [ADR-0061 v1.1](../adr/ADR-0061-device-return-contract.md) D3 第 8 条与 D8 第 8 条；审议稿中标有「已按裁决修改」的原句不得作为依据。
   本稿选定 Q=7200s（全部步骤共用）；E_i=冻结正整数墙钟；历史缺失/非法墙钟固定300s；
   T0=最早有效服务器退出触发；D_job=T0+min(Q+ΣE_i,86400s)，包含本次路径全部 teardown。
   控制面首次收到某步骤执行阶段证据时固定 D_i=min(收到时刻+E_i,D_job)，同一步骤重试不重置；排队累计扣 Q。
   Agent 取得 permit 后必须立即启动解除，不得等待控制面确认（M2）；本地执行窗口取 E_i 与最近一次返回的 D_job 剩余时间的较小者。
   持久化位置为新 `job_instance.disarm_guard` JSONB 和现有 LocalDB 活跃记录；协议使用现有状态加 queue/execute 阶段，不新增执行状态。
   queue 的15s轮询存活与 execute 的15s实际 PROGRESS 分开；控制面各自60s新鲜度（按服务器接收时刻），执行还要求执行心跳有效。
   阶段证据必须经协调心跳送达；任一 job 处于解除阶段时，Agent 必须至少每30s发一次协调心跳，不受该间隔配置放大影响（M1）。extend-batch 可以同时携带，不能作为唯一通道。
   每次保护还必须验证租约/token/host/boot/设备占用，普通 WAITING 无豁免；updated_at 不作为证据。
   重复 seq 不刷新存活，乱序/重启/重复abort不得延长任一已冻结截止；最终 SQL/CAS 必须复读最新事实。
   自然结束多步默认尾段上限8280s，manual_exit只解除为7860s；达到任一适用截止后停止并记录义务，不能换步骤继续延长 job。
6. **兼容推导及平台终止优先级**：U8 必须同步 Agent 汇总与 `disarm_derive.py`。
   已开始解除被平台 wall/stall 截断（受控124/125及终止来源）或信号中断143/-15/137/-9，必须先取 incomplete；
   该优先级高于成功 JSON、readback 和普通 failed。仅有历史 trace 退出码时124/125按平台保留码推导 incomplete并记录 `legacy_reserved_exit`，普通脚本失败对照使用其它退出码。
   无终态回报且已建立足迹必须补 incomplete；终态回报存在但无任何解除开始证据取 not_executed；STARTED无终态取 incomplete；
   真正 COMPLETED 取 executed_unverified，不从 job.status 猜归还；普通非平台 FAILED 取 failed。
   先按 §3.0.2 识别该 job 的实际步骤角色，再按 original_ts 选真实最后执行结果，迟到STARTED不覆盖后来终态。
   控制面对未接入专项的 incomplete 也必须转欠账；普通 failed 仍保留 ADR D8.4 的 unverified 过渡。

7. **告警阈值**（D7 第 3 条）：
   - `stp_disarm_result_total{family,result}`：1 小时内 `not_executed` 或 `incomplete` 增量 > 0 → warning；单专项 24 小时 `failed` 占比 > 5% → warning。
   - `stp_device_return_debt_open{family}` > 50 → warning；`stp_device_return_debt_oldest_seconds` > 86400 → warning；`stp_device_return_override_total` 24 小时增量 > 10 → warning。
8. **清偿下发通道**：Agent host 心跳响应（`backend/api/routes/heartbeat.py:187`）新增可选字段 `return_debts`（该 host 上在线设备的未关闭条目、专项、欠账租约令牌）；清偿回报走新端点 `POST /api/v1/agent/devices/{device_id}/return-discharge`。
9. **一次性命令**：`backend/scripts/init_device_return.py`（阶段 B 初始化：默认 `--dry-run`，`--apply` 才写库；可重复执行）与 `--release-all-debt-leases`（回退用，写审计）。

### 3.1 分组表

| 组 | 覆盖 | 目标版本 | 实施档位 | 合入前复核 | 依赖 |
|---|---|---|---|---|---|
| U1 [#3726](https://github.com/DUElost/stability-test-platform/issues/3726) Agent 解除执行保证与结果回报 | F1；F2（Agent 侧）；D3 第 1–4、7、8 条（Agent 侧）；§3.0 第 2 条的 `FAMILY_STEPS` | Agent 代码，无脚本版本 | 高阶 | **必须**：执行语义、全机队热更新后难回退 | — |
| U2 [#3727](https://github.com/DUElost/stability-test-platform/issues/3727) 控制面解除结果、宽限续期与墙钟校验 | F2（控制面）；F4；D3 第 6、8 条；D4；D7 第 1 条 | 迁移 1 个（`job_instance.disarm_results`） | 高阶 | **必须**：迁移与回收语义 | 可与 U1 并行开发；合入在 U1 之后 |
| U3 [#3728](https://github.com/DUElost/stability-test-platform/issues/3728) 控制面欠账 | F3；D1 第 3–8 条；D5；D7 第 2 条；ADR-0057 D2 修订 | 迁移 1 个（`device_return_debt`、`device.return_state`） | 高阶 | **必须**：租约与派发安全、难回退 | U2 合入后 |
| U4 [#3729](https://github.com/DUElost/stability-test-platform/issues/3729) Agent 清偿与 init 检查 | D5 第 5 条（Agent 侧）；D6 第 1 条 | Agent 代码 | 高阶 | **必须**：设备侧动作 | U1、U3、U5、U8 合入后；与U8 Agent文件串行 |
| U5 [#3730](https://github.com/DUElost/stability-test-platform/issues/3730) 开关机接入 | F5；D2（开关机）；`ONBOARDED` 登记；模板 pin | `powercycle_disarm` 1.0.3（下一修复版）；`powercycle_finish` 1.1.0 | 中阶 | **必须**：设备侧动作、真机验证 | U1 合入后（登记表同一文件） |
| U6 [#3731](https://github.com/DUElost/stability-test-platform/issues/3731) 前端 | D4 第 4 条；D5 第 7、9 条（界面） | — | 中阶 | 否 | U2、U3 合入后 |
| U7 [#3732](https://github.com/DUElost/stability-test-platform/issues/3732) 文档 | 执行协议、紧急释放手册、开关机手册、运维索引 | — | 中阶 | 否 | U3/U8 合入后；与本轮规划文档串行 |
| U8 [#3752](https://github.com/DUElost/stability-test-platform/issues/3752) 平台归还保护与结果政策接线 | F1/F2跟进；排队/执行持久截止；124/125；D2.5统一核验 | 追加1个nullable JSONB迁移；阶段协议可选字段 | 高阶 | **必须**：租约、回收与结果信任 | U3→U5完成合入后，新建独立PR |

U1 #3733 已合入 `611e777d5a87e1907508fb9be2eebff6adfca2b5`；U2 #3734 已合入
`962d73ee751904a1cf25394f0c2cc91c67b0447c`，保留原权威通过和历史，不重开旧PR。
本次不把新缺口塞进U4。U3保留 #3728/PR #3748；U5保留 #3730/PR #3747。

实际文件分工（以diff复核，不形成目录所有权）：

- U3：`backend/scripts/init_device_return.py`；`backend/services/device_return.py`中初始化事务/历史有界查询的必需修改；
  CLI/初始化PG测试和原Agent Note。其余既有PR差异保持原验收，不顺带承接新guard/类别政策。
- U5：`backend/agent/contracts/device_return.py`中实际版本角色helper与登记；`backend/agent/scripts/powercycle_disarm/`的轮询进度；
  必需的Agent调用签名适配；`tool_manifest.json`、powercycle模板/pin、相应测试及原Note。既有finish@1.1.0包内容不变。
  §3.6.5 的保存入口校验：共享契约中的纯函数，`backend/api/routes/plans.py` 三个保存入口的调用，`backend/tests/api/` 下的入口测试。
  另含 `tools/dev/check_script_packages.py` 的 `--publish-restored` 模式及其自检（§3.6.3 方案 A，Owner 2026-10-17 选定）。
  U5 必须把这些路径并入原 Execution 的完整 scope（`ai_work.py update --scope` 会整体替换，必须保留原有路径）。
- U8：共享契约的guard协议/结果优先级/类别政策；`pipeline_engine.py`、`api_client.py`、`pipeline_runner.py`、`coordinator.py`、`lease_renewer.py`、
  `backend/agent/registry/local_db.py`的持久恢复；`backend/models/job.py`及新增alembic revision；`backend/api/schemas/run.py`、`backend/api/schemas/agent.py`、
  `backend/api/routes/agent_api.py`与 `backend/services/agent_lease_extend.py`、`agent_coordinator_heartbeat.py` 接收服务；`backend/services/plan_run_abort.py`中最早退出锚点的必要接线；
  `backend/core/job_timeout_config.py`、`backend/scheduler/device_lease_reconciler.py`、`recycler.py`、
  `backend/services/plan_run_devices.py`、`agent_completion.py`、`disarm_derive.py`、`device_return.py`、
  `backend/api/routes/agent_device_return.py`的统一核验接线；必要前端API类型同步；匹配测试/Note。
  路径不存在或真实调用落在清单外时必须先退回规划者，不自行扩面。
- U4：清偿执行与init检查功能，必须导入U8共用政策/协议，不能复制核验；依赖U8结束共享Agent编辑。
- U6：稳定API后的详情/欠账/放行交互；U7：最终执行协议和运维文档，依赖U8并与本轮文档串行。

保守合入顺序：U3 → U5 → U8 → U4；U6在U3后可独立推进；U7在U8后推进。
Owner裁决且发布v1.7后，U3与U5仅在实际文件无交叉时可并行返修；U5必要的pipeline_engine适配不得与U8并发。
U3 与 U5 都修改 `backend/api/routes/plans.py`，但位于不同函数（U3 为 `_dispatch_warnings`，U5 为 §3.6.5 的保存入口校验）。
两者可以并行开发；U5 必须在 U3 合入后基于最新 origin/main 集成，并重跑 §3.6.5 与 §4.1 U5 的入口验收。
U8以同时包含U3/U5的当时最新origin/main开独立分支、PR目标main，不采用stacked PR，不复制父分支diff。

### 3.2 U1 修复内容

1. `_run_step_with_permit`：teardown不得因普通取消丢弃解除排队；真实失租必须按 §3.0.4 禁止新执行。U8必须把排队/执行预算改为 §3.0.5；SchedulerShutdown仍立即返回并记incomplete。
2. `cancel()`：当前活动进程属于 teardown 时只置取消标志，不得终止子进程。`ABORT_LOCAL`、重复 abort、设备重连触发的 recovery 不得终止执行中的 teardown（M2），也不得在 worker 存活时清除其 fencing 令牌或本地活跃记录（§1.1 `recovery_executor.py:408-413` 一行）。
3. manual_exit：执行解除。专项已接入时只执行登记的解除步骤；未接入时执行该专项完整的 teardown 步骤（解除与收取未拆分，只能整体执行）。
4. 优雅停机：先对在跑 job 派发中止，在 20 秒内让已开始的解除步骤收尾，再关闭调度器；未完成者按 `incomplete` 写入本地 outbox。不得改动 systemd unit。
5. 终态载荷新增 `disarm_results`：按 `FAMILY_STEPS` 为每个建立过足迹的专项回报 §3.0 第 1 条的六值与证据（步骤 id、退出码、回读摘要）。
6. 解除步骤执行期间以 `EXECUTING_DISARM` 上报执行状态（§3.0 第 5 条）。
7. 租约已失时按 §3.0 第 4 条处理。

### 3.2.1 U1 权威复核 R1–R4 返修裁定（v1.4 历史记录）

本节保留当时复核判据；v1.7的job实际版本选择和排队/执行上限以 §3.0.2/5 为准，U8回归必须同步旧预算断言。

依据：[PR #3733 权威复核评论](https://github.com/DUElost/stability-test-platform/pull/3733#issuecomment-6191561976)，绑定 head `a5c1897e4315d23103d1b50214ea30deaae84e7f`。规划者已核对该提交的相关生产路径与测试；四类均为既定 §3.0 / §3.2 / §4 U1 范围内的实现遗漏，交回原 U1 实施者。复核者的五个行为探针在 `d58bf774b` 执行；两提交的 `backend/agent` tree 均为 `50aeb7f57d9f7906cd705d2ff0c901deb6ebe813`。规划者本轮没有重跑这些探针或危险变异，不把源码核对写成新的独立复现。

**R1：普通取消不得丢弃排队解除。** 实施者必须在 `_run_step_with_permit` 区分 `PermitCancelled` 与 `SchedulerShutdown`。teardown 遇到普通 abort 导致的 `PermitCancelled` 时，必须重新经既有 scheduler 等待 permit；等待总截止时刻必须沿用进入该步骤时的原值，重复取消不得重置预算。`SchedulerShutdown` 仍必须立即退出并记录 `incomplete`；其它真正的 permit 契约错误不得被当成普通取消无限重试。实施者不得绕过 permit、手工修改 permit 计数或修改 OperationScheduler 的通用取消语义。确认失租后的执行禁令仍按 R4 承接。

**R2：SKIPPED 不等于执行成功。** 实施者必须使禁用步骤在申请 permit、探测租约或启动脚本前进入原有 SKIPPED 路径；该路径不得上报 `EXECUTING_DISARM`。retry 结果处理必须先识别 `StepResult.skipped`，不得只凭 `success=True` 将解除映射为已执行。对已建立足迹但没有真正执行解除的专项，结果必须为 `not_executed`，证据中必须保留禁用或跳过原因。实施者可以保留既有 SKIPPED trace 与 job / teardown 聚合约定，但不得从聚合成功反推解除已执行，也不得覆盖该专项已有的真实完成结论。

**R3：足迹从步骤开始边界登记。** 实施者必须删除 acquire 前的 init 足迹登记，并把执行入口中的登记移到「已取得 permit、启用检查通过、进入实际步骤执行并写 STARTED」的同一开始边界。禁用、等待中取消、未获 permit、SchedulerShutdown 均不得建立足迹。步骤开始后失败仍必须保留足迹；不得改成脚本成功后才登记，也不得等到确认设备执行体已启动才登记。同一专项重试时，足迹集合必须保持幂等。

**R4：明确失租必须阻止新的解除尝试。** 实施者必须继续使用与普通取消分离的、当前 worker 内单向生效的 `_lease_lost_for_disarm` 标记，并接通以下确认来源：引擎租约验证返回 409、teardown 租约探测返回 409、既有 LeaseRenewer 明确失租回调。`handle_lease_lost` 必须经 JobRunnerState 向当前 runner 传递明确失租通知，再沿用原 abort / occupancy 清理顺序；普通 abort、ABORT_LOCAL 与取消等待者不得自行冒充明确失租。实施者不得用 `_is_lock_lost()` 直接代替该标记，因为当前实现将普通 abort 合并到该返回值。

- 实施者必须在首次解除尝试、每次重试再入及退避结束后的启动边界检查明确失租；确认后不得启动新的解除调用。后续网络失败或成功响应不得清除已经确认的失租标记。
- 失租阻止执行或重试时，实施者必须按 §3.0 第 4 条回报 `not_executed` / `reason=lease_lost`，并保留已有尝试的步骤 id、退出码与摘要；该结果不得被未授权的后续成功覆盖，也不得抹掉其它已完成专项的事实。
- 本项不授权增加「失租时终止正在运行的 teardown」的新进程信号路径；运行中进程的取消与墙钟处理继续遵守 §3.2 第 2 条与既有超时边界。实施者必须保持 fencing / 当前 worker 的归属校验、ABORT_LOCAL 存活 worker 延迟清理与 CLEANUP 原行为。

**本轮修改范围**：原 U1 的 `pipeline_engine.py`、`job_runner.py`、`recovery_executor.py`、`recovery_runtime.py`、`coordinator.py`、共享契约、Agent 测试及本单 Agent Note 内的必要改动。实施者不得把 #3739 的生产 PID/PGID 安全修复、临时变异插件修复或其它专项接入塞入本 PR。若实现确需修改上述范围之外的生产接线或改动公开执行状态 / 数据模型，实施者必须停止并退回规划者；不得自行扩大 scope。

**本轮有限行为验收（原 §4 U1 与通用门禁继续有效）**：

1. R1 原真实控制链反例：真实 OperationScheduler + HostRunCoordinator + JobRunnerState + control handler，唯一 permit 被占时重复 abort，随后释放 permit，解除必须执行；同时验证重复取消不延长原截止时刻、SchedulerShutdown 返回 `incomplete`、cap=1 不被突破。脚本执行必须使用无进程信号的安全替身。
2. R2 原反例：有足迹的禁用解除，实际动作调用次数为 0，结果为 `not_executed`，保留 SKIPPED 事实；正常启用解除仍按既有六值映射，真实完成结果不得因另一个 skipped 分支被降级。
3. R3 两个原反例：禁用 init、scheduler 已关闭而从未获 permit 的 init，足迹集合均为空；增加正向对照，启用 init 真正开始后即使失败，足迹仍存在。
4. R4 原引擎级反例：第 1 次失败期间探测到 409，`retry=1` 时动作调用次数必须为 1；失租在首次执行前已确认时调用次数必须为 0。补齐既有 LeaseRenewer → handle_lease_lost → JobRunnerState → runner 的接线行为测试，并验证普通 abort 不误设失租标记、普通失败在有效租约下仍可以按原 retry 配置重试。测试仅用 HTTP / 动作替身，不接真实设备。
5. 实施者必须把原五个探针移为仓库回归测试或给出同调用链、同判据的等价测试，绑定最终 HEAD。修复前失败 / 修复后通过必须由真实生产分支差异产生；`_PRE_FIX_BEHAVIOR=True` 中主动 `pytest.fail` 不构成变异证据。不得改弱原断言使旧实现也通过。
6. 实施者必须复跑本次受影响的 permit / control / lease-lost / retry / footprint / graceful-shutdown 测试，保留此前真实 20 秒 LocalDB 持久化、manual_exit、六值、ABORT_LOCAL 回归。测试必须经项目 runner 与隔离环境；完成 §4 通用门禁后，最终远端提交的 required checks 仍必须成功。因安全边界不能完成的项目必须标 pending，不得假称通过。

**#3739 与复核证据边界**：#3739 是已发生事故的 P1 独立事项，不是对 U1 已上线功能的归因，也不是本轮新增实施范围。实施者与复核者不得在当前宿主直接运行留存的 `review_mutation.py` / `run_mutations.py` 或原危险 cancel 变异。cgroup 内存硬顶不提供信号权限隔离。涉及取消 / 终止的负向验证，必须在实际信号调用边界由可信桩拦截，或在能够隔离信号权限的环境执行；不得复制函数 globals 后假设原模块 patch 仍生效，不得让无可信 PID 的 mock 落入真实终止函数。

权威复核者可以按 batch-review.md §4 采用安全的等价证据；若尚不能安全取得该证据，则继续标 pending，或等待 #3739 安全修复后再补验。#3739 不要求 U1 越界修复，但本轮未取得有效负向证据的事实不得省略；没有最终独立复核「通过」，任何人不得将 PR 转 Ready。

**D11 流转裁定**：本轮 R1–R4 均直接落在现行验收语义内。前次 A 为停机等待阻塞，C 为修复 A 后的持久化回归，当前记录不足以认定连续两轮同类非回归新发现。规划者据此回派一次 R1–R4 实现修正，不通过更换编号重置既有复核历史。若后续权威复核记录满足 D11 第 3 条，必须先回到规划者修订判据，不得继续追加同类返修。

### 3.3 U2 修复内容

1. 迁移：`job_instance.disarm_results`（JSONB，可空）。
2. `/complete`（`agent_api.py:162`、`schemas/run.py:77`）接受 `disarm_results`；缺失时按 §3.0 第 6 条推导；持久化到 `disarm_results`。不得写入 `status_reason`（`report_service.py:269`）。
3. `_VALID_EXECUTION_STATES` 增加 `EXECUTING_DISARM`；abort reaper 与 §1.4 的通用回收消费者必须按 §3.0 第 5 条和 §3.3.2 的同一保护判据协作。通用 recycler 的候选选择、最终判定、行锁 / CAS 复核与基础活性投影均由 U2 收口。
4. D3 第 6 条：控制面在保存 Plan 时，必须对启用且在 `ONBOARDED` 登记了 `min_wall_clock_seconds` 的步骤，要求显式配置正整数 `timeout_seconds`，且该值必须不小于登记预算。控制面必须拒绝省略、`null`、`0`、负数或低于预算的值，并返回结构化 422。控制面不得读取本机 `STP_STEP_WALL_CLOCK_SECONDS` 为远端 Agent 的预算背书。未登记步骤与禁用步骤沿用现有规则；细则与验收见 §3.3.1。
5. D7 第 1 条：`stp_disarm_result_total{family,result}`（`backend/core/metrics.py`）与告警（`deploy/prometheus/alerts-stability-platform.yml` 及其 `.test.yml`）。
6. API：job 详情返回 `disarm_results`；同步 `frontend/src/utils/api/types.ts`。

### 3.3.1 U2 墙钟修正裁定（v1.3）

**问题与选定修法**：`ad294e402`（最新远端 `c3272e48e` 保留相同逻辑）用控制面本机环境变量校验省略墙钟，但 dispatcher 对省略字段不写入下发步骤，远端 Agent 使用自身环境变量或 300 秒。两者不是同一配置事实。规划者选择「已登记步骤显式墙钟」：保存值、PlanStep 值、下发值与 Agent 执行值保持一致；本批不引入自动补值或跨主机默认值同步。

以下规则适用于 U2：

1. 控制面必须在创建 Plan、更新 steps、追加 chain tail 三个保存入口执行 §3.3 第 4 条。对更新请求没有携带 steps 的情形，控制面可以沿用现有行为；存量计划迁移与重指仍按 §5 执行。
2. 控制面必须保留错误码 `WALL_CLOCK_BELOW_ONBOARDED_BUDGET` 与结构化 `violations`。每项必须包含 `step_key`、`script_name`、收到的 `timeout_seconds`（省略或 null 时记 null）与 `min_wall_clock_seconds`；控制面不得把本机 env 推导值冒充请求值。
3. 控制面必须以该脚本版本的 `Script.default_params` 合并步骤 `params` 后的有效参数计算登记预算，步骤参数覆盖默认参数，与 dispatcher 一致。实施者可以在现有路由中按脚本名与版本批量取默认参数；不得新建共享运行时或更改脚本已发布默认参数。
4. 实施者必须删除控制面读取 `STP_STEP_WALL_CLOCK_SECONDS` 的放行逻辑，并改写 `test_omit_timeout_env_at_or_above_budget_ok`：控制面 env 大于或等于预算时，省略墙钟仍必须返回 422。实施者不得修改 Agent 的默认墙钟语义、未登记步骤的省略语义或 `apply_step_timing_fields` 的通用省略规则。
5. 实施者必须验证既有 dispatcher 将显式墙钟原值写入 lifecycle 与快照构建结果；远端 Agent 环境变量不一致时，执行仍必须采用显式下发值。实施者不得只验证 helper 返回值。
6. U1 合入后，实施者必须在本单元最终复核前按 §3.1 导入共享契约，消除本单元的临时常量与空登记表副本。U2 必须保持 draft，合入顺序仍为 U1 → U2。

**有限验收清单（补充 §4 U2，原验收项继续有效）**：

- 已登记启用步骤：省略、null、0、负数、659（预算 660）全部返回结构化 422；660 与 700 通过。
- 控制面 env 未设、660、10000 时，省略或 null 均被拒绝；远端 Agent env 未设、1、43200 时，显式下发的 660 仍按 660 执行。
- 创建、更新 steps、追加 chain tail 三个真实 API 入口均验证拒绝路径；拒绝前后 Plan / PlanStep 不得出现部分写入。保存成功后，数据库中的显式值与构建下发步骤中的值必须一致。
- 参数预算覆盖默认参数、步骤覆盖参数两条路径；空登记表、未登记步骤与禁用步骤保持原有行为。
- 实施者必须对原「控制面 env=660、远端 Agent 无 env、请求省略墙钟」反例保留修复前红、修复后绿的行为证据；不得通过改变测试环境令两端默认值偶然相同。

**当前 CI 集成项**：`c3272e48e` 合入当前 main 后形成 `9b7e24a6d081` 与 `c3d4e5f6a7b8` 双 head，当前 `pr-agent-tests` 与 `pr-migrate-empty-db` 均失败。实施者必须在自己的分支核实 U2 迁移尚未发布后，将本单元迁移接到当前 main 的唯一 head（本次核对为 `9b7e24a6d081`），并同步本单元迁移契约测试。实施者不得修改 main 已合入迁移、弱化单 head 门禁或在生产库试跑迁移；若 U2 迁移已经发布，必须停止并退回规划者。验收必须包含隔离空库 upgrade / downgrade / 再 upgrade 与最终提交的 required checks。

### 3.3.2 U2 权威复核退回修订（v1.5）

依据：[PR #3734 权威复核](https://github.com/DUElost/stability-test-platform/pull/3734#issuecomment-6192251976)，绑定 `310a8803a`。R1 是规划漏扫新状态消费者，规划者承担方案补齐；R2 / R3 是原方案内的实现遗漏。此次接受 §7 退回并在原 U2 中补齐，不新增单元、数据模型、业务状态或 env，不改变准入核心算法。所有以下规则与 §3.3.1 继续适用。

本节记录v1.5已合入验收；其旧abort+max墙钟和旧新鲜度公式由v1.7 §3.0.5替换，U8必须更新相应断言。SQL/CAS最终复读与安全边界仍适用。

#### A. R1：统一解除保护与通用回收接线

1. **选定结构**：实施者必须把现有 abort 主体时钟与解除宽限的纯判断收敛到既有 `backend/core/job_timeout_config.py`，由 abort reaper 与通用 recycler 共同调用。实施者可以保留原模块的兼容导出薄壳，避免破坏现有测试及调用点；不得在两个回收器复制两套阈值 / 解析规则，也不得新建 Agent 与控制面共享运行时。纯判断不得访问 DB、读取生产 env 文件或执行副作用。
2. **保护条件**：对仍 RUNNING、`execution_state=EXECUTING_DISARM`，且受该 job 有效 abort 主体覆盖、进度在两个协调心跳周期内、尚未到达「该 abort 请求时刻 + 对应解除步骤墙钟」的 job，两个回收器必须保持 RUNNING。新鲜进度足以触发该有界保护，通用 recycler 不得因旧 started_at 或另一个较旧的基础执行心跳绕开保护。实施者必须复用原主体覆盖语义：run 名单、host abort、host late-claim、二者取更早有效时钟、历史数据回退与时区解析；重复 abort 不得重置时钟。
3. **基础时钟**：在不满足上述有界保护时，EXECUTING_DISARM 必须作为已知执行态按 `last_execution_heartbeat_at` 参与通用活性判断；信号缺失时沿用既有 dispatch-time 回退。NULL / 真未知状态、WAITING_*、PATROL_SLEEP 与 EXECUTING_STEP 原判据必须保持。`updated_at`、租约续期本身或协调心跳不得被替换成执行进度证据。
4. **SQL 与并发**：实施者必须同步已知状态集合、候选 SQL、Python 判定及 `_mark_running_timeout` CAS，保持有界批次、keyset 分页和现有索引查询形态；不得通过每轮全表加载规避 SQL 分类。进入 UNKNOWN 的最终写入前，必须在现有事务 / 行锁 / CAS 防线上复核最新状态、进度和保护条件；不得只在 collector 过滤。若候选之后 job 转为解除、进度更新或已经完成，陈旧候选必须放弃写入。实施者不得引入相反的 job / PlanRun / host 锁顺序。
5. **相邻回收路径**：patrol-stall、step-trace-stall 的 collector 与最终写入必须一致承接保护；旧 patrol / trace 活动不得在有效解除宽限期间导致 UNKNOWN。保护失效后仍按现行规则回收，不得将 EXECUTING_DISARM 无条件从所有回收中排除，也不得对其它执行态新增基于 progress / execution heartbeat 的全局豁免（保留 #3061 / #3146 的原防僵尸语义）。
6. **宽限终点**：进度中断、缺失或超过解除墙钟时，保护必须失效；abort reaper 按原正常中止宽限回收，通用 recycler 按原基础超时回收。两者可以因各自 tick 时点不同先后处理，但不得让受保护 job 提前 UNKNOWN，也不得让失去保护的 job 永远不回收。UNKNOWN 时 JOB 租约保留，后续仍走既有 reconciler；U3 的租约转换职责不变。
7. **基础投影**：`plan_run_devices.running_heartbeat_deadline` 必须同步已知执行态的基础时钟分支，并保留现有兼容别名。该投影仍表示基础心跳 deadline，不把它包装成新的解除完成事实；本批不扩展 U6 界面范围。

#### B. R2：兼容推导按发生事实，不按到达顺序

1. 控制面必须先按 job / stage / step 与 FAMILY_STEPS 识别解除 trace，再依据事件类型、状态与 `original_ts` 选取实际发生顺序上的解除终态；`created_at`、插入 id、查询返回次序不得作为主要业务时序。并列记录可以用已有稳定键保持确定性；实际事实无法区分时必须按 §7 退回，不得直接偏向成功。
2. 更早发生但迟到的 STARTED / RUNNING 不得覆盖其后发生的 COMPLETED / FAILED。也不得一律优先成功：后发生的 FAILED 必须胜过先发生的 COMPLETED，后发生的成功亦按原推导表处理。实施者必须同时修正查询端与 helper 的行为，不能只给当前 SQL 加 order_by 后仍让 helper 依赖输入顺序。
3. 原 §3.0 第 6 条映射、五专项范围、Agent 已上报结果的路径与终态重放幂等必须保持；不得从 job.status 猜解除成功，不得写 status_reason。无解除终态证据时不得推成已执行。复核探索中的「单条 SKIPPED」及标准 dispatcher 已过滤的 disabled pipeline，本次不提升为新的阻塞验收项。

#### C. R3：三个保存入口统一错误契约

对合法脚本引用、已登记启用步骤的预算问题，创建、更新 steps、追加 chain tail 三入口必须在通用 lifecycle 形态校验抢先拦截 0 / 负数之前执行既有登记预算校验；脚本引用与参数校验的必要前置仍必须满足。实施者必须返回 §3.3.1 约定的 `WALL_CLOCK_BELOW_ONBOARDED_BUDGET`、message 与完整 violations，不得只满足 HTTP 422。不得放宽 lifecycle 校验，也不得改变未登记 / 禁用步骤原契约或其它多重错误输入的通用优先级。本问题仅指错误结构不一致，不得报告为原实现曾放行 0 / 负数。

#### D. 本次追加 scope 与停止边界

除原 U2 文件外，原实施者可以且必须按上述处置需要修改：`backend/scheduler/recycler.py`、既有 `backend/core/job_timeout_config.py`、`backend/services/plan_run_devices.py` 及对应 `backend/tests/` 回归。本轮不授权修改 Agent、systemd、生产数据、已发布脚本或新增迁移。原 U2 `device_lease_reconciler.py`、`agent_completion.py`、`plans.py` 与原 Agent Note 仍为本单修改面。

实施者必须在自己的原 Execution 中把新增路径并入完整 scope（`ai_work.py update --scope` 会替换 scope，必须保留仍有效的原范围）；不得新建重复 issue Execution 或改他人记录。U1 当前仍 draft，原 U1 → U2 合入顺序、共享契约最终导入、独立复核及激活闸门均保留。需要本节之外的新回收路径、公开状态、数据模型、env、准入算法或新的共享运行时时，必须停止并退回规划者。

#### E. 有限行为验收（与原 §4 U2 / §3.3.1 合并执行）

1. **原 R1 真入口**：隔离 PG 中构造 RUNNING 已持续 2 小时、ACTIVE JOB 租约有效、执行 / 协调 / 进度新鲜、abort+90s、解除墙钟600s，先确认保护成立，再分别运行真实 `recycle_once()` 与 abort reaper；两个调用顺序均保持 RUNNING / EXECUTING_DISARM、租约 ACTIVE，且不产生提前回收审计 / 计数。不得只测试 helper。增加基础执行心跳较旧但进度新鲜、仍在有效解除宽限内的对照。
2. **失效对照**：abort+墙钟到达 / 超过、进度缺失 / 超过两个周期时，真实 abort reaper 按原规则回收为 UNKNOWN 且保留 JOB 租约；无 abort 且执行心跳陈旧的解除 job 仍可被通用 recycler 回收。WAITING 新鲜协调心跳、普通执行态、NULL / 未知状态及真实失联 / 租约过期的既有行为不得倒退。覆盖 run / host / 名单外 job / host late-claim / 混合时区原规则。
3. **其它 UNKNOWN 入口与竞态**：用现有隔离 PG 夹具覆盖 running-timeout、patrol-stall、step-trace-stall；collector 后切换为受保护解除或刷新进度、以及 collector 后已终态三种状态变化，最终写入均不得覆盖最新事实。保护失效后，原 patrol / trace 真僵尸仍必须可被回收；同步验证基础 deadline 投影使用执行心跳，而非旧 started_at。
4. **R2 helper 与真实 complete**：至少覆盖「先入库 COMPLETED、后入库 original_ts 更早的 STARTED」及其反向交付顺序，helper 对输入排列不敏感；真实无 disarm_results 的 `/complete` 必须落库 executed_unverified。再覆盖后发生 FAILED 胜过旧成功、后发生成功胜过旧失败、原 signal 退出码映射、没有解除 trace 的回退。详情返回、原测试结论、status_reason 与同载荷重放不重复计数均继续验证。
5. **R3 三入口矩阵**：创建 / 更新 / append 各自覆盖省略、null、0、负数、659（预算660），全部核对 HTTP422、约定 code / message / violations；拒绝后 Plan / PlanStep 不得部分写入。660 / 700 正向及不同 Agent env 的显式值下发、默认参数 / 步骤覆盖、未登记 / 禁用对照按 §3.3.1 保留。
6. **红绿与门禁**：保留原权威探针的修前失败证据；每个本次核心行为必须有修后通过及安全变异 / 等价负向证据。重点是去掉 recycler 接线 / 写入保护、重置 trace 输入顺序和恢复 create 错误校验顺序后，对应行为应变红。不得使用主动 pytest.fail 冒充生产分支变异。运行受影响 scheduler、completion、heartbeat、deadline projection、Plan API、迁移 / metric 契约回归，再执行原通用门禁并等待最终 HEAD 的 required checks。只按实际执行报告；未完成项继续 pending。

#3739 的危险 cancel 变异不在本轮范围；不得执行留存危险插件或真实宿主信号。测试必须走项目 runner、隔离 PG 与可信信号边界桩；本次安全回收 / API 变异不需要重编译 Agent cancel。规划者本轮只做扫描与方案修订，未重跑权威 PG 探针，不将上述验收清单写成已完成结果。

### 3.4 U3 修复内容

1. 迁移：`device_return_debt`（单数表名；列：`device_id`、`family`、`source_kind`（`job`/`stock`）、`source_job_id`、`source_fencing_token`、`cause`、`state`、`closed_reason`、`closed_at`、`created_at`、`evidence` JSONB；部分唯一索引（`device_id`,`family`,`source_kind`,`source_job_id`）`WHERE state='open'`）；`device.return_state`（JSONB）。
2. 新服务 `backend/services/device_return.py`：统一入口（ADR-0061 D5 第 2、3 条）、条目写入与关闭、专项状态映射（D1 第 3–6 条）、清偿、人工放行、初始化。
3. §1.2 的 11 处全部改为调用统一入口；并用 `tools/dev/source_anchor.py` 的 SourceGuard 加守卫：统一入口之外不得直接调用 `release_lease` / `release_lease_sync` 释放 JOB 租约。
4. 欠账租约：`MAINTENANCE`、holder 平台、reason「归还欠账」、不设到期；转换在终态同一事务内完成并推进 `lease_generation`；终态处理、初始化、清偿、放行、退役在同一设备行锁下串行（D5 第 10 条）。
5. reconciler、紧急释放接口不得释放欠账租约；紧急释放 JOB 租约改经统一入口。
6. 准入：`plan_dispatcher_sync.py:223` 的 `active_lease` 之前新增原因 `device_return_debt`；预览 `_dispatch_warnings`（`plans.py:1302`）新增欠账告警。
7. 清偿端点（§3.0 第 8 条）与心跳响应 `return_debts`；人工放行端点 `POST /api/v1/devices/{device_id}/return-override`（admin，原因类别 + 证据引用，审计）。
8. 退役（`device_retirement.py:57`）与取消退役按 D5 第 8 条改造。
9. D7 第 2 条指标与告警；`backend/scripts/init_device_return.py`（§3.0 第 9 条）。

### 3.4.1 U3 首轮规划核验补齐（v1.6）

规划核验绑定 PR #3748 `7b3d43f095553b5e1973b67bd56e54f5ddd6163c`。以下均为原 §3.4 / §4 U3 与 ADR-0061 D1/D4/D5/D7 已要求的清单内实施遗漏，不新增业务模型，不重置独立复核历史。

1. 实施者必须让统一入口在没有终态回报时，依据实际 init STARTED 等足迹证据为相关专项补记 `incomplete` 并建立欠账；不得因 `disarm_results` 为空就释放可能建立过足迹的 JOB 租约。无足迹的 PENDING 回收仍按原规则释放，不得一律造欠账。
2. 实施者必须落实 D1.7 / D5.11 的初始化历史判定：按每专项最近建立足迹或执行解除的真实 job 及结束时间、后续成功解除覆盖规则生成存量状态/条目；不得把所有专项直接初始化为 unverified。必须覆盖空闲设备原子建欠账租约、已占用设备只记条目而不抢租约、dry-run 不写及重复执行幂等。
3. 实施者必须在终态、清偿、放行、迟到补偿及初始化事务完成前按真实 open 条目重算专项状态。只要该专项仍有 open 条目，专项状态必须为 debt；当前 job 的成功或 unverified 不得覆盖其它来源的未关闭义务。
4. 实施者必须把 D5.6 迟到 confirmed 处理接到真实 `/complete` 的终态分支，而不是仅定义未调用的 helper。必须按条目保存的原 job / 原 fencing token 核对来源，只关闭同源条目，不改变原测试终态，不因该补偿放宽其它终态载荷冲突或旧 token 规则。
5. 实施者必须修复本 HEAD required CI 的实际失败：新增两个审计 action 的保留期登记、API 响应模型与客户端契约登记、欠账 Gauge 的真实生产者与告警接线。允许对 `backend/scheduler/audit_log_cleanup.py`、相关既有 audit/API/metric 契约测试登记项及 U3 服务/指标生产者作必需修改，并并入原完整 scope；不得通过删除守卫、增加无证据豁免或只调用一次空 setter 使门禁变绿。
6. 实施者必须把规划者四个隔离 PG 反例落为仓库回归，并补齐同族 async/真实入口对照；每个修复点保留修前红/修后绿及匹配安全负向证据。最终 HEAD 的六项 required checks 必须成功。未完成的迁移升降升、schema、根/Agent/相关套件必须按实际结果记录，不复用旧 SHA 绿灯。

### 3.4.2 U3 CLI逐设备事务与有限重试（v1.7）

1. U3必须以设备id keyset分页（每页100，按id升序，游标为最后id）；分页查询必须使用短读事务并在处理设备前结束。
   初始化CLI不得 `.all()` 全量设备ORM对象，也不得把全批设备装入同一外层事务。
2. 每台设备必须使用独立Session/顶层事务：dry-run完整rollback并close后才处理下一台；apply逐台commit并close。
   保存点不构成跨设备事务边界；单台失败必须回滚本台，不得回滚已成功设备或遗留部分条目/代次/租约。
3. claim唯一冲突或代次CAS冲突时，CLI必须回滚并结束本台整个事务，在新Session中重新取得设备锁和最新占用后重试。
   每台最多3次，总等待退避为0.1s、0.2s；已有其它占用时只记条目，不抢占；3次失败必须计冲突并继续后续设备。
   CLI有任何失败/重试耗尽必须返回非零退出码1，正常成功或正常已占用处理返回0，统计区分成功/占用/跳过/失败/冲突耗尽。
4. dry-run不得改业务状态、条目、租约、代次或审计；必须实际释放锁，不能靠不提交掩盖持锁。
   `--limit`跨页累计限制设备数；重复运行必须幂等。历史查询只做必要有界化，不改 §3.4.1 的最近相关job与后续成功覆盖规则。
5. U3必须测试真实CLI main(argv)，在第二台入口前用独立PG连接NOWAIT锁第一台，dry-run与apply都必须成功。
   必须再测第一台已提交、第二台注入真实失败后第一台仍保存，第二台无部分状态；测试service helper不能代替这两条。

### 3.5 U4 修复内容

1. 读取心跳响应 `return_debts`；在设备重连（`heartbeat_bindings.py` 的重连钩子）与周期检查时，对有未关闭条目的已接入专项执行登记的解除与回读（经 OperationScheduler permit），按 §3.0 第 8 条回报，携带欠账租约令牌。控制面未下发该字段时不做任何事。
2. D6 第 1 条：job 的 `init` 前，对每个已接入专项先以探针模式检查；命中则执行解除；`readback_failed` 或 `failed` 时以 init 失败结束该 job；结果计入 `disarm_results`（来源 `init_check`）。step trace 的 stage 为 `init`，step_id 为 `return_check.<family>`。

### 3.6 U5 修复内容

1. 新族 `powercycle_disarm` 的最终目标修复版本为1.0.3：参数 `mode`（`probe` / `disarm`）、`wait_device_online_seconds`。`disarm`：等待上线 → 写 `auto_resume=false`、`running=false` → 停服务 → root 优先回读；`probe`：只回读。输出结构化结果：§3.0 第 1 条的值与回读证据；原始读取失败与正式解除结果必须按 §3.6.1 区分，不得把读取失败报为无足迹。
2. `powercycle_finish` 1.1.0：只做结果收取，不再停服务、不再写 prefs。
3. `ONBOARDED` 登记 power-cycle：解除预算与 `min_wall_clock_seconds(params)`（`disarm`：`wait_device_online_seconds + 60`；`finish`：`collect_attempts × collect_retry_wait_seconds + 60`）。
4. 真机验证（D2 第 5 条）：按设备类别验证正反判例、重启后行为、运行时读取失败识别；首批验证 MTK `MLD_LX2`、`MLD_LX3`、`MLD_NX2`（近 30 天开关机设备 213 / 148 / 49 台）。其它 MTK 类别能取到设备则一并验证，取不到的不登记为已验证。展锐 `Z2581` / `Z2582` 不验证（#3722）。验证报告附在 PR 中，列出设备、APK sha、步骤与结果。
5. 模板 `powercycle.json`：teardown 改为 `[powercycle_disarm 1.0.3, powercycle_finish 1.1.0]`，墙钟满足第 3 条；按 script-version-lifecycle §A 登记新版本。

### 3.6.1 U5 读取诊断、类别验证与交付边界（v1.6）

规划核验绑定 PR #3747 `74c3bdd3684457c16f8650b6a89d8d166ed5de2b`。ADR-0061 D2.5 高于原 §3.6 第 1 条中未加类别限定的“读不到 → readback_failed”；本节澄清两种字段的不同含义，不改变六值模型。

1. 脚本可以在原始 `readback` / evidence 中记录 `readback_failed`（权限、不可达、超时等读取诊断）。脚本及控制面不得把该原始状态无条件复制成正式 `disarm_result=readback_failed`。
2. 对已通过 D2.5 真机验证的设备类别，解除已执行但本次可信探针无法读取时，正式结果可以为 readback_failed。只有可信探针本次确认无足迹时，正式结果才可以为 confirmed。
3. 对未通过 D2.5 的类别，解除动作已完成且未取得可信回读时，正式结果必须为 executed_unverified，并保留原始失败诊断；不得为 confirmed 或 readback_failed。若解除动作本身失败、未开始或未完成，实施者必须依 D1 的对应值保留实际失败事实，不得借此规则把未执行动作报成已执行。未验证类别的 probe 不得作为可信无足迹判据或因读取失败而报告可信回读失败；其无法判定结果必须保持不通过，原始诊断必须保留。
4. 实施者必须对“类别未验证 + 解除成功 + 原始读取失败”“类别已验证 + 解除成功 + 读取失败”“已验证 + clear”“解除自身失败/无法执行”“probe 无可信判据”分别增加有限对照。必须验证脚本结构化输出经 Agent 汇总和终态回报后的正式值，不能只测 helper。实施者不得把未验证读取失败断言为正式 readback_failed 的旧测试当成权威。
5. MLD_LX2 / MLD_LX3 / MLD_NX2 的 D2.5 真机正反判例、重启后行为与读取失败识别仍是 §4 U5 的交付验收项。无独立测试设备时可以交代码预审，但必须保持 draft、真机 pending；独立复核者不得签 U5 最终通过。若测试类别或能力均无法满足，按 §7 退回，不得虚填已验证类别。
6. U5 可以对共享结果映射函数作结构化结果互通必需的最小适配，并必须补齐原 U1 六值、失租和取消边界回归；不得改动 U1 的 permit、终止或停机机制。修正脚本包内容与真机类别登记时，实施者必须按 script-version-lifecycle 与 ADR-0051 处理版本，必要时追加下一可用版本并同步 ONBOARDED 最低版本、模板 pin 与测试。实施者不得原地改写已发布或冻结的包/版本事实；不能把旧包或旧 HEAD 的 CI/设备证据代签最终内容。

### 3.6.2 U5 旧计划兼容与等待进度（v1.7）

U5必须按 §3.0.2 修改共享选择/归属helper及最小调用适配；manual_exit、普通abort、自然结束的实际执行与结果归属必须一致。
U5不得简单把所有finish重新纳入解除，或靠删除ONBOARDED绕过兼容。

上线等待必须使用monotonic总截止，沿用600s默认等待，不扩660s步骤墙钟；每次真实device/boot轮询完成后产生递增PROGRESS，
相邻戳最多15s。ADB单次调用必须受剩余总预算及15s轮询周期约束；sleep必须受剩余截止约束。
实现必须在同步轮询中打戳，不新增后台线程。超时/失租退出不得继续打戳；假时钟测试必须带独立迭代上界。
U8的执行新鲜度固定60s，15s进度容纳短网络延迟，但不能扩大任何截止；queue存活由U8的permit等待循环独立负责。

### 3.6.3 冻结版本、恢复与发布清单（v1.7）

U5必须追加 `powercycle_disarm@1.0.3`，同步ONBOARDED最低版本、模板pin和测试；`powercycle_finish@1.1.0`保持原包。
若1.0.3已被并发登记或登记后内容变化，U5必须顺延下一patch并同步 §3.1、本节、§5发布清单和原PR，报告实际最终版本/包SHA；
不得用旧包证据代签新包。版本变化是append-only执行规则，不授权改变本稿语义。

以下条目全部保留登记。任何人不得删除条目、改 SHA 或复用其版本号。
2026-10-17 本稿首版作者已在临时目录确定性重建四项，SHA 均与 manifest 一致；原规划者（B6 v1.0 作者）复核时另按树对象重建三个旧 disarm 版本，结果相同（只读，未写站点）：

| 条目 | 源提交（追溯用） | 族树对象（恢复锚点） | package_sha256 |
|---|---|---|---|
| `powercycle_disarm@1.0.0` | `74c3bdd3684457c16f8650b6a89d8d166ed5de2b` | `9943ccae94c232151ee7ce826deecd355485a50e` | `d51f38397e01374d888e422a574387bb1b7c2f5fc626a815671972e53b37f5f9` |
| `powercycle_disarm@1.0.1` | `f906b4e19a918fc04cf0fc8e421b6b8ef0ebea09` | `153c867d9bc2438144506884625b1d66749becd5` | `4fc50d485272d8107e98d778b7fe0c7ce7af7572b54203e23653e708e319b064` |
| `powercycle_disarm@1.0.2` | `13a77c7fdd1097c70651a1961b20d0b8a063ded0` | `1fdf49a42f9ed3bae7a453302ce147db9909cf03` | `9c9453ba59fb556e217af319be4212e50766917b6bf95015a2441871f492a9d1` |
| `powercycle_finish@1.1.0`（最新版，由 `--publish` 从当前族树发布） | `74c3bdd3684457c16f8650b6a89d8d166ed5de2b` | `32e4209696cafa15f234492a699ec5655def4272` | `76d9883df880d2156f016db6bc8adcf902f978ae87690331181b07c897ec1aff` |

恢复方法：以族树对象为锚点。树对象按内容寻址，rebase 后不变；提交号只用于追溯。
用 `git archive <树对象>` 解到临时目录，用 `git ls-tree -r --name-only <树对象>` 列出包成员，
调用 `tools/dev/package_tool_asset.py::build_deterministic_tar_gz`，比较 manifest 的 `package_sha256`。
U5 必须在最终 HEAD 运行 `git rev-list --objects <最终HEAD> -- backend/agent/scripts/powercycle_disarm`，
确认三个旧树对象都在输出中，再按树对象重建并核对 SHA。

**缺包事实**（2026-10-17 原规划者核对 main `945fcd440`）：

- `check_script_packages.py --publish` 只发布每族最新的未退役条目（`publish_latest`），三个旧 disarm 版本不会随之发布。
- scan 对站点缺包的条目记 `package_missing`。`retired: true` 但库内没有对应行的条目，scan 仍会读包建一条 inactive 行，
  读不到包同样记 `package_missing`（`backend/services/script_catalog.py` 退役分支）。三个旧版本只登记在未合入的 U5 分支、从未发布，库内不会有它们的行，所以只做退役消不掉该诊断。
- 部署 SOP 把 #3661 存量以外的 `package_missing` 定为停止条件（`.claude/skills/control-plane-deploy/SKILL.md` 阶段 B 第 4、5 步）。
  保留缺包而不改 SOP，此后每次部署都会在该步停下。
- 原稿「保留登记、不退役、不发布」还留下一个隐患：日后若有人补发这些包，scan 会把三个旧版本建成 active 行，带已知缺陷的版本将可被计划选用。

**处置**：Owner 已于 2026-10-17 选定方案 A（[裁决记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234224273)）。
U5 必须实现第 1、3、4 条；激活执行者必须执行第 2、5 条。

退役与零引用复核：

1. U5 必须在原 PR 中把 `powercycle_disarm` 1.0.0、1.0.1、1.0.2 三个条目改为 `retired: true`。
   这是 `check_tool_manifest.py` 允许的单向翻转。三者只登记在未合入的 U5 分支、从未发布；激活前的零引用由第 2 条复核。
2. 激活执行者必须在 scan 前按 [`production-diagnostics.md`](../operations/production-diagnostics.md) 的只读身份运行：
   `SELECT count(*) FROM plan_step WHERE script_name = 'powercycle_disarm' AND script_version IN ('1.0.0', '1.0.1', '1.0.2');`
   预期：0。如果不为 0，执行者必须停止激活并报告 Owner。

恢复发布（方案 A）：scan 不新增缺包。

3. U5 必须为 `tools/dev/check_script_packages.py` 增加 `--publish-restored NAME@VERSION --source-tree TREE --packages-root DIR` 模式。
   该模式必须按上面的恢复方法从树对象重建包；重建 SHA 等于 manifest 条目时才写站点，否则必须拒绝。
   站点已有同字节包时必须跳过；已有不同字节时必须拒绝。写入必须沿用 `publish_latest` 的临时文件加 `os.replace`；解包必须使用 `tarfile` 的 `filter="data"`。
   该模式只能写入 `packages/<NAME>/<VERSION>.tar.gz` 一个文件，不得改写 `tool_manifest.json` 或站点 `packages/manifest.json`。
4. U5 必须为该模式补自检：SHA 一致时写入、SHA 不一致时拒绝、同字节跳过、异字节拒绝、条目不存在时拒绝。
   U5 还必须在临时站点目录对三个旧版本各真实运行一次，报告重建 SHA 与写入结果。
5. 激活执行者必须在 §5 第 2 步先运行 `--publish`，再对三个旧版本各运行一次 `--publish-restored`，然后 scan。
   预期：scan 响应的 `package_missing` 只含 #3661 的存量条目，不含 `powercycle_disarm` 或 `powercycle_finish`；
   `GET /api/v1/scripts?is_active=false` 的 `data` 含三个旧 disarm 版本，`GET /api/v1/scripts?is_active=true` 的 `data` 含 disarm 最终版与 `powercycle_finish@1.1.0`。
   该模式同样可以用于 #3661 的同类缺口；本批不承接 #3661。

未采用的方案 B：不发布三个旧包，保留缺包诊断，并改部署 SOP 的缺包预期。任何人不得按方案 B 实施。

### 3.6.4 真机与最终包（v1.7）

MTK MLD_LX2/LX3/NX2的有/无足迹、重启续跑行为和读取失败识别必须绑定最终探针包SHA、实际APK SHA及权限模型。
三类当前均pending，verified_device_classes仍为空。Owner未指定独立测试设备时只能代码预审，不能签U5最终通过。
展锐Z2581/Z2582仍不纳入；不得借用忙设备、扫描生产或虚填已验证类别。
验证组合必须满足 §3.8，不以修改参数声明类别替代实际事实。

### 3.6.5 Plan 保存入口的接入形态校验（v1.7，U5）

本节落实 §3.0.2 末句，承接单元为 U5。只收取的 `powercycle_finish@1.1.0` 由 U5 拆出；ADR-0061 D2 第 4 条要求同一 PR 更新契约，
因此不符合接入形态的计划（缺解除步骤，或仍用旧合并版 finish）也由 U5 在同一 PR 封住。U8 不得重复实现本节。

1. U5 必须在共享契约 `backend/agent/contracts/device_return.py` 增加纯函数：输入为 PlanStep 形态的步骤列表与 `ONBOARDED`，输出缺口列表。
   该函数必须复用 §3.0.2 的 job 级步骤角色 helper，不得另写脚本名或版本判断。
2. 判定规则：对每个已在 `ONBOARDED` 登记的专项，如果计划含该专项启用的 init 足迹步骤（`FAMILY_STEPS[专项]["footprint"]`），
   启用的 teardown 必须是拆分后的接入形态：至少有一个该专项登记的解除脚本（`disarm_onboarded`，本批为 `powercycle_disarm`），
   版本不低于 `disarm_min_version`；启用的 `powercycle_finish` 版本不低于 `finish_min_version`。版本按数字段比较（`1.0.10` 高于 `1.0.3`）。
   控制面必须按下表拒绝，每个违规项带一个 `reason`：

   | 形态 | `reason` |
   |---|---|
   | teardown 只有 `powercycle_finish@1.1.0`，没有解除步骤（主反例）；或 teardown 没有任何开关机步骤 | `disarm_missing` |
   | 解除步骤存在，但全部 `enabled=false` | `disarm_disabled` |
   | 启用的解除步骤版本都低于 `disarm_min_version` | `disarm_below_min_version` |
   | 启用的 `powercycle_finish` 低于 `finish_min_version`（旧合并版 1.0.x，无论是否另有解除步骤） | `finish_below_min_version` |

   旧合并版 finish 被拒有两个原因：§3.0.2 只为已冻结的历史 job 保留它的解除角色；
   登记的 `min_wall_clock_seconds` 公式只覆盖 1.1.0 的收取预算，套在 1.0.x 上会低估其等待预算，M6 会重现。
   这一项也让 `ONBOARDED` 中的 `disarm_min_version`、`finish_min_version` 有了生产消费方。
   计划没有启用的足迹步骤时（例如只收取结果的计划），本节不约束该计划。
3. U5 必须在 `backend/api/routes/plans.py` 的三个保存入口调用该校验：`POST /plans`；`PUT /plans/{plan_id}`（仅当请求携带 `steps`）；
   `POST /plans/{plan_id}/append-chain-tail`。调用位置必须在 `_validate_script_refs` 之后、`_validate_onboarded_wall_clock` 之前；
   同一请求同时违反本节规则与墙钟预算时，控制面必须返回本节错误。
   `PUT` 携带的 `steps` 与原步骤相同也必须校验（与墙钟校验一致）；存量旧计划因此必须先重指，才能再次携带 `steps` 保存。
   2026-10-17 核对 main `945fcd440`：除历史 alembic 迁移外，PlanStep 只在这三个入口写入（`plans.py` 三处 `db.add(PlanStep(`）；
   没有原地改 `plan_step` 的接口，也没有单独的步骤启停接口。
4. 错误契约：HTTP 422，`detail.code` 为 `ONBOARDED_TEARDOWN_INVALID`，`detail.message` 写明 ADR-0061 D2、D8 第 3 条与 #3723 §3.6.5。
   `detail.violations` 每项必须包含 `family`、`reason`（第 2 条表中的值）、`footprint_step_keys`、`required_disarm_script`、
   `disarm_min_version`、`finish_min_version`，以及 `teardown_steps`（该专项相关 teardown 步骤的 `step_key`、`script_name`、`script_version`、`enabled`、`role`）。
   一个专项命中多条时，控制面必须每条各给一个违规项。控制面拒绝时不得部分写入 Plan / PlanStep；`PUT` 被拒后原步骤必须保持不变。
5. 本校验只作用于保存入口。控制面不得因本节阻断步骤未修改的存量计划派发：
   存量计划在运行时按 §3.0.2 的 job 级角色处理，在用计划由 §5 第 4 步经 Owner 确认重指。
6. U5 必须保持 U2 墙钟矩阵（§3.3.1、§3.3.2 C）原有结论；只有同一请求同时触发两类问题、按第 3 条改为返回本节错误码的用例，才可以调整断言，并必须在 PR 中逐条列出。

### 3.7 U6、U7 修复内容

- U6：job 详情展示各专项解除结果；设备页展示归还状态、未关闭条目与来源；admin 的人工放行操作（必填原因类别与证据）；派发预览展示欠账告警。
- U7：`docs/design/07-execution-protocol.md`（终态载荷 `disarm_results`、`EXECUTING_DISARM`、欠账生命周期）；`docs/operations/device-lease-emergency-release.md`（不得释放欠账租约、JOB 租约经统一入口）；开关机运维手册按 ADR-0061 D8 第 6 条改为只保留排障内容；`docs/operations/README.md` 同步。U7 改动的句子必须按 ADR-0059 写作约定书写。

### 3.8 U8 统一结果核验与平台保护（v1.7）

U8必须实现ADR修订提案 §2–§4经Owner裁决后的规则，并将policy保留在ADR-0054共享纯契约内；
控制面层负责取可信持久事实，Agent不得import控制面模块；service叶模块不得回依赖agent_completion造成C5环。

四入口：普通 `/complete`、迟到confirmed补偿、`return-discharge`、U4未来init_check均必须在业务修改前调用同一政策（ADR-0061 v1.1 D2 第 7、8 条）。
未验证/未知/版本不匹配组合的confirmed/readback_failed按入口处理（M3）：
- 普通 `/complete`：控制面必须照常落终态与测试结论，不得返回 422；该专项丢弃上报的正式值，按 §3.0.6 从 step trace 推导，不得记 confirmed/readback_failed。
- 迟到 confirmed：控制面不得关闭任何条目，也不得为此返回新的 4xx。
- `return-discharge` 与 init_check：控制面必须对该专项返回 422 `UNVERIFIED_PROBE_RESULT`（含 family、reason、expected/observed 组合及报告引用），
  不得关闭条目或改变归还状态；同一回报中合法专项照常处理。
错误token仍先按原409安全规则拒绝。被替换或被拒的原始值、原因与报告引用必须写入既有独立诊断审计并计数，正式job/设备/欠账事实不写入不可信值。

可信登记必须包含platform、model、权限模型、APK SHA、探针包SHA和真实验证报告；设备类别取控制面device事实，
包身份取job冻结pipeline+manifest，实际工具/权限证据取当前令牌保护的采集并持久化记录。
缺字段视为未验证，自报verified=true不生效。旧job无精确证据最多executed_unverified，不能仅因载荷声称成功关闭债务。
迟到入口仍严格原job/原token只关同源；清偿和init确认只有合法confirmed可关闭专项条目；人工放行不生成confirmed。

平台124/125等中断的优先级按 §3.0.6，必须贯穿真实PipelineEngine→pipeline_runner→complete存盘→欠账，
及无disarm_results兼容trace和未接入专项状态消费。普通failed不变，D8.4风险不扩大。
回收统一policy接线必须覆盖 §1.4列出的全部最终写入口及基础deadline投影；候选SQL和锁/CAS不可只改一边。

## 4. 验收（每组必须满足，缺一不合入）

通用门禁（与 CI 一致）：`./scripts/project_python.sh scripts/run_gates.py check:quick`；`./scripts/project_python.sh scripts/run_pytest.py tests/ -q`；受影响范围的后端测试（项目 runner）；Agent 套件 `env -i … pytest backend/agent/tests/`；涉及脚本族时 `python tools/dev/check_script_packages.py` 与 `python tools/dev/check_tool_manifest.py --base origin/main`；涉及前端时 vitest 与类型检查。

每组新增的反例测试：

| 组 | 反例测试（修复前必须红、修复后必须绿） |
|---|---|
| U1 | permit 上限 1、同 host 3 个 job 同时中止：3 个 teardown 均执行；teardown 执行中收到 abort / ABORT_LOCAL / 设备重连：子进程不被终止；manual_exit：解除被执行；停机：已开始的解除在窗口内收尾，未完成者记 `incomplete`；六值逐一映射；租约已失：不执行解除并记 `not_executed`；本轮 R1–R4 按 §3.2.1 的有限行为验收与安全证据要求执行 |
| U2 | 迁移在空库 upgrade / downgrade；带与不带 `disarm_results` 的 `/complete`（后者按推导表）；reaper：`EXECUTING_DISARM` 且进度新鲜时不回收、超上限或进度中断时回收；墙钟按 §3.3.1 验收（省略也必须拒绝、显式值原样下发、三入口与有效参数）；指标计数；告警规则 promtool 测试；v1.5 追加回收消费者、乱序 trace 与三入口错误矩阵按 §3.3.2 执行 |
| U3 | 11 处 SourceGuard；终态转换与 claim 并发由唯一索引仲裁；部分清偿不释放租约；迟到回报只关闭同源条目；重放不产生第二个条目；人工放行记 `unverified`/`override` 且写审计；带欠账退役释放租约、条目保持 `open`，取消退役同一事务重建租约；初始化两款（空闲建租约、被占只写条目）与重复执行幂等；reconciler 与紧急释放不释放欠账租约；准入原因与预览告警 |
| U4 | 心跳下发欠账 → 重连后清偿并回报令牌；过期令牌被拒；init 检查命中 → 解除 → `confirmed` 后继续；解除失败 → init 失败；控制面未下发字段 → 不动作 |
| U5 | `probe` / `disarm` 两种模式；读取诊断与类别核验按 §3.6.1/§3.8；`powercycle_finish` 1.1.0 不改 prefs；模板 pin 与 manifest 守卫；Plan 保存入口拒绝不符合接入形态的计划（§3.6.5）；旧条目退役及按树对象恢复发布（§3.6.3 方案 A）；真机验证报告 |
| U6 | 组件测试：解除结果、欠账、放行表单必填项、预览告警 |
| U7 | 文档链接与治理面门禁；写作约定自查 |

### 4.1 v1.7追加有限验收（不得以helper或静态守卫替代真入口）

- U3：§3.4.2真实CLI两设备跨事务的dry-run/apply NOWAIT；全业务零写；第二台失败不回滚第一台；keyset跨页与limit；
  claim并发冲突整事务回滚/新Session最多3次重试；无第二条ACTIVE；重复初始化幂等；历史成功覆盖、后续失败、open重算、迟到同源/错token/PENDING全部保持通过。
- U5：旧finish@1.0.8在全局ONBOARDED已登记时manual_exit和abort仍执行并记录解除；
  新disarm+finish@1.1.0的manual_exit只解除、自然结束按序二者；混合、未知、缺版本和只有收取步骤反向对照；
  模拟重启离线超过60s后上线，15s以内真实轮询戳可连续推进；600s内部预算/660s外墙到期及失租立即退出；
  probe/disarm类别矩阵保留；真实Engine汇总到runner载荷；最终包三类真机验证；四冻结包可恢复、append-only、最低版本/模板一致。
- U5（§3.6.5 保存入口）：用真实 API 在 `POST /plans`、`PUT /plans/{plan_id}`、`POST /plans/{plan_id}/append-chain-tail` 三个入口
  各提交一次「init 含 `powercycle_setup`、teardown 只有 `powercycle_finish@1.1.0`」的计划，墙钟取满足预算的值。
  当前 U5 HEAD 没有此校验，修复前必须红；修复后必须返回 422 `ONBOARDED_TEARDOWN_INVALID`、`reason=disarm_missing` 与完整 violations，
  且 Plan / PlanStep 无部分写入，`PUT` 后原步骤不变，append 后没有新增尾计划。
  拒绝对照（至少走一个真实入口）：teardown 无开关机步骤（`disarm_missing`）、解除步骤禁用（`disarm_disabled`）、
  解除版本低于最低版本（`disarm_below_min_version`）、只有旧 `powercycle_finish@1.0.x` 及 disarm 加旧 finish（`finish_below_min_version`）。
  正向对照：`powercycle_disarm` 最终版加 `finish@1.1.0` 保存成功；没有足迹步骤的只收取计划保存成功；步骤未修改的存量旧计划仍能派发。
  同一请求同时违反本节规则与墙钟预算时返回本节错误码。不得只测纯函数。
- U5（§3.6.3 方案 A）：`--publish-restored` 自检五项；在临时站点目录对三个旧版本真实重建并写入，SHA 与 manifest 一致；
  对同一目录重跑得到「同字节跳过」；改写一个已落位文件后重跑必须拒绝。三个旧条目为 `retired: true`，`check_tool_manifest.py --base origin/main` 与 `check_script_packages.py` 通过。
- U8：真实隔离PG reaper→extend-batch在abort+61s合法queue（证据经协调心跳送达）下保持RUNNING/renewed；
  同host至少11job/permit=1跨旧60s阈值（有限虚拟时钟推进到61s以上，真实策略/数据库路径，不做长时间裸sleep）；
  Q内都可尝试解除，Q外/整job上限/步骤E耗尽按incomplete交接；至少1条覆盖6600s排队后仍有660s执行窗口。
- U8：重复abort、重复seq、乱序/未来时间/旧token、重新排队、进程恢复、服务器时间回拨不得延长截止；
  queue新鲜不冒充业务进度；execute只有心跳而无PROGRESS不受保护；host失联、boot变化、失租、其它job占用、普通WAITING均有反对照。
- U8：running/patrol/trace三个最终写入口、reaper及基础投影；候选后状态转入queue/execute、进度/abort更新和已终态竞态；
  必须用独立事务最新提交复读验证，不只测纯policy。预算/新鲜度失败后真实回收仍发生。
- U8：受控wall/stall触发→真实Agent终态→控制面/欠账；兼容trace124/125与迟到STARTED；
  未接入专项incomplete→debt，普通失败→unverified对照；现有六值、取消、停机、失租不得回退。
- U8：四入口×合法验证/未验证/未知/错APK/错包/伪造verified；不合法时普通 `/complete` 返回 200、测试结论与终态照常落库、该专项按 trace 推导且无 confirmed/readback_failed，
  并对照证明 outbox 不产生死信；迟到入口不关闭条目；清偿与 init 对该专项 422 且零写、同回报合法专项照常处理；原始审计保留。
  合法confirmed可以按各自来源规则闭合，合法readback_failed保持欠账；init共用policy行为测试先交付，真实init接线由U4再验。
- U8（M1/M2）：协调心跳间隔配置为 60s 时，处于解除阶段的 job 仍至少每 30s 送达证据且保护不中断；只经 extend-batch 送达时不得单独维持保护。
  控制面不可达期间 Agent 取得 permit 后仍立即启动解除，结果经 outbox 补报；控制面以首次收到执行证据的时刻冻结 D_i。
- U4：真实init路径必须重用U8政策；没有可信组合不得放行/关闭欠账；原心跳/清偿/旧控制面无字段反对照保持。
- U6：除组件与类型检查，几何/点击/切换/表单操作按testing.md §4做真实浏览器证据；U7逐链接与最终命令事实对账。

### 4.2 命令、集成与安全证据

所有pytest必须经项目入口和内存保护runner；后端PG测试仅用隔离testcontainers，不加载生产env或连接生产DB。
实施者必须按各自范围运行：

```sh
./scripts/project_python.sh tools/dev/check_script_packages.py
./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main
./scripts/project_python.sh scripts/run_pytest.py tests/ -q
# Agent套件必须单独干净环境运行
# PATH沿用调用侧工具路径；不注入生产环境变量
 env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/ -q
# 后端套件用隔离PG；由testcontainers创建，不传生产连接串
 env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py backend/tests/ -q
./scripts/project_python.sh scripts/run_gates.py check:quick
```

包检查由U5承担，U8最终集成必须复核；模型迁移由U8做隔离空PG升降升/schema，保留唯一head；U3原迁移继续原验收。
U3/U5先按各自新增文件做定向红绿，再运行相关完整测试根；U8必须在同时含U3/U5/U8的唯一集成SHA上运行三个根、迁移和通用门禁。
实施者必须记录命令、进程最终退出状态、SHA与日志；超时/中断/pending不是通过。required六项最终HEAD必须全部SUCCESS：
lint、CodeQL、pr-typecheck、pr-compileall、pr-agent-tests、pr-migrate-empty-db。CI绿不替代真机或独立复核。

有限负向证据必须使用可逆副本与信号桩/可信等价证据；任何人不得运行 #3739 危险cancel变异或真实宿主信号。
不适用变异时必须写明原因并按batch-review.md §4提供等价事实对账，不能用主动pytest.fail冒充实现变异。

## 5. 生效（激活；本轮不执行）

0. 硬前置：Owner完成ADR v1.1裁决并引用（2026-10-17 已完成，[记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234602253)）；Accepted正文与 #3723已同步v1.7；U1–U5和U8独立复核全部通过；
   U1–U8均合入；同一最终集成SHA检查、三类最终包真机证据和required CI完成。历史U1/U2通过不代签U8。
1. 继续部署冻结。Owner另开激活授权和维护窗口，先确定暂停派发/排空旧job/能力核验/恢复步骤与回退；
   本稿不授权任何production动作。原v1.6“先新Agent后旧控制面”不能证明新协议保证，不能直接照旧执行。
2. `--publish` 发布 `powercycle_disarm@1.0.3`（实际顺延时三处同步）与 `powercycle_finish@1.1.0`；执行者必须核验最终 manifest 包 SHA。
   disarm 1.0.0/1.0.1/1.0.2 在 U5 中已改为 `retired: true`。执行者必须先按 §3.6.3 第 2 条复核零引用，
   再按 §3.6.3 第 5 条对三者各运行一次 `--publish-restored`，然后 scan（方案 A，Owner 2026-10-17 选定）。
   publish/scan仍需整批激活授权。
3. 维护窗口内按ADR修订 §4完成协调升级：先兼容控制面和nullable迁移，再Agent能力核验，全部guard协议可用后恢复派发。
   控制面/Agent交错阶段不开展B6验收，不接管旧在跑job；没有维护窗口授权时保持冻结。
4. Owner单独确认在用计划54/78/66/71/35重指最终disarm/finish版本及660/420以上有效参数预算；
   旧job继续使用实际冻结版本。重指前重新核验计划现态，历史清单不代表当前仍违规。
5. 阶段B初始化先dry-run取得最新计数，Owner另确认apply；CLI必须为 §3.4.2逐设备事务。
   原146台、48host等历史数字不得当现值。任何回退释放欠账命令也必须另经Owner授权。
6. L0/L1只读核验实际协议、版本、归还证据与统计；L2仅Owner指定独立设备，含同时中止/重启窗口、已有残留、
   欠账阻断claim/重连清偿/人工放行后init/退役取消退役；L3用最终有限红绿与独立复核。
   三类未验证前不得期待全部confirmed或提前关单。
7. 回退前停止新归还工作并处置欠账，保留guard列和原始证据，不能让旧消费者接管依赖新协议的运行job；
   实施者不得把原“release-all-debt-leases”文字当成日常释放授权。U7必须将获批的具体步骤、审计预期和停止条件写入激活清单。

## 6. 明确不做

- 休眠、Monkey、GPU、MTBF 的接入（本批只在 `FAMILY_STEPS` 中分类，不登记 `ONBOARDED`）；D6 第 2、3 条。
- #3722（展锐开关机有效性）与展锐 `Z2581` / `Z2582` 的探针验证。
- #118 结果自动收取；#2909。
- 非在用开关机计划（23/26/28/30/32/37）的重指；其它专项 teardown 的墙钟校验。
- 修改 systemd unit（`TimeoutStopSec`）。

## 7. 退回条件（实施者遇到即停，在本 issue 评论「退回：U?」）

本轮新增硬前置：#3723仍为v1.6、U8共享依赖未合入、实际版本/包无法恢复，实施者必须停止并只读准备。
§2已明列的新guard模型已于2026-10-17批准（ADR-0061 §8.2），只按 ADR-0061 v1.1 落库。清单外路径/协议/模型仍必须退回。

- 发现 §1.1、§1.2 及 v1.5 §1.4 已列范围之外的解除终止路径或 JOB 租约释放路径。§1.4 已纳入的 recycler 路径按 §3.3.2 实施，不再因同一已裁定路径重复触发停工。
- 实现需要改动准入核心算法（ADR-0026），或需要 §2 之外的数据模型。
- 开关机探针在 `MLD_LX2`、`MLD_LX3`、`MLD_NX2` 三类上全部不能通过真机验证。
- §3.0 第 6 条的兼容推导无法区分解除是否执行。
- 优雅停机在 30 秒 unit 窗口内无法让已开始的解除有界收尾。

## 8. 本次规划新发现、尚无 issue 跟踪的证据（不进入本批）

- 其它专项 teardown 的内部等待预算未登记，墙钟校验无从执行（如 `sleep_finish`、`gpu_finish`）。
- MTBF prefs 普遍读不到（盘点 373 台），MTBF 接入需要不同的探针设计。
- `powercycle_check` 未能发现停滞（#761 第 2 点，#3722 相关）。
- `.69` 上占位序列号设备 280 的 GPU 循环脚本残留未清理（[#3718 清理归档](https://github.com/DUElost/stability-test-platform/issues/3718#issuecomment-6166875518)）。
- 非在用开关机计划（23/26/28/30/32/37）的墙钟违规。

## 9. 版本修订记录

- v1.0 2026-10-14 首版（规划者：Claude Code）。基线 `origin/main@2c4906311`；Appetite 依 ADR-0061 §8。
- v1.1 2026-10-15 派单回填：创建单元 issue #3726–#3732 并回填 §3.1；写明契约文件归属与 U1 → U2 合入顺序；派单复查基线 `f1bc61990`。方案语义无变化。
- v1.2 2026-10-15 派单前补 §1.1 一行：`recovery_executor.py:408-413` ABORT_LOCAL 在 worker 存活时清令牌与本地记录，属 F1 的间接终止路径，并入 U1（§3.2 第 2 条同步）。

- v1.3 2026-10-15 U2 墙钟修正裁定：§3.3 第 4 条改为已登记启用步骤必须显式正整数墙钟；新增 §3.3.1 的跨主机语义、有限验收清单与当前双 head 集成项。保留原 Appetite、U1 → U2 合入顺序与激活闸门；方案补齐后交回原 U2 实施者，规划者不写实现。

- v1.4 2026-10-15 U1 权威复核返修裁定：新增 §3.2.1，明确重复取消的 permit 重排、SKIPPED 与足迹开始边界、确认失租的重试与回调接线；补齐五个原反例及正向对照，单列 #3739 的范围和负向证据限制。四类按既定方案内实现遗漏交回原 U1 实施者；U2 v1.3 规则、Appetite、合入顺序与激活闸门不变。

- v1.5 2026-10-16 接受 U2 权威复核退回：R1 归方案漏扫（新状态通用回收消费者），R2/R3 归实现遗漏。新增 §1.4 与 §3.3.2，纳入 recycler 全部相关 UNKNOWN 写入口、既有超时纯策略与基础活性投影；明确 trace 发生顺序与三入口错误契约及有限验收，补齐 scope 后回派原 U2。保留原 Appetite、U1 v1.4 规则、单元依赖、共享契约与激活闸门。

- v1.6 2026-10-17 U3/U5 首轮规划核验：新增 §3.4.1，列明无回报、初始化、专项状态与 open 条目一致性、真实迟到入口及 required CI 接线的原清单内实施补齐；新增 §3.6.1，修正原 U5 读取失败句缺少类别限定的规划表述，区分原始读取诊断与 D2.5 验证后的正式结果，并保留真机与最终 HEAD 闸门。无新增模型、单元、env 或激活授权，原 U1/U2 已通过结论与 U3→U4/U6/U7 依赖不变。

- v1.7 **待裁决草案** 2026-10-17：新增ADR D3.8完整模型提案与D2.5统一拒绝策略；修订 §3.0.2/5/6、§3.1、§3.4.2、§3.6.2–5、§3.8、§4、§5。
  新增U8独立单元，U3/U5保留原issue/PR。排队预算/持久化缺项与全局切换/控制面类别核验为规划缺口；
  CLI保存点替代逐设备事务、124/125与等待进度遗漏属原规则未充分落地，新增明确真入口验收；不重置原权威复核轮次。
  Owner裁决前不发布为实施规范，不授权编码、生产或PR状态操作。
  同日补齐（原规划者，回应草案复核的两处缺口，均为规划缺口）：§3.6.3 改为退役三个旧 disarm 条目，
  另按方案 A（按树对象恢复发布）或方案 B（保留缺包诊断并改部署 SOP 预期）交 Owner 裁决，删去「不退役、不发布」；
  §3.0.2 末句的保存入口拒绝规则落到 U5，新增 §3.6.5 与 §4.1 U5 真入口反例，并写明 `plans.py` 与 U3 的集成顺序。
  同日 Owner 选定 §3.6.3 方案 A（[裁决记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234224273)），正文删去方案 B 分支。
  同日 Owner 授权规划者裁决 ADR 提案 §6 五项（[记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234602253)）：五项接受，附修改 M1–M3，并入 ADR-0061 v1.1；本稿 §2、§3.0.5、§3.8、§4.1、§5、§7 同步，标题改为「已裁决，待发布」。
