# ADR-0065：统一「收尾中（`finishing`）」过渡态模型与不可中断临界区有界保护

- 状态：Proposed v0.7（2026-11-26 v0.7：按五轮独立复核修订——在 `activate_permit` 行锁事务内补齐当前 `ACTIVE JOB` 租约未过期与同 Job/设备/worker 归属复核并返回租约有效期，Agent 本地放行前同步核验租约未过期余量，彻底闭合过期租约写入 `granted` 的缺口；将停止证明扩展至覆盖同一 Job/attempt 实际持有硬件的全部进程（含 MTK `flash_firmware` 以 `start_new_session=True` 独立会话启动的工具进程组，经尝试专属 cgroup 或全受追踪可信 PGID 集核验），并限定宿主重启替代证明仅认读自 `/proc/sys/kernel/random/boot_id` 的 `kernel_proc` 真值、排除随机回退 `boot_id`；在 D5.7 回滚门禁中明确 SQL 欠账核验谓词采用 `IS NOT TRUE` 同时计入 `false` 与 `NULL`；拆净 L135、L152、L154、L212 等剩余 ADR-0059 复合句并同步 PR 正文文档自查项；v0.6：2026-11-26 四轮复核修订；v0.5：2026-11-26 三轮复核修订；v0.4：2026-11-26 二轮复核修订；v0.3：2026-11-26 首轮复核修订；v0.2：2026-11-26 吸收 Owner 裁定；v0.1：2026-11-26 初稿）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-11-26
- 决策者：Owner
- 标签：执行协议, 中止语义, 刷机, 设备归还, 状态投影
- 归属域：n/a（Proposed 阶段不入语义归属表；转 Accepted 后登记至 `docs/design/2026-semantic-ownership.md`，与 `device-return`、`run-terminal-semantics` 协同划界）
- 关联：
  [执行协议契约](../design/07-execution-protocol.md)（§2–§3、§5、§7、§9.2、§9.5）；
  [列表分页与 Minimap 契约](../design/2026-11-24-list-pagination-and-facets-contract.md)（§5.1）；
  [ADR-0043](./ADR-0043-abort-grace-subject-alignment.md)（中止宽限的请求主体同构）；
  [ADR-0048](./ADR-0048-execution-status-semantics-v2.md)（执行终态语义 v2）；
  [ADR-0058](./ADR-0058-planned-batch-execution.md)（批次交付流程 D7–D9）；
  [ADR-0061](./ADR-0061-device-return-contract.md)（设备归还契约与 B6 解除有界保护）；
  [ADR-0063](./ADR-0063-vbus-cycle-flash-without-adb.md)（VBUS 切电刷机 D5 第 3 条刷后残留检查）

---

## 1. 背景与证据

### 1.1 问题现场

1. **刷机烧写临界区被直接 `abort` 砍砖（2026-11-25 PlanRun `#1679` / `job=210319`）**：
   - `P525AAN` 批量刷机期间，操作者对运行中的 PlanRun 执行 `POST /api/v1/plan-runs/{id}/abort`。
   - 控制面立即向 Agent host 下发 `command=abort`，Agent `PipelineEngine.cancel()` 仅豁免 `phase == "teardown"`（`backend/agent/pipeline_engine.py:1237`），对 `phase == "init"` 正在执行 `spd_dump` 烧写分区的进程直接发送 `SIGTERM`（`rc=-15`）。
   - 目标设备（`AUXMNX6A****0239`）分区写入被腰斩，滞留于展锐下载态（`1782:4d00`）且 USB 端点死锁，软件复位（`spd_dump reset`、`usbreset`、端口重枚举）均报 `LIBUSB_ERROR_TIMEOUT`，只能现场强复位后离线手刷救砖。
2. **多专项共用「受保护收尾」诉求，但缺乏统一顶层模型与对外过渡态**：
   - 开关机、休眠、GPU、Monkey、MTBF 等专项在手动中止、手动退出、主体失败、barrier 超时或自然到点后，均需在 `teardown` 阶段执行设备解除与回读（或仅收取结果）。
   - B6（ADR-0061 v1.1 与 `执行协议契约（07-execution-protocol.md）§9.2`）已针对 `teardown` 解除落地 `EXECUTING_DISARM` 与 `disarm_guard` 有界保护，但此前只针对 A 类（后置解除）存在，既未把「收尾中」抽成对用户可见的一等过渡态，也未覆盖 B 类（刷机等 `init` 阶段的不可中断原子交付流程）。
   - 操作者点完中止或定时计划自然到点后，界面持续显示 `RUNNING`，无法区分「仍在跑主体测试 / 卡死未响应中止」与「正在安全收尾（刷完当前设备或等待设备解除归还）」。

### 1.2 问题本质：一个统一集合，两类本质不同的收尾成员

不在 B6 `disarm_guard` 之上给刷机打特例补丁，而把「**作业已离开普通可随时中断的主体运行期，正在执行受平台保护的收尾工作**」提升为一等概念——**`finishing`（收尾中）集合**，其下并列两类语义正交的子类型（`finishing_kind`），共用同一套有界保护与观测底座：

```text
统一过渡态集合：finishing（收尾中）   [持久化 status 仍为 RUNNING，不改 DB 终态机与活跃索引]
  │
  ├── 共享底座（统一 FinishingGuard 契约）：
  │     • 进入前两段同步握手（预登记 JobInstance FOR UPDATE → PlanRun FOR NO KEY UPDATE → 准入门禁行 FOR SHARE；
  │       activate_permit 与 recycler 行锁互斥，复核未过期 ACTIVE JOB 租约后确立 granted，再由 Agent 在租约余量内投递 grant）
  │     • 同量纲绝对截止锚点（服务端时钟冻结、当前步与全组双预算准入、时钟回拨持久失效）
  │     • 存活心跳与真实新进展分离核验（按控制面接收时刻计算，兼容静默写分区窗口）
  │     • 统一 reaper / recycler 候选与最终行锁 CAS 复读入口（多主体时钟取早、强度取高）
  │     • 统一权威投影：ui_status="finishing"（Minimap 复用 7 字段 job_exec_status="finishing"）+ 分页表/Job 详情子类型明细
  │
  ├── 子类型 A：teardown_disarm（后置解除收尾）
  │     • 场景：开关机、休眠、GPU、Monkey、MTBF（手动中止、手动退出、主体失败、barrier 超时或自然到点进入 teardown）
  │     • 保护对象：设备足迹解除、开机回读与自然到点时的结果收取（解除资格按冻结版本与设备足迹确定）
  │     • 终态口径：沿用 ADR-0061——测试终态与解除结果正交（中止→ABORTED，自然到点→COMPLETED），欠账由统一归还入口裁定
  │     • 与 B6 关系：自然收编现有 disarm_guard / EXECUTING_DISARM，保持线协议向后兼容
  │
  └── 子类型 B：atomic_commit（原子流程收尾）
        • 场景：展锐 BSL 刷机、MTK 刷机等不可中断硬件交付流程（收到普通 abort 时已获批进入冻结 atomic_group）
        • 保护对象：两层冻结——plan_snapshot 静态声明全必需成员 + 预登记校验持久化 VBUS 展开 return_check 至 JobInstance.finishing_guard
        • 终态口径：纯原子交付 Job 全成员实证成功→COMPLETED；任一成员失败、超期或无证据跳过→FAILED；
                   混合 Plan（含未执行 patrol）完成本机原子闭环后跳过 patrol→ABORTED；显式 force 强杀→ABORTED
        • 完整性义务与全进程树停止门禁：预登记持久化 flash_integrity 待核验义务；按阶段判定核销与保留，终态统一交由同步/异步归还入口检查全设备欠账；
                   停止证明覆盖全部硬件侧进程（含独立会话子工具，经尝试专属 cgroup 或全受追踪可信 PGID 集核验），宿主重启替代证明仅认 kernel_proc boot_id
        • 排队隔离：未获批进入临界区的作业（PENDING / return_check / 缓存拉取 / lock-wait）不进入本集合，立即中止为 ABORTED
```

### 1.3 最小持久化约束

`JobInstance.status` 与 `PlanRun.status` 被部分唯一索引 `uq_job_active_per_device`（`PENDING|RUNNING|UNKNOWN`）、claim 门禁、reaper、聚合器与热更新门控强依赖。因此本提案沿用 `执行协议契约（07-execution-protocol.md）§9.2` 的持久化边界：**不在数据库 `status` 枚举中新增状态值，持久化层维持 `RUNNING`，由控制面统一投影 `finishing` 集合及子类型明细。**

---

## 2. 决策

> 本节按 [ADR-0059 写作约定](../development/writing-conventions.md)书写。每条规则独立成句，标明执行者，且仅使用「必须 / 不得 / 建议 / 可以」表达规范强度。

### D1 统一「收尾中（`finishing`）」过渡态集合、双子类型与全量投影口径

1. 实施者不得在 `PlanRun.status` 或 `JobInstance.status` 数据库持久化枚举中新增过渡状态值。
2. 控制面必须将处于收尾中的活跃 Job 持久化状态保持为 `RUNNING`。
3. 控制面必须在 PlanRun 列表、PlanRun 详情、设备分页表与 Job 详情响应中提供统一的权威过渡态投影字段 `ui_status="finishing"`（界面统一展示名：**`收尾中`**）。
4. 实施者必须在保持 `列表分页与 Minimap 契约（2026-11-24-list-pagination-and-facets-contract.md）§5.1` 既有七字段轻量结构不变的前提下，在 Minimap 端点中扩展 `job_exec_status` 与 `facets.by_status` 词表以包含 `"finishing"`（与 `ui_status` 共用同一服务端判定谓词），并在同一实施批次修订该契约 §5.1。
5. 前端不得根据零散时间字段或执行日志自行推断 `finishing` 过渡态。
6. 对每一个投影为 `ui_status="finishing"` 的活跃 Job，控制面必须在设备分页表与 Job 详情响应中同时投影正交字段 `finishing_kind`（取值为 `teardown_disarm` 或 `atomic_commit`）与 `finishing_trigger`（取值为 `manual_abort`、`manual_exit`、`natural_end`、`body_failure` 或 `barrier_timeout`）。
7. 控制面必须按以下全量规则计算单 Job 级 `ui_status`（及 Minimap 对应 `job_exec_status`）：
   - 对处于 `PENDING` 的 Job，控制面必须投影为 `"pending"`，且不得将其纳入 `finishing`；
   - 对处于 `UNKNOWN` 的 Job，控制面必须投影为 `"unknown"`，且不得将其纳入 `finishing`；
   - 对处于 `RUNNING` 且已获批进入硬件临界区、但尚未收到普通 `abort` 的刷机 Job，控制面必须投影为 `"running"`，且不得提前投影为 `"finishing"`；
   - 对处于 `RUNNING` 且正在执行 `teardown` 解除排队（`WAITING_EXECUTION_SLOT`）、执行解除步骤（`EXECUTING_DISARM`）或仅收取结果的 `teardown` 阶段 Job，控制面必须投影 `ui_status="finishing"` 与 `finishing_kind="teardown_disarm"`；
   - 对处于 `RUNNING` 且已获批进入冻结 `atomic_group` 并在收到普通 `abort` 后正在完成本机闭环的 Job，控制面必须投影 `ui_status="finishing"` 与 `finishing_kind="atomic_commit"`；
   - 对处于 `RUNNING` 且已获批进入硬件临界区（无论是否已收到 `abort`）的 Job，只要主机心跳与保护存活证据新鲜，控制面不得因刷机期间预期的 ADB 断连将其 `ui_status` 或 `job_exec_status` 降级为 `"unknown"`。
8. 当处于 `finishing` 的 Job 属于仅收取结果且无有效保护截止的 `teardown` 场景时，控制面必须返回 `finishing_deadline_at=null`。
9. 当 PlanRun 下至少存在 1 个投影为 `ui_status="finishing"` 的活跃 Job，且不存在 `PENDING`、`UNKNOWN` 或普通可中断 `RUNNING`（非临界区 `init`、未中止的临界区 `init`、`patrol`、`backoff`）Job 时，控制面必须将该 PlanRun 投影为 `ui_status="finishing"`。
10. 当 PlanRun 被投影为 `ui_status="finishing"` 时，控制面必须按 `finishing_kind` 返回分组剩余计数（`atomic_commit` 剩余台数与 `teardown_disarm` 剩余台数），并仅对存在非空 `finishing_deadline_at` 的活跃 Job 聚合最近保护截止时间（若全部为 `null` 则返回 `null`）。
11. 当 PlanRun 同时存在 `ui_status="finishing"` 的活跃 Job 与其他未终态活跃 Job（`PENDING`、`UNKNOWN` 或普通 `RUNNING`）时，控制面必须保持 PlanRun 级 `ui_status="running"`。
12. 在上述混合活跃状态下，控制面必须在 PlanRun 明细计数中返回各 `finishing_kind` 分组计数。
13. 在上述混合活跃状态下，控制面必须在 Minimap 中通过 `job_exec_status` 逐 Job 返回 `"finishing"`（不含子类型字段）。
14. 在上述混合活跃状态下，控制面必须在设备分页表与 Job 详情响应中逐 Job 返回 `ui_status`、`finishing_kind` 与 `finishing_trigger`。
15. 控制面必须在 PlanRun 列表查询（`plan_run_catalog`）中支持按投影态筛选 `finishing`。
16. 控制面在活跃 PlanRun 统计中必须继续按持久化 `RUNNING` 计入处于 `finishing` 的 PlanRun。
17. 前端在 PlanRun 列表、设备分页表与 Job 详情页中必须消费控制面投影的 `ui_status`（及设备分页表与 Job 详情页中的 `finishing_kind` 与 `finishing_trigger`）。
18. 前端在设备矩阵 Minimap 中必须消费控制面投影的七字段 `job_exec_status`，不得要求 Minimap 返回子类型字段。
19. 前端在 PlanRun 列表、Minimap、设备分页表与 Job 详情页之间必须保持统一的轮询与推送刷新口径。
20. 实施者必须使 A 类与 B 类共用 `FinishingGuard` 围栏、预算与证据判定底座。
21. 控制面与 Agent 必须继续按冻结脚本版本与已建立设备足迹判定 `teardown_disarm` 的解除资格（遵循 ADR-0061）。

### D2 普通中止分流、临界区仲裁与 `force=true` 紧急强杀

1. 收到不带 `force=true` 的 PlanRun 级或主机级 `POST /api/v1/plan-runs/{run_id}/abort` 时，控制面必须对命中主体内的 `PENDING` Job 立即置为 `ABORTED` 并释放本 Job 的 `JOB` 租约。
2. 收到不带 `force=true` 的普通中止时，对处于 `RUNNING` 且未获批进入不可中断临界区、也未进入 `teardown` 的 Job（包括 `return_check`、固件缓存拉取、主机刷机锁排队 `lock-wait`、`patrol` 巡航），Agent 必须立即中止当前步骤。
3. 上述步骤被立即中止后，若本 Job 按 ADR-0061 已建立专项设备足迹，Agent 必须将其转入 `teardown_disarm` 子类型执行解除。
4. 上述步骤被立即中止后，若本 Job 未建立任何专项设备足迹，Agent 必须直接上报终态 `ABORTED`。
5. 收到不带 `force=true` 的普通中止时，对处于 `RUNNING` 且已获批进入冻结 `atomic_group`（无论当前处于硬件临界区内，或处于硬件 `exit` 之后且尚未完成本机 `atomic_group` 剩余成员步骤）的 Job，Agent 不得向当前进程树发送 `SIGTERM` 或 `SIGKILL`。
6. 对上述已获批进入 `atomic_group` 的 Job，Agent 必须将其转入 `atomic_commit` 子类型并顺序执行完本机冻结 `atomic_group` 的全部剩余成员步骤，直至闭环结束、保护窗到期、确认失租或收到 `force=true`。
7. 控制面必须在 `POST /api/v1/plan-runs/{run_id}/abort`（含主机级作用域）上提供可选布尔参数 `force`（缺省 `false`）。
8. 控制面必须在 `PlanRun.run_context` 中按请求主体持久化 `force` 升级状态（run 级记于 `abort_requested`，主机级记于 `abort_requested_hosts[host_id]`）。
9. 控制面不得将已升级为 `force=true` 的主体中止强度降级回普通中止。
10. 当全 Run 级与主机级两个中止主体同时覆盖同一 Job 时，控制面与 Agent 必须在所有适用主体上取最高强度（任一适用主体为 `force=true` 即按 `force=true` 执行），不得因较早时钟主体为普通中止而降级。
11. 控制面在筛选中止候选 Job 时必须同时纳入 `RUNNING` 与 `UNKNOWN` 状态的 Job。
12. 控制面在向 Agent 下发或补送 SocketIO `abort` 载荷时必须在载荷中显式携带按第 10 条计算的有效 `force` 布尔字段。
13. 当操作者传入 `force=true` 时，Agent 必须跳过 `atomic_commit` 保护并立即向本尝试受约束的全部硬件侧进程集合发送 `SIGTERM → SIGKILL`。
14. 当 `force=true` 中断处于 `teardown_disarm` 的 Job 时，Agent 必须将已开始但被中断的解除步骤记为 `incomplete`，并将尚未开始的解除步骤保留为 `not_executed`。
15. 当 `force=true` 中断处于 `teardown_disarm` 的 Job 时，Agent 必须以终止原因 `force_abort` 上报 Job 终态 `ABORTED`。
16. 当 `force=true` 中断处于 `teardown_disarm` 的 Job 时，控制面不得豁免 ADR-0061 设备归还欠账判定，必须通过统一归还租约入口按 `执行协议契约（07-execution-protocol.md）§9.5` 转换或保留本设备的 `MAINTENANCE` 欠账租约。
17. 当 `force=true` 请求获取锁时若目标 Job 已先期提交终态 `/complete`，控制面不得覆写已提交终态，必须在本次 `abort` 响应摘要中将该 Job 标记为 `already_terminal`。
18. 当 `force=true` 先于 `/complete` 提交到 `PlanRun.run_context`、而 Agent 在收到强杀前已完成并提交合法 `/complete` 时，控制面不得回改该已提交终态，必须在审计与终态摘要中记录 `force_requested_before_complete_settled`。
19. 前端在发起 `force=true` 请求前必须展示二次确认弹窗，向操作者明确提示强杀刷机临界区可能导致设备滞留下载态并需人工复位及隔离恢复。

### D3 统一 `FinishingGuard` 底座、兼容锁序两段许可仲裁与两层冻结 `atomic_group`

1. 实施者必须在 `backend/agent/contracts/` 中建立统一的 `FinishingGuard` 契约，以 `kind ∈ {"teardown_disarm", "atomic_commit"}` 区分两类收尾成员。
2. 实施者不得将 B 类保护逻辑以特例分支散落拼接在 `disarm_guard` 专用代码中。
3. 实施者必须将既有 B6（`执行协议契约（07-execution-protocol.md）§9.2`）的 `disarm_guard` 与 `EXECUTING_DISARM` 语义收编为 `FinishingGuard(kind="teardown_disarm")`。
4. 实施者必须在过渡期内保持既有 B6 字段与线协议向后兼容。
5. 脚本发布者必须在具备不可中断硬件临界区的脚本包元数据（`capabilities.json`）中显式声明能力标记 `critical_section_guard`、最小临界区执行预算 `min_critical_section_seconds` 与默认临界区预算上限。
6. 控制面与 Agent 不得按脚本名硬编码猜测临界区能力。
7. 实施者必须在派发前不可变的 `plan_snapshot`（第一层冻结，遵循 `执行协议契约（07-execution-protocol.md）§5`）中以显式 `atomic_group` 列表声明并冻结静态刷机模板的原子交付成员步骤（含步骤标识、顺序、固定脚本版本与超时）。
8. 实施者必须迁移专用刷机模板（如 `flash_unisoc_bsl`、`flash_firmware`）以在模板定义中显式声明 `atomic_group` 成员。
9. 实施者不得将可选步骤列入 `atomic_group`，`atomic_group` 内全部成员步骤均必须作为必需步骤执行。
10. 控制面与 Agent 不得将同一 Plan `init` 阶段内未列入 `atomic_group` 的其余步骤默认吞并入原子闭环。
11. 对 VBUS 刷机作业，Agent 引擎必须在发起 `critical_section_enter_request` 之前展开 ADR-0063 D5 第 3 条刷后 `return_check` 的确切成员、顺序、脚本版本与步骤超时并随预登记请求上报。
12. 控制面必须在预登记事务内校验上述 VBUS 展开结果与 `plan_snapshot` 及已登记专项归还探针一致，并将合并后的完整有效成员列表写入 `JobInstance.finishing_guard`（第二层每 Job 冻结，一经写入不可变，不得回写 `plan_snapshot`）。
13. 若上述冻结的 VBUS 刷后 `return_check` 返回失败或抛出异常，Agent 引擎必须保留 ADR-0063 D5 第 3 条口径，以 `init` 失败（`FAILED`）结束本 Job 且不得执行本 Job 的 `teardown`。
14. 声明了 `critical_section_guard` 的脚本在执行首次不可中断硬件副作用之前（例如 `flash_firmware_unisoc_bsl` 持主机锁并准备发送 `adb reboot autodloader` 启动 `spd_dump` 之前），必须向 Agent 发起有界同步进入许可请求（`critical_section_enter_request`）并阻塞等待显式 `grant` 或 `deny` 回复。
15. 脚本不得仅凭单向 `stderr` 打点即开始执行不可中断硬件操作。
16. 若进入许可请求超时、控制通道断开或收到 `deny`，脚本必须立即按失败或中止退出，不得执行任何硬件写入或重启指令。
17. 控制面必须使临界区预登记事务严格遵循固定无死锁锁序：先以 `FOR UPDATE` 锁定本 `JobInstance` 行，再以 `FOR NO KEY UPDATE` 锁定所属 `PlanRun` 行，最后以 `FOR SHARE` 锁定单一全局降级准入门禁行直至事务提交（与 `plan_run_abort` 的 `PlanRun FOR NO KEY UPDATE` 互斥串行，与 `/complete` 外键 `FOR KEY SHARE` 兼容，并与回滚关门事务在准入门禁行上的 `FOR UPDATE` 严格串行）。
18. Agent 必须在发送预登记请求前为本次尝试生成客户端唯一幂等键 `request_id` 并随请求上报。
19. 控制面在签发每份进入许可时，必须将其绑定至 `(job_id, attempt_id, step_id, worker_boot_id, fencing_token, request_id, permit_id, expires_at)`。
20. 控制面与 Agent 不得接受跨尝试、跨 worker、跨租约代次、不匹配 `request_id` 或已超过 `expires_at` 的许可。
21. Agent 必须将 `critical_section_enter_request` 与普通 `abort`、`force=true` 及失租信号置于同一进程内互斥仲裁点并按以下规则仲裁：
    - 若仲裁点已收到普通 `abort`、`force=true` 或确认失租，Agent 必须向脚本回复 `deny`；
    - 若仲裁点尚未收到中止信号，Agent 必须先携带 `request_id` 向控制面发起有界同步预登记请求；
    - 若控制面在预登记事务内发现全局降级准入门禁已关闭（D5 第 7 条）、任一适用主体已提交有效 `abort_requested`、本 Job 的 `ACTIVE JOB` 租约已失效或到期、或当前步骤剩余预算与全组剩余预算中任一项低于 `min_critical_section_seconds`，控制面必须拒绝预登记且不得写入 `flash_integrity` 义务；
    - 收到控制面签发的有效预登记许可后，Agent 必须在向脚本投递 `grant` 之前向控制面发起有界同步激活请求 `activate_permit(permit_id, request_id, fencing_token)`；
    - 在收到控制面 `activate_permit` 成功确认（含未过期 `lease_expires_at`）并完成本地租约余量与预算复核之前，Agent 不得向脚本回复 `grant`；
    - 收到控制面拒绝预登记、拒绝激活、任一响应超时、许可已过期、或激活响应扣除本地往返耗时后 `JOB` 租约剩余有效期不足安全余量或剩余预算（含 60s 存活窗余量）不足时，Agent 必须向脚本回复 `deny`；
    - 当预登记或激活请求因响应丢失而超时、Agent 从未向脚本投递 `grant` 且脚本全硬件进程集合已确认停止时，Agent 可以携带客户端生成的 `request_id`（以及可选 `permit_id`）与 `(job_id, attempt_id, step_id, fencing_token)` 向控制面提交显式 `cancel_pre_registration`；
    - 控制面收到上述合法 `cancel_pre_registration` 后，必须按 `request_id` 定位并撤销该次未触碰硬件的 `flash_integrity` 待核验义务；
    - 若 Agent 在证明未投递 `grant` 前崩溃或失租，控制面必须保留该未关闭的 `flash_integrity` 义务并按 D4 第 28 条隔离设备；
    - 仅当控制面返回 `activate_permit` 成功确认（`guard_state="granted"` 已落库）、Agent 复算本地未过期 `JOB` 租约余量、存活窗余量与剩余预算均充足且仲裁点仍无中止信号时，Agent 必须将本 Job 原子置为 `critical_section_granted=true` 并向脚本回复 `grant`。
22. 在 `critical_section_granted=true` 生效后、统一 `FinishingGuard` 截止 `D_atomic` 到达之前且存活心跳新鲜期间，Agent 的步骤墙钟定时器与停滞 watchdog 不得独立杀死该子进程树。
23. 脚本在硬件不可中断阶段完成后必须向 Agent 上报 `critical_section="exit"`。
24. 在硬件 `critical_section="exit"` 之后、本机冻结 `atomic_group` 全部成员步骤执行完毕之前，无论普通 `abort` 是在 `exit` 之前还是在 `exit` 之后到达，Agent 都不得提前撤销 `atomic_commit` 保护，必须继续保护至本机 `atomic_group` 结束或 `D_atomic` 到达。
25. 当本机冻结 `atomic_group` 在未收到 `abort` 的情况下自然执行完毕时，Agent 必须正常退出 `atomic_group` 保护并按常规生命周期继续执行后续阶段或结束 Job。
26. 脚本在获得 `grant` 之前（例如固件缓存拉取或 `lock-wait` 排队期间），Agent 不得将其纳入 `atomic_commit` 保护，收到普通 `abort` 时必须立即中止。

### D4 统一有界保护、租约有效激活、硬件完整性欠账与全进程树停止门禁

1. 控制面与 Agent 必须对 `teardown_disarm` 与 `atomic_commit` 两类收尾强制实施有界保护窗，不得提供无上限等待。
2. 控制面 `abort reaper`（`device_lease_reconciler`）与 `recycler` 必须调用同一个 `FinishingGuard` 判定入口决定是否推迟 `UNKNOWN` 回收。
3. 控制面 `abort reaper` 与 `recycler` 必须在候选筛选阶段与最终行锁/CAS 事务提交阶段复读同一份持久化保护事实。
4. 首次冻结保护截止时间后，控制面与 Agent 不得因重复 `abort`、乱序回报、步骤重试或服务重启延长截止时间。
5. 控制面在 `plan_run_abort` 的全 Run 级与主机级两条写入路径中必须均保留首次 `at` 时间戳（补齐 ADR-0043 D2 在全 Run 重复中止上的现存偏差）。
6. 当全 Run 级与主机级中止时钟并存时，控制面必须取较早者作为有效宽限时钟基准，且必须完整覆盖主机级 late-claim Job。
7. 控制面计算 `teardown_disarm` 的截止时间时必须完全沿用 `执行协议契约（07-execution-protocol.md）§9.2`（`Q=7200s`、各解除步骤 `E_i`、绝对上限 `86400s`）。
8. 控制面与 Agent 必须将冻结 `atomic_group` 成员步骤中缺失或 `<= 0`（含 `0`）的 `timeout_seconds` 统一归一化为缺省值 `1800s`（且单步上限不得超过 `ATOMIC_HARD_CAP_SECONDS`）。
9. 控制面必须按服务端时钟将 `atomic_commit` 的绝对保护截止时间 `D_atomic` 一次性冻结为以下三项同量纲绝对时刻的最小值，且不得引用仅用于 `patrol` 的 `lifecycle.timeout_seconds`：
   - `D_steps = T_arm + E_curr_rem + sum(E_i for subsequent steps i in atomic_group_frozen)`，其中 `T_arm` 为控制面服务端批准预登记时刻，`E_curr_rem = max(0, E_curr_norm - elapsed_pre_arm)` 为当前临界区步骤归一化超时扣除获批前已耗时后的剩余预算，后续各成员步骤（含第二层冻结的 VBUS `return_check`，其归一化时限覆盖该合成检查允许的探测、条件解除与允许的重试）取归一化冻结超时且重试不得重置；
   - `D_cap = T_arm + ATOMIC_HARD_CAP_SECONDS`（缺省 `3600s`）；
   - `D_run = T_job_start + PLAN_INIT_WALL_CAP`（`T_job_start` 为控制面记录的本 Job 启动时刻，`PLAN_INIT_WALL_CAP` 为冻结 `init` 各步归一化超时之和加上第二层冻结合成检查超时、契约允许的条件步骤重试预算及平台预留余量）。
10. 控制面在预登记准入时必须同时校验 `E_curr_rem >= min_critical_section_seconds` 与 `D_atomic - T_arm >= min_critical_section_seconds`，任一不满足时控制面必须拒绝预登记。
11. 控制面在提交预登记时必须将 `JobInstance.finishing_guard` 置为有界预登记态 `guard_state="pre_registered"`（以 `permit_expires_at = T_arm + PRE_REG_GRACE_SECONDS` 封顶，缺省 `30s`），并将 `last_liveness_receipt_at` 与 `last_progress_receipt_at` 初始化为 `T_arm`。
12. 当 `JobInstance.finishing_guard` 处于 `guard_state="pre_registered"` 且服务端时钟未超过 `min(permit_expires_at, D_atomic)` 时，控制面 `recycler` 与 `abort reaper` 不得因首条心跳尚未到达而将该 Job 置为 `UNKNOWN`。
13. 若服务端时钟超过 `permit_expires_at` 仍未完成 `activate_permit` 激活，控制面必须在 `JobInstance FOR UPDATE` 行锁事务内停用该次 `pre_registered` 保护并恢复常规回收。
14. 控制面必须在 `JobInstance FOR UPDATE → PlanRun FOR NO KEY UPDATE` 行锁事务内处理 `activate_permit`（与第 13 条到期回收严格互斥），核验以下全部条件：本 Job 仍为 `RUNNING`、`permit_id` / `request_id` / `fencing_token` 匹配、服务端时钟未超过 `min(permit_expires_at, D_atomic)`、无已提交中止，且同 Job、同设备与同 worker 当前持有状态为 `ACTIVE` 且 `expires_at > T_act` 的未过期 `JOB` 租约。
15. 仅当第 14 条全部条件成立时，控制面必须将保护态原子推进为 `guard_state="granted"`、将 `last_liveness_receipt_at` 与 `last_progress_receipt_at` 刷新为激活提交时刻 `T_act`（开启完整 `60s` 存活窗），并在响应中返回 `lease_expires_at` 与 `lease_remaining_seconds`。
16. 若第 14 条任一条件不满足（含 `ACTIVE JOB` 租约已到期、缺失、权属变更或租约代次失配），控制面必须拒绝 `activate_permit`（返回 `409` 或 `410`）且不得推进为 `granted`。
17. Agent 在收到 `activate_permit` 成功确认、完成本地租约有效期与预算复核并向脚本投递 `grant` 后，必须自 `grant` 起直至本机 `atomic_group` 结束（独立于当前 `ui_status` 是否为 `"running"` 以及是否已收到 `abort`）每 ≤15s 持续上报受控存活心跳。
18. 控制面收到上述有效存活心跳时必须按服务端接收时刻刷新存活窗口。
19. Agent 可以在上报的存活心跳中重复携带最近一次进展快照。
20. 控制面必须仅在收到单调更新的真实新进展事实时刷新 `silent_write_progress_window_seconds` 推进窗口，不得因心跳重复携带旧进展快照延长该推进窗口。
21. 控制面必须按控制面服务端接收时刻分别核验存活与推进两类新鲜度证据：
    - 若超过 `60s` 未收到存活心跳，控制面不得继续推迟回收；
    - 为兼容 `spd_dump` 静默写分区期间工具无输出超过 `60s` 的正常工况，在存活心跳持续新鲜前提下，控制面必须按独立的 `silent_write_progress_window_seconds`（缺省 `300s`，且受 `D_atomic` 封顶）核验推进新鲜度；
    - 若超过 `silent_write_progress_window_seconds` 仍无真实新进展证据，控制面不得继续推迟回收。
22. 当 host 心跳有效、本 Job 的 `JOB` 租约处于 `ACTIVE` 且未过期、`fencing_token` 与 `boot_id` 匹配、设备占用归属匹配、存活与推进证据均在有效窗内（或处于未过期的 `pre_registered` 窗内）这五项条件中任一项不满足时，控制面不得推迟回收。
23. 若控制面检测到服务器时钟回拨，控制面必须向该 Job 的保护记录持久化写入单向失效标记 `guard_invalidated_reason="clock_regressed"` 并立即停用保护。
24. 对已写入 `guard_invalidated_reason="clock_regressed"` 的 Job，控制面不得在后续轮询中恢复推迟回收。
25. Agent 必须在心跳、预登记与激活请求中随 `boot_id` 一并上报 `boot_id_source`（仅当直接读自 Linux `/proc/sys/kernel/random/boot_id` 时记为 `"kernel_proc"`，其余含读取失败生成的随机回退值均记为 `"pseudo_fallback"`）。
26. 控制面在批准 `critical_section_enter` 预登记的同一事务内，必须在数据库中持久化一条与 `(device_id, job_id, request_id, permit_id)` 绑定的待核验硬件完整性义务（`obligation_kind="flash_integrity"`），同时记录本次尝试的 `worker_boot_id`、`boot_id_source` 与进程约束标识，纳入统一的设备归还欠账机制。
27. 当已登记 `flash_integrity` 义务的 Job 通过合法 `/complete` 上报完整 `atomic_group_evidence`（证明冻结 `atomic_group` 全部成员步骤均已实证成功，含刷后开机回读与版本校验通过；即使该混合 Job 随后在 `teardown_disarm` 阶段被 `force=true` 中断亦同）时，控制面必须在终态事务内关闭该 `flash_integrity` 义务条目。
28. 当已登记 `flash_integrity` 义务的 Job 在未产生完整实证 `atomic_group_evidence` 的情况下发生以下任一情形——在 `atomic_group` 阶段内被 `force=true` 强杀、`atomic_group` 步骤失败或保护窗超期、`extend_lock` 或 `/complete` 返回 `409` 失租、或 `UNKNOWN` 到期落 `FAILED`——控制面必须在统一归还租约事务内保留未关闭的 `flash_integrity` 义务并将本 Job 的 `JOB` 租约转为 `MAINTENANCE` 欠账租约。
29. 在按第 27 条关闭 `flash_integrity` 义务条目后，控制面不得直接释放本 Job 的 `JOB` 租约，必须统一调用同步或异步归还租约入口（`handle_job_lease_release_sync` / `handle_job_lease_release`）检查该设备全部未关闭欠账条目（含同一混合 Job 因 `teardown_disarm` 被强杀或失败产生的 B6 欠账）：仅当全设备未关闭欠账数为 `0` 时释放本 Job 的 `JOB` 租约，仍有任一未关闭欠账时必须转为或保留 `MAINTENANCE` 欠账租约。
30. 控制面不得在未经授权的设备开机与固件一致性回读探针清偿、或未经 Owner/管理员人工放行的情况下关闭 `flash_integrity` 欠账条目。
31. 当 Owner 或管理员人工放行 `flash_integrity` 欠账条目时，控制面必须仅将 `flash_integrity` 专项归还状态更新为 `unverified`（不得置为 `returned`），并按全设备所有已登记专项重算聚合归还状态与 `MAINTENANCE` 欠账租约去留。
32. 终态租约转换完成后，控制面对 `/complete` 重放与迟到回报必须保持以下区分：
    - 若目标 Job 已处于终态且来访载荷摘要与已接受的 `terminal_payload_digest` 一致，控制面必须返回只读幂等 `200` 且不得改变任何欠账或租约状态；
    - 若载荷摘要不一致且原 `JOB` 租约已转换为欠账租约，控制面必须先对 B6 解除结果执行现行同源可信迟到确认（匹配未关闭欠账行记录的原 `fencing_token`），不得一刀切覆盖该兼容路径；
    - 若 `fencing_token` 与已登记历史租约或欠账源令牌均不匹配，控制面必须返回 `409` 租约拒绝（`stale_job_completion_rejected`）且不得解除 `MAINTENANCE` 欠账租约；
    - 若 `fencing_token` 有效但提交了与已落库终态冲突的新摘要，控制面必须按现行 `terminal_payload_conflict` 口径拒绝并记录审计，不得与租约失配混淆。
33. 当任一保护截止到达、确认失租（`409`）或收到 `force=true` 时，Agent 必须立即停止收尾并上报终态。
34. 在上述保护终止条件命中后，控制面必须恢复按现行无保护规则执行回收。
35. 控制面热更新门控（`host_upgrade_gate`）必须继续将 `RUNNING` 与 `UNKNOWN` 均视为活跃，并保留默认 `45s` 排空超时后拒绝升级的安全边界。
36. 控制面热更新门控在因活跃作业拒绝升级时，必须在响应中补充按主体汇总的 `finishing_kind` 原因与重试等待提示。
37. Agent 优雅停机（`graceful_shutdown`）在收到 `SIGTERM/SIGINT` 时，必须对在途 `atomic_commit` 作业提供与现有 `teardown_disarm` 共用的有界停机结算窗，且不得突破 systemd `TimeoutStopSec`。
38. Agent 必须为每个活跃 `job_id` 维护本地单次终态提交锁/CAS 槽位，确保退出中的 worker 与停机路径最多仅有一方提交终态。
39. 实施者必须通过每尝试专属 Linux cgroup 或全量受追踪进程组集合，约束或追踪声明 `critical_section_guard` 的每次 Job 尝试的全部硬件侧进程（包括 MTK `flash_firmware` 以 `start_new_session=True` 独立会话启动的 `flash_tool` 等子工具）。
40. 在未启用每尝试专属 Linux cgroup 的宿主上，脚本不得以独立会话启动硬件侧子工具，除非在首次硬件 I/O 之前将全部内层子进程组的 `(pgid, start_time)` 注册至本尝试受追踪进程组集合并上报控制面。
41. 当停机结算窗到期且退出中的 worker 尚未占领上述本地 CAS 槽位时，Agent 停机路径必须先封锁该槽位。
42. 封锁槽位后，Agent 停机路径必须向本尝试受约束的全部硬件侧进程集合（尝试专属 cgroup 全部成员，或外层脚本与全部已注册内层可信 `pgid`）发送 `SIGTERM → SIGKILL`。
43. 发送终止信号后，仅当直接子进程已回收且尝试专属 cgroup 进程列表为空，或全部已注册可信 `pgid`（含外层脚本与全部独立会话子工具 `pgid`）执行 `os.killpg(trusted_pgid, 0)` 均返回 `ESRCH` 时，Agent 才可以判定 `process_group_stopped=true`。
44. 凡存在未追踪独立会话、残留子孙、`EPERM`、探测失败或超时的情形，Agent 必须判定 `process_group_stopped=false`。
45. 完成上述有界停组核验后，Agent 停机路径必须在关闭 LocalDB 前向本地 outbox 持久化未验证中断记录并如实写入 `process_group_stopped`。
46. 对所有已登记 `flash_integrity` 义务且经由非正常实证完成路径（包括优雅停机 outbox、worker 先占本地 CAS 槽位提交的终态、`force=true` 强杀与步骤超时中断）结束的 Job，Agent 必须在 `/complete` 或 outbox 载荷中按第 43–44 条全硬件进程集合收敛判据上报 `process_group_stopped`（缺省按 `false` 处理）。
47. 当任一未关闭 `flash_integrity` 欠账条目缺失可信全硬件进程停止证明（`process_group_stopped IS NOT TRUE`，覆盖 `false` 与缺失 `NULL`，包括停机 outbox、worker 先占槽、`force=true` 路径未证明收敛及失联超时无回报）时，控制面统一归还、自动清偿与人工放行入口均不得关闭该条目或释放 `MAINTENANCE` 欠账租约。
48. 仅当满足以下任一可信停止证据时，控制面才可以解除第 47 条门禁并允许后续清偿或人工放行：
    - 当宿主新心跳的 `boot_id` 与该条目记录的 `worker_boot_id` 均满足 `boot_id_source="kernel_proc"`（严格读自 Linux `/proc/sys/kernel/random/boot_id`，排除随机回退 `pseudo_fallback`）且两者不同时，控制面可以认定内核已真实重启并解除第 47 条门禁；
    - 当同内核重启后的 Agent 或经授权的宿主探针按记录的尝试专属 cgroup 或全量受追踪 `(pgid, start_time)` 身份核验确认全部硬件侧进程均已退出（cgroup 已清空或全部受追踪 `pgid` 返回 `ESRCH`）时，控制面可以解除第 47 条门禁。
49. 面对伪造或回退 `boot_id`、未知进程组身份或探针探测失败的情形，控制面不得解除第 47 条停止证明门禁。

### D5 按子类型区分终态口径、混合 Plan 边界与在途版本交错兼容

1. 控制面与 Agent 必须按收尾子类型本质分别判定 Job 终态，不得混用两类口径：
   - 对 `teardown_disarm`（A 类），控制面与 Agent 必须保持 ADR-0061 契约：由主体测试的 `termination_reason` 决定 Job 终态（手动中止落 `ABORTED`，自然到点落 `COMPLETED`），且不得因解除成败改写该测试终态；
   - 对 `teardown_disarm`（A 类）中的解除异常，控制面必须按 ADR-0061 过渡与接入规则处置：已接入专项的解除失败/`incomplete`/`not_executed`/`readback_failed` 必须记为设备欠账，未接入专项的普通 `failed` 必须按既有过渡规则记为 `unverified`；
   - 对 `atomic_commit`（B 类）且属于纯原子交付的 Job（冻结计划在当前 `atomic_group` 之后不存在未执行的必需非原子阶段，例如无 `patrol`），若收到普通 `abort` 后冻结 `atomic_group` 内全部成员步骤均产生真实完成证据并执行成功，Agent 必须上报 Job 终态 `COMPLETED`；
   - 对上述在普通中止排空中成功的纯原子交付 Job，Agent 必须在 `/complete` 载荷中标记 `completed_during_abort_drain=true` 与 `atomic_group_completed=true` 并附可核验的 `atomic_group_evidence`；
   - 对冻结 `atomic_group` 内任一成员步骤返回 `success=true, skipped=true` 的情形，除非该步骤契约显式声明 `skip_satisfies_delivery=true` 并附目标版本核验证据，否则 Agent 必须将其判定为该步骤原子交付失败（覆盖引擎通用 `skipped && success` 放行分支）；
   - 判定上述无交付证据跳过失败后，Agent 必须立即停止执行冻结 `atomic_group` 的后续成员步骤；
   - 对上述因无交付证据跳过而中止的 `atomic_group` Job，Agent 必须上报 Job 终态 `FAILED`；
   - 对 `atomic_commit`（B 类）且属于混合 Plan 的 Job（冻结计划在 `atomic_group` 之后仍包含 `patrol` 或其他必需非原子阶段），若收到普通 `abort` 后本机 `atomic_group` 全部成员实证成功，Agent 必须跳过 `patrol`（按既有引擎契约推进或到达 barrier 以免阻塞同伴）；
   - 对上述跳过 `patrol` 的混合 Plan Job，若已建立专项设备足迹，Agent 必须按 ADR-0061 转交 `teardown_disarm` 执行解除；
   - 对上述混合 Plan Job，Agent 必须最终上报 Job 终态 `ABORTED`（不得上报 `COMPLETED`）；
   - 对上述混合 Plan Job，Agent 必须在 `/complete` 载荷中标记 `atomic_group_completed=true` 与完整 `atomic_group_evidence` 以供控制面按 D4 第 27 条关闭 `flash_integrity` 义务；
   - 对任意 `atomic_commit`（B 类）Job，若冻结 `atomic_group` 内任一成员步骤失败或保护窗超期，Agent 必须上报 Job 终态 `FAILED`；
   - 对任意 `atomic_commit`（B 类）Job，若被 `force=true` 强杀或未获批进入临界区即被中止，Agent 必须上报 Job 终态 `ABORTED`。
2. 无论 Job 经由正常完成（未收到 `abort`）、普通中止排空还是混合 Plan 退出结束，只要执行了冻结 `atomic_group`，Agent 都必须将 `atomic_group_completed`、`atomic_group_evidence`、`completed_during_abort_drain`、`finishing_kind`、`abort_mode` 与 `process_group_stopped` 写入 `/complete` 请求载荷并持久化至 Agent LocalDB outbox。
3. 控制面必须在终态提交事务中校验并消费上述字段，并写入 Job 终态摘要。
4. 控制面必须保持 ADR-0048 父 PlanRun 聚合口径：只要 PlanRun 存在有效 `abort_requested`（或 `aborted > 0`），全部 Job 落终态后控制面必须将父 PlanRun 聚合为 `FAILED`。
5. 对因上述规则聚合为 `FAILED` 的父 PlanRun，控制面不得触发 `next_plan_id` 续链。
6. 控制面必须将纯原子交付 Job 在 drain 中以 `COMPLETED` 落终态的设备独立计入成功数，便于运维区分已交付与未交付设备。
7. 控制面与 Agent 在滚动升级与版本交错期间必须遵守以下在途能力冻结、准入门禁与降级规则：
   - **启动期能力准入**：在 Job claim 或启动时刻，控制面与 Agent 必须按冻结脚本包 `capabilities.json`、Agent 运行时能力与控制面 API/reaper 能力的交集协商并持久化该 Job 的 `negotiated_capabilities`（含 `finishing_guard_v2`、`atomic_commit`、`force_abort`）；
   - **在途能力冻结**：对已启动的在途 Job，控制面与 Agent 必须在其整个生命周期内锁定上述已协商能力集；
   - **未协商场景**：当 Job 启动时任一方未声明 `atomic_commit` 能力（脚本包无 `critical_section_guard`、旧版 Agent 或旧版控制面），控制面与 Agent 不得启用 `atomic_commit` 保护，必须退化为现行无临界区保护的 `abort` 行为且不得伪造保护窗；
   - **心跳回复缺字段场景**：滚动发布期间若新 Agent 收到旧控制面实例不含 `finishing_guard_v2` 字段的心跳回复，对新启动的 Job，Agent 不得协商启用 `atomic_commit` 并必须记录 `atomic_guard_protocol_unavailable`；
   - **心跳回复缺字段时的在途 Job**：对启动前已冻结 `atomic_commit` 能力的在途 Job，Agent 必须继续按已冻结能力并依赖控制面预登记与激活接口响应决策，若任一接口不可用则必须向脚本回复 `deny` 安全退化；
   - **回滚前关门提交屏障**：运维人员与发布流程计划将控制面回滚至不支持 `finishing_guard_v2` 的旧版本时，必须先在单一全局降级准入门禁行上获取 `FOR UPDATE` 独占锁、写入 `admission_open=false` 并提交（与所有在途预登记事务在该行持有的 `FOR SHARE` 锁严格串行，确保关门提交后不再有任何在途预登记提交）；
   - **回滚前排空活跃作业至零**：上述关门事务提交后，运维人员与发布流程必须确认全部已提交预登记或已获得 `grant` 的活跃 Job（含仍投影为 `"running"` 的刷机 Job）计数为 `0`；
   - **回滚前核验停止证明欠账为零**：在上述排空完成后，运维人员与发布流程必须确认全库不存在缺失可信全硬件进程停止证明的未关闭 `flash_integrity` 欠账条目（查询谓词必须采用 `process_group_stopped IS NOT TRUE` 以同时计入 `false` 与缺失 `NULL`，不得使用漏计 `NULL` 的 `!= TRUE`，防止旧版人工放行绕过 D4 第 47–49 条释放 `MAINTENANCE` 欠账租约）；
   - **切换全部消费者**：仅在关门已提交、活跃计数为 `0` 且无未确认停组的 `flash_integrity` 欠账后，运维人员与发布流程才可以切换控制面 API、`device_return`、`abort reaper` 与 `recycler` 至旧版本；
   - **重连恢复与 `force` 同步**：Agent 重连执行 `agent_recovery`（含 `ABORT_LOCAL` 路径）或控制面重试下发中止时，控制面必须按 D2 第 10 条在所有适用主体上取最高 `force` 强度并同步下发，不得仅按较早时钟主体降级强度；
   - **旧版无法兑现 `force=true` 的显式反馈**：当操作者对锁定旧版能力（无 `force_abort`）的 Agent 主机下发 `force=true` 时，控制面不得静默宣称已强杀，必须在响应中显式返回 `force_unsupported_hosts`；
   - **旧版主机的兼容中止下发**：对上述列入 `force_unsupported_hosts` 的旧版 Agent 主机，控制面必须同时继续下发普通兼容中止指令并提示操作者风险。

---

## 3. 备选方案与权衡

| 备选方案 | 做法 | 未采纳理由 |
|---|---|---|
| **方案 1：拆分为两个对外过渡态名称（`中止中` + `收尾中`）** | 人工中止显示「中止中」，自然到点显示「收尾中」。 | Owner 裁定合并为单一顶层概念 **`收尾中`（`finishing`）**，通过子类型（`teardown_disarm` / `atomic_commit`）与触发源字段表达差异，避免界面与 API 状态词表碎片化。 |
| **方案 2：在 `disarm_guard` 上直接加 `if is_flash` 特例补丁** | 保持 `disarm_guard` 只面向 B6，把刷机硬塞进解除逻辑里。 | A 类（后置解除，终态正交、失败转欠账）与 B 类（原子交付闭环，含刷前完整性义务与整体成败口径）语义本质不同，硬塞会导致代码和契约持续腐化；抽成统一 `FinishingGuard` 集合 + 两类子模型才能一劳永逸。 |
| **方案 3：仅靠单向 `stderr` `critical_section="enter"` 打点或在激活确认前即投递 `grant`** | 脚本打印标记后或 Agent 收到预登记响应后立即执行硬件操作，事后再补发激活心跳。 | 存在两类明确竞态：单向打点来不及仲裁 `abort`；先投递 `grant` 后补激活会在 `permit_expires_at` 边界被 `recycler` 写入 `UNKNOWN` 导致在途烧写失租；改为「预登记 → `activate_permit` 复核未过期 `ACTIVE JOB` 租约并与 `recycler` 行锁互斥确立 `granted` → 再在租约余量内投递 `grant`」彻底闭合竞态。 |
| **方案 4：预登记采用 `PlanRun FOR UPDATE → JobInstance` 锁序** | 先强锁父 `PlanRun` 行再锁子 `JobInstance` 行。 | 在 PostgreSQL 中与 `/complete`（先锁 `JobInstance`、再因写入 `plan_run_pending_aggregation` 触发父行外键 `FOR KEY SHARE`）成环产生 `40P01` 死锁；改采现行 `JobInstance FOR UPDATE → PlanRun FOR NO KEY UPDATE → 准入门禁行 FOR SHARE` 既与 `plan_run_abort`、回滚关门串行又与外键 `FOR KEY SHARE` 完全兼容。 |
| **方案 5：仅凭外层脚本 `waitpid` 或单 PGID 判定停机已停止，且接受任意 `boot_id` 变化** | 停机路径仅核验外层脚本 PGID 或不区分 `boot_id` 来源即放行欠账。 | 外层 PGID 漏过 MTK `flash_firmware` 等以 `start_new_session=True` 启动的内层 `flash_tool` 会话；`read_boot_id()` 回退随机值会在同一内核重启 Agent 时伪造“重启”假象；改用每尝试专属 cgroup 或全受追踪 PGID 集核验，并限定重启证明仅认 `kernel_proc` 真值。 |
| **方案 6：默认吞并同 Plan `init` 阶段全部剩余步骤作为原子尾段** | 不显式声明 `atomic_group`，收到 `enter` 后默认放行所有剩余 `init` 步骤。 | 容易误把第二段刷机或无关的专项足迹建立步骤卷入中止后放行范围，且无法覆盖不在 `PlanStep` 列表里的引擎插入 VBUS 刷后 `return_check`；改为两层显式冻结全必需成员的 `atomic_group` 可控且无歧义。 |
| **方案 7：临界区烧写成功后跳过 `oobe` 并把 Job 标 `ABORTED`** | 烧写完立刻跳后续步骤并强制标 `ABORTED`。 | 会留下「已刷机但未过 OOBE」的半成品设备，且运维无法从 Job 终态直接看出该机已完成交付；Owner 裁定单台纯刷机流程按整体执行并按整体成败落 `COMPLETED` / `FAILED`。 |
| **方案 8：在 DB `status` 枚举新增 `FINISHING` 持久化状态** | 改数据库状态机与部分唯一索引。 | 触及 `uq_job_active_per_device`、claim、reaper、聚合器、热更新全链路，迁移与回归风险远高于权威投影方案。 |

---

## 4. 影响范围

- **共享契约层（`backend/agent/contracts/`）**：新增统一 `finishing_guard` 契约模块（定义 `FinishingKind`、`FinishingTrigger`、超时归一化与同量纲截止计算、`pre_registered`/`granted` 状态、存活/真实新进展分离判定、`flash_integrity` 义务与 `process_group_stopped`/`boot_id_source` 契约、能力协商常量，向后兼容既有 `device_return` / `disarm_guard` 载荷）。
- **Agent 侧**：
  - `backend/agent/identity.py` 与 `backend/agent/heartbeat.py`：暴露并随心跳/预登记上报 `boot_id_source`（`kernel_proc` vs `pseudo_fallback`）；
  - `backend/agent/pipeline_engine.py`：实现进入临界区的同步许可仲裁点、携带客户端 `request_id` 的控制面预登记 + `activate_permit` 激活与本地 `JOB` 租约余量复核 + 未投递 `cancel_pre_registration` 调用、自 `grant` 起持续存活心跳与新进展上报、watchdog/步骤墙钟与 `D_atomic` 协同、两层冻结 `atomic_group`（含预登记前展开的 VBUS 刷后 `return_check` 及 ADR-0063 D5.3 失败跳过 teardown）、覆盖组内无证据 `skipped=true`、混合 Plan `atomic_commit → teardown_disarm` 交接、`force=true` 强杀与全硬件进程树约束/停止证明（尝试专属 cgroup 或全受追踪可信 PGID 集，覆盖 `start_new_session=True` 子工具）；
  - `backend/agent/api_client.py` 与 LocalDB outbox：扩展 `/complete` 载荷（`atomic_group_completed`、`atomic_group_evidence`、`completed_during_abort_drain`、`finishing_kind`、`abort_mode`、`process_group_stopped`）与持久化 outbox；
  - `backend/agent/graceful_shutdown.py`：纳入在途 `atomic_commit` 的有界停机结算、按 `job_id` 单次终态提交 CAS 槽位互斥以及全硬件进程树停止核验与 `process_group_stopped` 记录；
  - 刷机脚本族（`flash_firmware_unisoc_bsl`、`flash_firmware`）：新版本声明 `critical_section_guard`、`min_critical_section_seconds`、接入同步进入许可握手与 `critical_section="exit"`，并配合尝试专属 cgroup 或独立会话子工具 PGID 注册满足全硬件进程树停止证明。
- **控制面侧**：
  - `backend/services/plan_run_abort.py` 与 `backend/core/job_timeout_config.py`：新增 `force` 参数、按主体持久化单调 `force` 状态、保持 `JobInstance FOR UPDATE → PlanRun FOR NO KEY UPDATE` 无死锁锁序、修正全 Run 级重复中止保留首次 `at`（ADR-0043 D2）、实现多主体时钟取早与强度取高、将 `UNKNOWN` 纳入中止候选、SocketIO 下发携带有效 `force` 并汇报 `already_terminal` / `force_unsupported_hosts`；
  - `backend/services/agent_recovery.py`：重连 `ABORT_LOCAL` 路径按所有适用主体取最高 `force` 强度透传；
  - `backend/services/device_return.py` 与 `backend/services/agent_completion.py`：新增兼容锁序预登记端点（`JobInstance → PlanRun → 准入门禁行 FOR SHARE` + 双预算准入 + 冻结 `D_atomic` 与 VBUS 展开列表 + 绑定 `request_id` 许可 + 写入 `pre_registered` 与 `flash_integrity` 义务）及行锁互斥 `activate_permit` 端点（复核未过期 `ACTIVE JOB` 租约并返回 `lease_expires_at`）；按阶段核销 `flash_integrity` 后统一走同步/异步归还入口裁定全设备欠账；在统一归还/自动清偿/人工放行入口强制校验 `process_group_stopped IS NOT TRUE` 门禁与 `kernel_proc` `boot_id` 重启真值；保留只读重放 `200` / B6 同源迟到确认 / `terminal_payload_conflict` 区分；
  - `backend/scheduler/device_lease_reconciler.py` 与 `backend/scheduler/recycler.py`：收敛到统一 `FinishingGuard` 判定入口，识别有界 `pre_registered` 与 `granted` 保护态，实行候选与行锁 CAS 双次复读及 `clock_regressed` 持久失效；
  - `backend/services/plan_run_devices.py`、`backend/services/plan_run_catalog.py` 与 `backend/services/host_upgrade_gate.py`：实现全量 `ui_status`（分页表与 Job 详情含子类型）与 Minimap 七字段 `job_exec_status` 投影（含刷机预期掉 ADB 不误降 `unknown` 与无截止收尾返回 `null`）、列表筛选计数与热更新拒绝提示；
  - `backend/models/job_instance.py` 与 `plan_snapshot`：扩展 `finishing_guard` / `negotiated_capabilities` / 两层冻结 `atomic_group` 结构与单一全局降级准入门禁行。
- **前端侧**：PlanRun 列表（含 `finishing` 筛选）、详情页、设备矩阵 Minimap（消费扩展后的七字段 `job_exec_status="finishing"`）、设备分页表与 Job 详情（展示子类型明细与倒计时），以及「强制中止」二次确认弹窗。
- **关联契约文档同步**：落地时同批更新 `docs/design/07-execution-protocol.md`、`docs/design/2026-11-24-list-pagination-and-facets-contract.md`（§5.1 `job_exec_status` 词表扩展）与 `docs/design/2026-semantic-ownership.md`。
- **兼容性**：严格向后兼容（D3 第 4 条、D5 第 7 条），不破坏现行 B6 归还链路与旧版 Agent。

---

## 5. 落地路径（按 ADR-0058 批次交付）

本提案跨控制面保护/欠账事务、Agent 执行引擎/停机结算、共享契约、刷机模板与在途版本交错兼容，明确命中 **ADR-0058 D7「多单元协调激活 + 数据/硬件安全」强判据**，转 `Accepted` 后按 ADR-0058 批次拆分并遵守 D8–D9（合入不等于生效，激活前全量复核通过）：

1. **阶段 1（ADR 定稿与语义登记）**：独立复核通过且 Owner 确认 §6 后转 `Accepted`，同步更新 `docs/design/07-execution-protocol.md`、`docs/design/2026-11-24-list-pagination-and-facets-contract.md`（§5.1）与 `docs/design/2026-semantic-ownership.md`。
2. **阶段 2（统一底座 + A 类投影先行）**：落地 `backend/agent/contracts/` 统一 `FinishingGuard`、补齐 ADR-0043 全 Run 首次时钟保留与多主体强度取高、收编 B6 `disarm_guard` 为 `teardown_disarm`，上线控制面全量 `ui_status` / Minimap 七字段 `job_exec_status="finishing"` 投影与前端统一展示（开关机 / Monkey / GPU / 休眠 / MTBF 立即获得「收尾中」可视性）。
3. **阶段 3（B 类 `atomic_commit` + 租约有效互斥激活 + 全进程树停止门禁 + `force=true` 逃生阀 + 刷机接入）**：先发控制面（API + 预登记/激活 + 准入门禁行 + 统一归还与停止门禁事务 + reaper/recycler + recovery）与 Agent（`boot_id_source` + 仲裁点 + `request_id` + 激活与本地租约复核后放行 + 自 `grant` 持续取证 + watchdog + outbox + 优雅停机 CAS 与全进程树约束/停止核验），确认机队版本齐备后迁移刷机模板 `atomic_group` 并发布支持 `critical_section_guard` 握手的 `flash_firmware_unisoc_bsl` / `flash_firmware` 新版本。

---

## 6. 裁决记录与待 Owner 确认项

### 已裁决（2026-11-26，Owner）
1. **过渡态名称与统一集合模型**：合并为单一 **`收尾中`（`finishing`）**，作为一等集合概念纳入模型，下设 `teardown_disarm`（A 类）与 `atomic_commit`（B 类）两个正交子类型，统一抽成 `FinishingGuard` 而不在 B6 上打补丁。
2. **单台刷机流程整体语义与终态口径**：将单台纯刷机流程视为原子整体；获批进入临界区后收到普通 `abort` 时跑完本机闭环，实证成功标 `COMPLETED`，任一步失败或超期标 `FAILED`（混合 Plan 存在未执行 `patrol` 时跳过 `patrol` 并标 `ABORTED`）。

### 本稿建议收口项（待 Owner 在转 `Accepted` 时最终拍板）
1. **采纳两层显式冻结全必需 `atomic_group`（原选项乙，已写入 D3 第 7–13 条）**：派发前在不可变 `plan_snapshot` 声明静态全必需成员，VBUS 刷后 `return_check` 由 Agent 预登记前展开、控制面校验后写入 `JobInstance.finishing_guard`（保留 ADR-0063 D5.3 失败跳过 teardown），避免默认吞并全部剩余 `init` 导致误跑第二段刷机或无关足迹建立步骤。
2. **Minimap 保持七字段并扩展 `job_exec_status`（已写入 D1 第 4、13、18 条）**：同批修订 `docs/design/2026-11-24-list-pagination-and-facets-contract.md` §5.1，复用 `job_exec_status="finishing"`，子类型明细由设备分页表与 Job 详情提供。
3. **按 ADR-0058 批次拆分交付（已写入 §5）**：独立复核通过并由 Owner 裁定转 `Accepted` 后，按 ADR-0058 建立批次载体 issue 推进实施与协调激活。

---

## 7. 关联实现与文档

- `backend/agent/identity.py`（`read_boot_id` 与内核真值/回退区分）与 `backend/agent/heartbeat.py`
- `backend/agent/pipeline_engine.py`（`PipelineEngine.cancel`、`_process_group_alive`、`_await_tree_exit`、进程读线程与 watchdog、VBUS 刷后 `return_check`、`INIT→PATROL`、跳过分支）
- `backend/agent/scripts/flash_firmware/flash_firmware.py`（`start_new_session=True` 子工具会话）
- `backend/agent/device_return.py`（VBUS `return_check` 展开）与 `backend/agent/lease_renewer.py`（`job_not_running` / `lease_missing` 续租失效）
- `backend/agent/api_client.py`（`/complete` 载荷）与 `backend/agent/graceful_shutdown.py`（停机结算与 outbox）
- `backend/core/job_timeout_config.py`（`abort_reap_clock` 主体时钟）
- `backend/services/plan_run_abort.py`（`abort_plan_run`、`JobInstance → PlanRun FOR NO KEY UPDATE` 锁序、主体时钟写入、候选筛选与 SocketIO 下发）
- `backend/services/job_terminalization.py`（终态待聚合写入与外键 `FOR KEY SHARE`）
- `backend/services/agent_lease_extend.py` 与 `backend/services/agent_recovery.py`（运行中租约有效期校验、重连恢复 `ABORT_LOCAL`）
- `backend/services/agent_completion.py` 与 `backend/services/device_return.py`（终态重放/迟到确认、同步与异步统一归还租约入口、人工放行与欠账关闭）
- `backend/scheduler/device_lease_reconciler.py` 与 `backend/scheduler/recycler.py`（`UNKNOWN` 回收与行锁复读）
- `backend/services/plan_run_devices.py`、`backend/services/plan_run_catalog.py`、`backend/services/host_upgrade_gate.py`
- `docs/design/07-execution-protocol.md`（§3、§5、§9.2、§9.5、B6 兼容契约）
- `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（§5.1 Minimap 七字段契约）
- `docs/adr/ADR-0061-device-return-contract.md` 与 `docs/adr/ADR-0063-vbus-cycle-flash-without-adb.md`
