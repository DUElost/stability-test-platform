# ADR-0065：统一「收尾中（`finishing`）」过渡态模型与不可中断临界区有界保护

- 状态：Proposed v0.2（2026-11-26 v0.2：吸收 Owner 裁定——过渡态合并为单一「收尾中（`finishing`）」集合并下设 `teardown_disarm` 与 `atomic_commit` 两类子模型，重构为统一 `FinishingGuard` 抽象而非在 B6 上打补丁；单台刷机流程作为原子整体，进入临界区后收到普通中止时跑完本机闭环，成功标 `COMPLETED`、失败标 `FAILED`；v0.1：2026-11-26 初稿）
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
  [ADR-0061](./ADR-0061-device-return-contract.md)（设备归还契约与 B6 解除有界保护）

---

## 1. 背景与证据

### 1.1 问题现场

1. **刷机烧写临界区被直接 `abort` 砍砖（2026-11-25 PlanRun `#1679` / `job=210319`）**：
   - `P525AAN` 批量刷机期间，操作者对运行中的 PlanRun 执行 `POST /api/v1/plan-runs/{id}/abort`。
   - 控制面立即向 Agent host 下发 `command=abort`，Agent `PipelineEngine.cancel()` 仅豁免 `phase == "teardown"`（`backend/agent/pipeline_engine.py:1237`），对 `phase == "init"` 正在执行 `spd_dump` 烧写分区的进程直接发送 `SIGTERM`（`rc=-15`）。
   - 目标设备（`AUXMNX6A****0239`）分区写入被腰斩，滞留于展锐下载态（`1782:4d00`）且 USB 端点死锁，软件复位（`spd_dump reset`、`usbreset`、端口重枚举）均报 `LIBUSB_ERROR_TIMEOUT`，只能现场强复位后离线手刷救砖。
2. **多专项共用「受保护收尾」诉求，但缺乏统一顶层模型与对外过渡态**：
   - 开关机、休眠、GPU、Monkey、MTBF 等专项在手动中止或自然到点后，均需在 `teardown` 阶段执行设备解除与回读（例如开关机等待每台回到开机态后停止 AutoTestTool 并回读确认，Monkey 终止设备侧脚本并清理残留）。
   - B6（ADR-0061 v1.1）已针对 `teardown` 解除落地了 `EXECUTING_DISARM` 与 `disarm_guard` 有界保护，但此前只针对 A 类（后置解除）存在，既未把「收尾中」抽成对用户可见的一等过渡态，也未覆盖 B 类（刷机等 `init` 阶段的不可中断原子交付流程）。
   - 操作者点完中止或定时计划自然到点后，界面持续显示 `RUNNING`，无法区分「仍在跑主体测试 / 卡死未响应中止」与「正在安全收尾（刷完当前设备或等待设备解除归还）」。

### 1.2 问题本质：一个统一集合，两类本质不同的收尾成员

不应在 B6 `disarm_guard` 之上给刷机打特例补丁，而应把「**作业已离开普通可随时中断的主体运行期，正在执行受平台保护的收尾工作**」提升为一等概念——**`finishing`（收尾中）集合**，其下并列两类语义正交的子类型（`finishing_kind`），共用同一套有界保护与观测底座：

```text
统一过渡态集合：finishing（收尾中）   [持久化 status 仍为 RUNNING，不改 DB 终态机与活跃索引]
  │
  ├── 共享底座（统一 FinishingGuard 契约）：
  │     • 有界截止锚点（首次进入冻结、重复 abort 不重置、时钟回拨自动失效）
  │     • 双通道存活/进展取证（60s 新鲜度 + host/lease/token/boot 所有权校验）
  │     • 统一 reaper / recycler 推迟回收入口（同一策略函数，消除双套 if/else）
  │     • 统一权威投影：ui_status="finishing" + finishing_kind + finishing_trigger + 剩余计数/截止时间
  │
  ├── 子类型 A：teardown_disarm（后置解除收尾）
  │     • 场景：开关机、休眠、GPU、Monkey、MTBF（手动中止退出 patrol 或自然到点进入 teardown）
  │     • 保护对象：设备足迹解除、开机回读与自然到点时的结果收取
  │     • 终态口径：沿用 ADR-0061——测试终态与解除结果正交（中止→ABORTED，自然到点→COMPLETED），解除失败转设备欠账
  │     • 与 B6 关系：自然收编现有 disarm_guard / EXECUTING_DISARM，保持线协议向后兼容
  │
  └── 子类型 B：atomic_commit（原子流程收尾）
        • 场景：展锐 BSL 刷机、MTK 刷机等不可中断硬件交付流程（收到普通 abort 时已进入烧写临界区）
        • 保护对象：当前单台设备的完整交付闭环（当前临界区步骤 + 同机后续善后步骤，如刷后 verify / oobe_skip / check_device）
        • 终态口径：单台整机流程作为整体——整机完整跑通→COMPLETED；任一步失败或保护窗超期→FAILED；显式 force 强杀→ABORTED
        • 排队隔离：未进入临界区的同机或跨机排队作业（PENDING / return_check / 缓存拉取 / lock-wait）不进入本集合，立即中止为 ABORTED
```

### 1.3 最小持久化约束

`JobInstance.status` 与 `PlanRun.status` 被部分唯一索引 `uq_job_active_per_device`（`PENDING|RUNNING|UNKNOWN`）、claim 门禁、reaper、聚合器与热更新门控强依赖。因此本提案沿用 ADR-0061 §9.2 的持久化边界：**不在数据库 `status` 枚举中新增状态值，持久化层维持 `RUNNING`，由控制面统一投影 `finishing` 集合及子类型明细。**

---

## 2. 决策

> 本节按 [ADR-0059 写作约定](../development/writing-conventions.md)书写，规则句仅使用「必须 / 不得 / 建议 / 可以」并标明执行者。

### D1 统一「收尾中（`finishing`）」过渡态集合与双子类型模型

1. 实施者不得在 `PlanRun.status` 或 `JobInstance.status` 数据库持久化枚举中新增过渡状态值；处于收尾中的活跃 Job 持久化状态必须保持 `RUNNING`。
2. 控制面必须在 PlanRun 列表/详情与 Job 详情 API 响应中提供统一的权威过渡态投影 `ui_status="finishing"`（界面统一展示名：**`收尾中`**），前端不得自行推断过渡态。
3. 每一个处于 `finishing` 集合内的活跃 Job，控制面必须同时投影以下两个正交维度：
   - `finishing_kind`（收尾子类型）：
     - `teardown_disarm`（后置解除收尾，A 类）：Job 正在执行 `teardown` 阶段的设备解除、回读或自然到点后的结果收取；
     - `atomic_commit`（原子流程收尾，B 类）：Job 收到普通中止时已进入不可中断临界区，正在完成当前单台设备的原子交付闭环；
   - `finishing_trigger`（触发来源）：`manual_abort`（人工或主机级中止触发）或 `natural_end`（定时巡航自然到点或主体步骤自然结束触发）。
4. 当 PlanRun 下不存在处于普通可中断运行态（非临界区 `init` 或 `patrol` 巡航）的活跃 Job、且至少存在 1 个处于 `finishing` 的活跃 Job 时，控制面必须将该 PlanRun 投影为 `ui_status="finishing"`，并按 `finishing_kind` 返回分组剩余计数（例如 `atomic_commit` 剩余台数、`teardown_disarm` 剩余台数）与最近保护截止时间。
5. 当 PlanRun 同时存在处于 `finishing` 的活跃 Job 与仍在普通运行态的活跃 Job 时，控制面必须保持 PlanRun 级 `ui_status="running"`，但在明细计数与设备矩阵中逐 Job 展示各自 `finishing` 状态与子类型。

### D2 普通中止分流与单台原子流程整体善后

1. 收到不带 `force=true` 的 `POST /api/v1/plan-runs/{run_id}/abort`（含 host 级 abort）时，控制面与 Agent 必须按以下规则分流：
   - 处于 `PENDING` 状态的 Job，控制面必须立即置为 `ABORTED` 并释放租约；
   - 处于 `RUNNING` 且**未进入**不可中断临界区或 `teardown` 的 Job（包括 `return_check`、固件缓存拉取、主机刷机锁排队 `lock-wait`、`patrol` 巡航），Agent 必须立即中止当前步骤；已建立专项足迹的 Job 必须进入 `teardown_disarm` 子类型执行解除（ADR-0061），未建立足迹的 Job 必须直接上报 `ABORTED`；
   - 处于 `RUNNING` 且**已进入**不可中断临界区（D3）的 Job，Agent 不得发送 `SIGTERM` 或 `SIGKILL` 中断当前进程；Agent 必须将该 Job 转入 `atomic_commit` 子类型，完整执行当前临界区步骤以及该 Job 所属原子交付闭环内的后续步骤（例如刷机后的 `oobe_skip`、`check_device`、`ensure_root`），直至本机闭环自然结束或保护窗到期。
2. 控制面必须在 `POST /api/v1/plan-runs/{run_id}/abort` 上提供可选布尔参数 `force`（缺省 `false`）。
3. 当操作者显式传入 `force=true` 时，Agent 必须跳过 `atomic_commit` 保护并立即向子进程树发送 `SIGTERM → SIGKILL`；对于已建立设备足迹的 `teardown_disarm` 作业，`force=true` 不得豁免 ADR-0061 的设备归还欠账判定，被中断的解除必须记为 `incomplete` 并按 §9.5 转为欠账租约。
4. 前端在触发 `force=true` 前必须展示二次确认弹窗，明确提示强杀刷机临界区可能导致设备滞留下载态并需人工复位。

### D3 统一 `FinishingGuard` 底座与临界区显式声明契约

1. 实施者必须将控制面与 Agent 的收尾保护抽象为统一的 `FinishingGuard` 契约（置于 `backend/agent/contracts/`），以 `kind ∈ {"teardown_disarm", "atomic_commit"}` 区分两类成员，不得将 B 类逻辑以特例分支散落拼接在 `disarm_guard` 专用代码中。
2. B6（ADR-0061 §9.2）既有 `disarm_guard` 与 `EXECUTING_DISARM` 语义必须作为 `FinishingGuard(kind="teardown_disarm")` 的现行实现自然并入统一底座，现有字段与线协议在过渡期内必须保持向后兼容。
3. 具备不可中断临界区的步骤必须在脚本包元数据（`capabilities.json`）中显式声明能力标记 `critical_section_guard` 与默认临界区预算上限，控制面与 Agent 不得按脚本名硬编码猜测。
4. 声明了 `critical_section_guard` 的脚本，在真正进入不可中断硬件操作前（例如 `flash_firmware_unisoc_bsl` 获取主机锁并准备发送 `adb reboot autodloader` 启动 `spd_dump` 之前），必须向 `stderr` 输出结构化 `PROGRESS` 标记 `critical_section="enter"`；在该硬件操作完成并安全退出后，必须输出 `critical_section="exit"`。
5. 一旦某 Job 的步骤输出了 `critical_section="enter"`，Agent 必须将本 Job 标记为已进入原子交付闭环；若此后收到普通 `abort`，Agent 必须放行本步骤及原子交付闭环内的剩余步骤（默认覆盖同一 Plan `init` 阶段内排在该步骤之后的步骤，具体是否支持步骤级显式退出闭环由实施前 §6 细化），并在进入 `atomic_commit` 收尾期间持续上报 `FINISHING_ATOMIC`（或统一 `FINISHING` 执行态加 `phase="atomic_commit"`）与进展证据。
6. 脚本处于 `critical_section="enter"` 之前（例如固件缓存拉取或 `lock-wait` 排队）时，Agent 不得将其纳入 `atomic_commit` 保护，收到普通 `abort` 时必须立即中止。

### D4 统一有界保护与 Abort Reaper / Recycler 协同

1. `teardown_disarm` 与 `atomic_commit` 两类收尾都必须受有界保护窗约束，控制面与 Agent 不得提供无上限等待。
2. 控制面 `abort reaper`（`device_lease_reconciler`）与 `recycler` 必须调用同一个 `FinishingGuard` 判定入口决定是否推迟 `UNKNOWN` 回收；两类子类型共用以下硬约束（继承 ADR-0043 与 ADR-0061 §9.2）：
   - 首次冻结截止时间后，控制面与 Agent 不得因重复 `abort`、乱序回报或服务重启延长截止；
   - `teardown_disarm` 的预算计算必须完全沿用 `07-execution-protocol.md` §9.2（`Q=7200s`、各解除步 `E_i`、绝对上限 `86400s`）；
   - `atomic_commit` 的保护截止时间 `D_atomic` 必须取以下三者的最小值：① 控制面首次收到 `atomic_commit` 证据（或 `critical_section="enter"`）时刻加上临界区步及原子尾段各步冻结 `timeout_seconds` 之和；② 原子交付硬顶（建议缺省 `3600s`）；③ PlanRun 级剩余墙钟上限；
   - 控制面仅在 host 心跳、租约有效期、fencing token、boot_id 与设备占用均有效且 60s 存活/进展证据新鲜时推迟回收；
   - 遇到服务器时钟回拨时，控制面必须停用保护并记录 `clock_regressed`。
3. 任一截止到达、确认失租（`extend_lock` 返回 409）或收到 `force=true` 时，Agent 必须立即停止收尾并上报终态，控制面必须恢复按现行无保护规则执行回收。

### D5 按子类型区分终态口径与版本交错兼容

1. 两类收尾子类型的 Job 终态必须按各自本质判定，不得混用：
   - **`teardown_disarm`（A 类）**：严格保持 ADR-0061 契约——Job 终态由主体测试的 `termination_reason` 决定（手动中止落 `ABORTED`，自然到点落 `COMPLETED`），与解除成败正交；解除失败通过 `disarm_results` 转为设备欠账；
   - **`atomic_commit`（B 类）**：将当前单台设备的原子交付流程视为一个整体——若收到普通 `abort` 后本机原子闭环全部步骤执行成功，Agent 必须上报 `COMPLETED`（并在终态元数据中标记 `completed_during_abort_drain=true`）；若闭环内任一步骤失败或保护窗超期，Agent 必须上报 `FAILED`；仅当收到 `force=true` 强杀或未进入临界区即被中止时，Agent 才必须上报 `ABORTED`。
2. 父 PlanRun 的终态聚合必须保持 ADR-0048 不变：只要 PlanRun 存在有效 `abort_requested`（或 `aborted > 0`），全部 Job 落终态后父 PlanRun 仍必须聚合为 `FAILED`；其中已在 `atomic_commit` 闭环中成功完成的单台设备以 `COMPLETED` 计入成功数，便于运维准确区分「本台已刷好」与「本台未刷/被中止」。
3. 控制面与 Agent 版本交错期间：
   - 新控制面面对旧 Agent（未上报 `atomic_commit` / `critical_section` 协议能力）时，必须退化为现行无临界区保护的 `abort` 行为，不得伪造保护窗；
   - 新 Agent 面对旧控制面（心跳未声明支持 `finishing_guard_v2` / `atomic_commit` 保护）时，Agent 不得单方面延迟 `abort` 响应，必须按现行协议立即中止并记录 `atomic_guard_protocol_unavailable`，避免被旧控制面 `ABORT_REAPER_GRACE_SECONDS` 提前误判为 `UNKNOWN`。

---

## 3. 备选方案与权衡

| 备选方案 | 做法 | 未采纳理由 |
|---|---|---|
| **方案 1：拆分为两个对外过渡态名称（`中止中` + `收尾中`）** | 人工中止显示「中止中」，自然到点显示「收尾中」。 | Owner 裁定合并为单一顶层概念 **`收尾中`（`finishing`）**，通过子类型（`teardown_disarm` / `atomic_commit`）与触发源字段表达差异，避免界面与 API 状态词表碎片化。 |
| **方案 2：在 `disarm_guard` 上直接加 `if is_flash` 特例补丁** | 保持 `disarm_guard` 只面向 B6，把刷机硬塞进解除逻辑里。 | A 类（后置解除，终态正交、失败转欠账）与 B 类（原子交付闭环，整体成功标 `COMPLETED`、失败标 `FAILED`）语义本质不同，硬塞会导致代码和契约持续腐化；抽成统一 `FinishingGuard` 集合 + 两类子模型才能一劳永逸。 |
| **方案 3：临界区烧写成功后跳过 `oobe` 并把 Job 标 `ABORTED`** | 烧写完立刻跳后续步骤并强制标 `ABORTED`。 | 会留下「已刷机但未过 OOBE」的半成品设备，且运维无法从 Job 终态直接看出该机已完成交付；Owner 裁定单台刷机流程按整体执行并按整体成败落 `COMPLETED` / `FAILED`。 |
| **方案 4：在 DB `status` 枚举新增 `FINISHING` 持久化状态** | 改数据库状态机与部分唯一索引。 | 触及 `uq_job_active_per_device`、claim、reaper、聚合器、热更新全链路，迁移与回归风险远高于权威投影方案。 |

---

## 4. 影响范围

- **共享契约层（`backend/agent/contracts/`）**：新增统一 `finishing_guard` 契约模块（定义 `FinishingKind`、`FinishingTrigger`、统一截止与新鲜度计算，向后兼容既有 `device_return` / `disarm_guard` 载荷）。
- **Agent 侧**：`backend/agent/pipeline_engine.py`（`cancel()` 与步骤执行循环按 `FinishingGuard` 统一处理 `teardown_disarm` 与 `atomic_commit`，解析 `critical_section="enter"/"exit"`，保留 `force` 强杀）、刷机脚本族（`flash_firmware_unisoc_bsl`、`flash_firmware`）声明 `critical_section_guard` 并补打点。
- **控制面侧**：`backend/services/plan_run_abort.py`（新增 `force` 参数与载荷）、`backend/scheduler/device_lease_reconciler.py` 与 `recycler.py`（收敛到统一 `FinishingGuard` 判定入口）、`backend/services/plan_run_devices.py` 与 PlanRun 列表/详情聚合（权威投影 `ui_status="finishing"` 与子类型明细）、`backend/models/job_instance.py`（将 `disarm_guard` 泛化或并列 `finishing_guard` 持久化锚点，实施阶段定迁移方案）。
- **前端侧**：PlanRun 列表/详情与设备矩阵统一展示「`收尾中`」徽标、按 `atomic_commit` / `teardown_disarm` 展示收尾明细与倒计时，以及「强制中止」二次确认弹窗。
- **兼容性**：严格向后兼容（D3 第 2 条、D5 第 3 条），不破坏现行 B6 归还链路与旧版 Agent。

---

## 5. 落地路径（建议按 ADR-0058 批次交付）

1. **阶段 1（ADR 定稿与语义登记）**：闭合 §6 实施前确认项并完成独立复核 → 本 ADR 转 `Accepted`，同步更新 `docs/design/07-execution-protocol.md` 与 `docs/design/2026-semantic-ownership.md`。
2. **阶段 2（统一底座 + A 类投影先行）**：落地 `backend/agent/contracts/` 统一 `FinishingGuard` 接口，收编现有 B6 `disarm_guard` 为 `teardown_disarm`，上线控制面与前端 `ui_status="finishing"` 投影（开关机 / Monkey / GPU / 休眠 / MTBF 立即获得「收尾中」可视性）。
3. **阶段 3（B 类 `atomic_commit` + `force` 逃生阀 + 刷机接入）**：落地控制面/Agent `atomic_commit` 保护与 `force=true` 接口，随后在 `flash_firmware_unisoc_bsl` 与 `flash_firmware` 新版本接入 `critical_section_guard` 与进出打点。

---

## 6. 裁决记录与剩余待确认项

### 已裁决（2026-11-26，Owner）
1. **过渡态名称**：合并为单一 **`收尾中`（`finishing`）**，作为统一集合概念纳入模型，下设 `teardown_disarm`（A 类）与 `atomic_commit`（B 类）两个正交子类型，不在 B6 上打补丁。
2. **单台刷机流程整体语义与终态口径**：将单台设备的刷机流程视为一个整体；进入临界区后收到普通 `abort` 时等待本机完整闭环结束，整体成功标 `COMPLETED`，整体失败标 `FAILED`。

### 剩余待确认项（实施拆分前拍板）
1. **原子闭环尾段边界的声明方式（D3 第 5 条）**：
   - **选项甲（本稿默认）**：脚本输出 `critical_section="enter"` 后，默认将该步骤以及同 Plan `init` 阶段内排在其后的全部步骤视为本机原子交付闭环（对现行刷机 Plan `check_device → flash → oobe_skip` 零配置即生效，且若后续无 `patrol` 则自然闭环）；
   - **选项乙**：要求在 Plan 步骤或模板上显式标注 `atomic_group` 才纳入尾段（优点是显式可控；缺点是存量刷机 Plan 须逐一改配置才生效）。
2. **批次交付确认**：本需求跨控制面保护策略、Agent 引擎、共享契约与版本交错兼容，命中 ADR-0058 D7「协调激活」强判据，建议独立复核通过并转 `Accepted` 后按 **ADR-0058 批次** 拆分实施。

---

## 7. 关联实现与文档

- `backend/agent/pipeline_engine.py`（`PipelineEngine.cancel`、`_run_step_with_permit`、`_begin_disarm_tail`）
- `backend/services/plan_run_abort.py`（`abort_plan_run`）
- `backend/scheduler/device_lease_reconciler.py`（`_reconcile_aborted_running_jobs`）
- `backend/services/device_return.py`（`job_guard_deadline`、`guard_owner_valid`）
- `docs/design/07-execution-protocol.md`（§3、§9.2）
- `docs/adr/ADR-0061-device-return-contract.md`（B6 有界解除保护基线）
