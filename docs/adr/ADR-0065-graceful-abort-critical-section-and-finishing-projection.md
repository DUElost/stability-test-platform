# ADR-0065：优雅中止临界区有界保护与执行收尾过渡态投影

- 状态：Proposed v0.1（2026-11-26 起草，待 Owner 裁决与独立复核）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-11-26
- 决策者：Owner
- 标签：执行协议, 中止语义, 刷机, 设备归还, 状态投影
- 归属域：n/a（Proposed 阶段不入语义归属表；转 Accepted 后登记至 `docs/design/2026-semantic-ownership.md`，与 `device-return`、`run-terminal-semantics` 协同划界）
- 关联：
  [执行协议契约](../design/07-execution-protocol.md)（§2–§3、§7、§9.2）；
  [ADR-0043](./ADR-0043-abort-grace-subject-alignment.md)（中止宽限的请求主体同构）；
  [ADR-0048](./ADR-0048-execution-status-semantics-v2.md)（执行终态语义 v2）；
  [ADR-0061](./ADR-0061-device-return-contract.md)（设备归还契约与解除有界保护）

---

## 1. 背景与证据

### 1.1 问题现场

1. **刷机烧写临界区被直接 `abort` 砍砖（2026-11-25 PlanRun `#1679` / `job=210319`）**：
   - `P525AAN` 批量刷机期间，操作者对运行中的 PlanRun 执行 `POST /api/v1/plan-runs/{id}/abort`。
   - 控制面立即向 Agent host 下发 `command=abort`，Agent `PipelineEngine.cancel()` 仅豁免 `phase == "teardown"`（`backend/agent/pipeline_engine.py:1237`），对 `phase == "init"` 正在执行 `spd_dump` 烧写分区的进程直接发送 `SIGTERM`（`rc=-15`）。
   - 目标设备（`AUXMNX6A08000239`）分区写入被腰斩，滞留于展锐下载态（`1782:4d00`）且 USB 端点死锁，软件复位（`spd_dump reset`、`usbreset`、端口重枚举）均报 `LIBUSB_ERROR_TIMEOUT`，只能现场拆机/按键强复位后离线手刷救砖。
2. **多专项共用「受保护收尾」诉求，但目前缺少对外的过渡态投影**：
   - 开关机、休眠、GPU、Monkey、MTBF 等专项在手动中止或自然到点后，均需在 `teardown` 阶段执行设备解除与回读（例如开关机专项等待每台设备回到开机态后停止 AutoTestTool 并回读确认，Monkey 专项终止设备侧脚本并清理残留）。
   - B6（ADR-0061 v1.1）已在底层为 `teardown` 解除阶段落地了 `EXECUTING_DISARM` 与 `disarm_guard` 有界保护，但持久化层与前端展示层未设立过渡态：操作者点击中止或定时计划自然到点后，PlanRun 与 Job 在界面上仍持续显示 `RUNNING`，无法区分「仍在跑测试 / 卡死未响应中止」与「已受理中止或主循环结束，正在安全善后/归还设备」，容易诱发重复中止或人工误干预。

### 1.2 问题本质与设计约束

- **本质问题**：平台需要在「运行中（`RUNNING`）」与「终态（`COMPLETED` / `FAILED` / `ABORTED`）」之间，把两类不可粗暴中断的安全善后动作纳入统一治理：
  1. **A 类（`teardown` 后置解除与收取）**：退出 `patrol` 或主步骤后进入 `teardown`，保护解除与回读（已有 ADR-0061 §9.2 实现）；
  2. **B 类（`init` 不可中断原子临界区）**：如刷机烧写（`flash_firmware` / `flash_firmware_unisoc_bsl` 持锁写 Flash 期间），收到普通 `abort` 后应允许当前正在烧写的单台设备完成本原子步并正常复位退出，同时立即中止其余排队中的设备。
- **最小改动约束**：`JobInstance.status` 与 `PlanRun.status` 被部分唯一索引 `uq_job_active_per_device`（`PENDING|RUNNING|UNKNOWN`）、claim 门禁、reaper、聚合器与热更新门控强依赖。新增持久化枚举值会引发跨全链路迁移与锁语义扩散风险，因此本提案沿用 ADR-0061 §9.2 既定方向：**不新增 DB 持久化状态，通过步骤级临界区有界保护 + 权威过渡态投影解决。**

---

## 2. 决策

> 本节按 [ADR-0059 写作约定](../development/writing-conventions.md)书写，规则句仅使用「必须 / 不得 / 建议 / 可以」并标明执行者。

### D1 持久化状态机不变，新增权威过渡态投影

1. 实施者不得在 `PlanRun.status` 或 `JobInstance.status` 数据库持久化枚举中新增过渡状态值；活跃作业在收尾或善后期间，持久化状态必须保持 `RUNNING`。
2. 控制面必须在 PlanRun 详情/列表与 Job 详情 API 响应中提供权威过渡态投影字段（`ui_status` / `phase`），前端不得自行拼装推断逻辑。
3. 过渡态投影必须按触发来源区分以下两种语义（二选一合并口径见 §3 备选方案待 Owner 最终拍板）：
   - **`stopping`（界面名：`中止中`）**：PlanRun 或 Job 已收到有效中止请求（`abort_requested` 或 `abort_requested_hosts` 覆盖本机），但尚有活跃 Job 正在执行受保护的临界区善后或 `teardown` 解除；
   - **`finishing`（界面名：`收尾中`）**：未收到中止请求，但 Job 已离开主体测试步骤进入 `teardown` 阶段（自然到点或主步骤完成后执行后置清理、设备归还与结果收取）。
4. 当 PlanRun 下不存在处于 `init` 非临界区或 `patrol` 巡航中的活跃 Job、且至少存在 1 个处于 `stopping` 或 `finishing` 的活跃 Job 时，控制面必须将该 PlanRun 投影为对应过渡态（`stopping` 优先于 `finishing`），并同步返回剩余受保护作业计数与最近截止时间。

### D2 普通中止采用「优雅排空（Drain）」语义，并保留显式强制中止逃生阀

1. 收到不带 `force=true` 的 `POST /api/v1/plan-runs/{run_id}/abort`（含 host 级 abort）时，控制面与 Agent 必须执行以下分流：
   - 处于 `PENDING` 状态的 Job，控制面必须按现行协议立即置为 `ABORTED` 并释放租约；
   - 处于 `RUNNING` 但**未进入**受保护临界区或 `teardown` 的 Job（包括 `return_check`、固件缓存拉取、主机刷机锁排队 `lock-wait`、`patrol` 巡航、刷后 `oobe_skip`），Agent 必须立即中止当前步骤；已建立专项足迹的 Job 必须按 ADR-0061 进入 `teardown` 解除，未建立足迹的 Job 必须直接上报 `ABORTED`；
   - 处于 `RUNNING` 且**已进入**受保护临界区（D3）的 Job，Agent 不得发送 `SIGTERM` 或 `SIGKILL` 中断当前临界区子进程；Agent 必须等待该临界区自然退出（成功、失败或保护窗到期），随后跳过该步骤之后的全部非 `teardown` 步骤，按普通中止路径上报终态（`ABORTED`，或保护窗超期后的失败/强制终止结论）。
2. 控制面必须在 `POST /api/v1/plan-runs/{run_id}/abort` 上保留可选布尔参数 `force`（缺省 `false`）。
3. 当操作者显式传入 `force=true` 时，Agent 必须忽略临界区豁免并立即向子进程树发送 `SIGTERM → SIGKILL`；`force=true` 不得覆盖 ADR-0061 已生效的设备归还欠账判定，被强杀的作业仍必须如实记录未完成或失败结果。
4. 前端触发 `force=true` 前必须展示二次确认弹窗，明确告知强杀刷机临界区可能导致设备停留在下载态并需人工复位。

### D3 不可中断临界区声明与显式进入/退出标记

1. 步骤是否具备不可中断临界区保护，必须由脚本包元数据（`capabilities.json`）显式声明能力标记（如 `critical_section_guard`）与默认保护上限，不得由控制面或 Agent 按脚本名硬编码猜测。
2. 声明了 `critical_section_guard` 的脚本，在真正进入不可中断硬件操作前（例如 `flash_firmware_unisoc_bsl` 在获取主机锁并准备发送 `adb reboot autodloader` 启动 `spd_dump` 之前），必须向 `stderr` 输出结构化 `PROGRESS` 标记（`critical_section="enter"`，含阶段名与剩余预算）；在该硬件操作完成并安全复位后（例如 `spd_dump` 退出且下载口清除后），必须立即输出 `critical_section="exit"`。
3. 脚本处于 `critical_section="enter"` 之前（例如固件缓存下载、`lock-wait` 排队）或 `critical_section="exit"` 之后（例如刷后 `verify`、`oobe_skip`）时，Agent 不得将其视为不可中断临界区，收到普通 `abort` 时必须立即中止。
4. 在临界区生效期间，Agent 必须将 Job 的 `execution_state` 置为 `FINISHING_CRITICAL`（或复用统一受保护执行态并携带 `phase="critical"`，具体常量名由实施阶段对齐 `backend/agent/contracts/`），并每 15 秒推送存活与进展证据。

### D4 临界区有界保护与 Abort Reaper 协同（沿用 ADR-0043 / ADR-0061）

1. 临界区保护必须是有界的，控制面与 Agent 不得提供无上限等待。
2. 临界区保护截止时间 `D_crit` 必须取以下三者的最小值：
   - 临界区进入时刻（以控制面首次收到 `critical_section="enter"` 证据的服务器单调/墙钟时刻为准）加上脚本声明的临界区保护上限（`critical_section_max_seconds`，建议刷机类缺省 `1800s`，且不得超过步骤冻结的 `timeout_seconds`）；
   - 步骤自身的冻结墙钟截止时间；
   - PlanRun 级保护硬顶（防止重复 `abort` 延长截止，遵循 ADR-0043 D2「首次写入、后续不重置」）。
3. 在 `D_crit` 未到期、host 心跳与租约均有效、且临界区存活/进度证据新鲜（60s 窗口，同 `07-execution-protocol.md` §9.2）期间，控制面 `abort reaper` 与 `recycler` 必须推迟对该 Job 的 `UNKNOWN` 回收。
4. 一旦 `D_crit` 到期、证据失活、确认失租（`extend_lock` 返回 409）或收到 `force=true`，Agent 必须立即终止临界区子进程并上报终态，控制面必须恢复按现行无保护规则执行回收，不得借保护窗继续推迟。
5. 控制面遇到服务器时钟回拨时，必须停用临界区保护并记录诊断日志（与 §9.2 `clock_regressed` 同构）。

### D5 终态语义与版本交错兼容

1. 临界区在收到普通 `abort` 后自然完成当前烧写（即使单台烧写本身写入成功），该 Job 仍必须跳过后续步骤并按 `termination_reason="abort"` 落为 `ABORTED`（并在 `step_trace` / `metrics` 中保留 `critical_section_completed=true` 与已写入版本事实，便于运维知晓该台实际已完成刷机、无需重刷）；父 PlanRun 按 ADR-0048 聚合为 `FAILED`（因 `abort_requested=true`）。
2. 控制面与 Agent 版本交错期间：
   - 新控制面面对旧 Agent（未上报 `critical_section` 标记）时，必须退化为现行无临界区保护的 `abort` 行为，不得伪造保护窗；
   - 新 Agent 面对旧控制面（未在心跳中声明支持临界区保护能力）时，Agent 不得单方面延迟 `abort` 响应，必须按旧协议执行并记录 `critical_guard_protocol_unavailable`，防止被旧控制面 `ABORT_REAPER_GRACE_SECONDS` 提前误判为 `UNKNOWN`。

---

## 3. 备选方案与权衡

| 备选方案 | 做法 | 未采纳理由 |
|---|---|---|
| **方案 1：直接拒绝 `abort`（返回 409 Conflict）** | 只要 PlanRun 内有任意设备在烧写，整笔 `abort` 拒绝。 | 批量串行刷机时几乎时刻有 1 台在烧写，操作者无法中止其余排队设备，反而诱发人工强杀 Agent。 |
| **方案 2：在 DB `PlanRun.status` / `JobInstance.status` 新增 `STOPPING` / `FINISHING` 持久化枚举** | 修改数据库状态机与迁移，让 `RUNNING → STOPPING → ABORTED`。 | 触及 `uq_job_active_per_device`、claim、reaper、聚合器、热更新门控与前端筛选全链路，回归面极大；且与 ADR-0061 §9.2「不新增 Job 持久化状态」的既定设计原则冲突。 |
| **方案 3：合并 `stopping` 与 `finishing` 为单一过渡态名称「`收尾中`（`finishing`）」** | 不区分手动中止善后与自然到点后置收尾，统一展示「收尾中」并用副文案标明原因。 | **保留为 Owner 裁决选项**（见 §6 待裁决项 #1）：优点是状态词表更少；缺点是用户无法仅看状态徽标区分「自然跑完在收尾」与「自己点的中止已受理」。 |

---

## 4. 影响范围

- **Agent 侧**：`backend/agent/pipeline_engine.py`（`cancel()` 识别临界区、`PROGRESS` 解析 `critical_section`、超期/失租/`force` 强杀）、`backend/agent/contracts/`（共享状态与保护计算）、刷机脚本族 `capabilities.json` 与 `PROGRESS` 打点。
- **控制面侧**：`backend/services/plan_run_abort.py`（`force` 参数与下发载荷）、`backend/scheduler/device_lease_reconciler.py` 与 `recycler.py`（复用 §9.2 保护判定入口）、`backend/services/plan_run_devices.py` 与 PlanRun 列表/详情聚合（权威 `stopping` / `finishing` 投影）、`backend/models/job_instance.py`（复用 `disarm_guard` 或新增同类 JSONB 锚点列，实施期定）。
- **前端侧**：PlanRun 列表/详情与设备矩阵支持 `stopping`（中止中）/ `finishing`（收尾中）徽标、受保护剩余台数提示，以及「强制中止」二次确认入口。
- **兼容性**：严格向后兼容（D5 第 2 条），零破坏现有已发布脚本与旧版 Agent。

---

## 5. 落地与后续动作（待 Owner 裁决后按 ADR-0058 评估是否走批次）

1. **阶段 1（契约与协议）**：Owner 裁决 §6 待裁项 → 本 ADR 转 `Accepted`，同步更新 `docs/design/07-execution-protocol.md` 与 `docs/design/2026-semantic-ownership.md`。
2. **阶段 2（控制面 + 前端过渡态投影）**：先基于现有 `abort_requested` 与 B6 `EXECUTING_DISARM` / `teardown` 事实落地 `stopping` / `finishing` 投影（开关机 / Monkey / GPU / 休眠 / MTBF 立即受益）。
3. **阶段 3（临界区有界保护 + `force` 逃生阀）**：落地 Agent `critical_section` 标记、控制面有界保护与 `force=true` 接口，随后在 `flash_firmware_unisoc_bsl` 与 `flash_firmware` 新版本中接入 `critical_section_guard`。

---

## 6. 待 Owner 裁决项

1. **过渡态展示命名**：
   - **选项甲（本稿推荐）**：拆分为 **`中止中`（`stopping`）** 与 **`收尾中`（`finishing`）**，一眼区分人工中止善后与自然到点后置收尾；
   - **选项乙**：合并为单一 **`收尾中`（`finishing`）**，用副文案标明「中止收尾 / 正常收尾」。
2. **临界区烧写恰好成功时的 Job 终态口径（D5 第 1 条）**：
   - **选项甲（本稿推荐）**：仍标 `ABORTED`（保证「点了 abort 且跳过了后续 `oobe` 步骤」不误报为完整 `COMPLETED`），但在 `metrics` 显式写 `critical_section_completed=true` 与刷后版本，方便排查；
   - **选项乙**：若该 Job 所有剩余步骤均无业务必须性且烧写成功，允许标 `COMPLETED`（风险：与 Plan 中声明的 `oobe_skip` 未执行矛盾，且破坏 ADR-0048 `abort → ABORTED` 一致性）。
3. **交付形态**：本需求跨控制面状态保护、Agent 引擎与版本交错兼容，命中 ADR-0058 D7 协调激活判据，建议裁决通过后按 **ADR-0058 批次** 拆分实施与复核。

---

## 7. 关联实现与文档

- `backend/agent/pipeline_engine.py`（`PipelineEngine.cancel`、`_run_step_with_permit`）
- `backend/services/plan_run_abort.py`（`abort_plan_run`）
- `backend/scheduler/device_lease_reconciler.py`（`_reconcile_aborted_running_jobs`）
- `backend/services/device_return.py`（`job_guard_deadline`、`guard_owner_valid`）
- `docs/design/07-execution-protocol.md`（§3、§9.2）
- `docs/adr/ADR-0061-device-return-contract.md`（B6 有界解除保护先例）
