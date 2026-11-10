# vbus 刷机作业的派发与认领放行（#3892 U4）

Status: implemented
Class: feature

## Decision

规格是 [ADR-0063](../../adr/ADR-0063-vbus-cycle-flash-without-adb.md) D4 第 1–6 条。

- 新增 `backend/services/vbus_flash_admission.py`，判定只认一种情况：`lifecycle.init[0]` 为
  `script:flash_firmware`，且参数 `trigger_mode == "vbus_cycle"`。派发侧用 Plan 快照判定，认领侧用作业的
  `pipeline_def` 判定，两边共用这一个函数。快照无法物化时一律按「否」处理（不放行），不抛异常，
  因此不改变准入泵原有的报错次序。
- `_classify_dispatch_devices_sync` / `_validate_dispatch_devices_sync` 新增参数 `allow_device_offline`。
  它只跳过 `device_offline` 这一级，后面各级（`device_error`、`host_offline`、维护窗、欠账、租约、作业）照常判定。
  有三个调用点：
  - `prepare_plan_run`：vbus 计划不把 `device_offline` 记为排队理由，按刚建好的冻结快照判定（`is_vbus_flash_snapshot`）；
  - `complete_plan_run_dispatch`；
  - 准入泵终检 `admission_pump.admission_transaction`。
- 认领：
  - `ClaimRequest.vbus_slots` 取 0–1，缺省 0；
  - vbus 刷机作业不论设备在线与否，只走 vbus 名额。普通名额先按判定函数滤掉 vbus 作业，再按 `capacity` 截断，
    所以 vbus 作业既不会被普通名额认领，也不会挤占普通名额（复核 #3901 P1-2：守卫原本只在 vbus 路径，普通路径可绕过）；
  - `vbus_slots > 0` 时，本 host 上未退役、非 ERROR 且 `adb_state` 不是 `unauthorized`
    （复核 #3899 指出：健康过滤之外 unauthorized 设备原本会成为候选；ADR-0063 §8 第 4 项裁定排除）、无 ACTIVE 租约、且最早 PENDING 作业是
    vbus 刷机的设备，另行认领。这部分最多 `vbus_slots` 个，不占 `capacity`。锁内复核判定，并使用 `FOR UPDATE SKIP LOCKED`；
  - 本 host 已有 RUNNING 的 vbus 作业时，不再发新的 vbus 作业（单主机严格串行），避免后续作业在脚本主机锁上空耗步骤超时；
    2026-11-10 订正：只计仍在刷机的作业，`execution_state` 为 `WAITING_BARRIER` / `PATROL_SLEEP` 的不算
    （阶段 B 真机验收 PlanRun 1242：A 刷完停在 INIT→PATROL barrier 等 B，守卫把 A 算作在跑，B 永远认领不到，
    两边等到 barrier 超时）。子状态为空（刚认领、首个心跳未到）仍算在跑；
  - 旧 Agent（不带 `vbus_slots`）认领不到任何 vbus 作业，作业保持 PENDING（ADR-0063 D6 第 4 条）。
- Agent（新增 `backend/agent/usb_power_slots.py`）：
  - 存在 `/etc/stp-agent/usb-power-slots.json` 时 `vbus_slots=1`，此时即使 `effective_slots ≤ 0` 也照样调用认领，`capacity` 传 0；
  - 映射文件不存在时，请求体与旧版逐字一致（不带该键）；
  - 路径与 wrapper 常量同源（测试锁定）。
  - 2026-11-10 新增（ADR-0063 D5 第 3 条，Owner 选甲）：vbus 刷机作业的 init 残留检查（ADR-0061 D6 第 1 条）推迟到
    第一个步骤（vbus 刷机）成功之后、其余 init 步骤之前。阶段 B 真机验收 PlanRun 1242：设备 B 派发时已关机，adb 探针
    等满 600s 后 `init_return_check:failed` 并记成欠账，永远到不了刷机步骤。判据移到共享契约
    `backend/agent/contracts/vbus_flash.py`，控制面 `vbus_flash_admission` 改为引用，两侧不再各写一份。
    刷机已启动且失败时不做检查、照常跑本 job 的 teardown；推迟的检查失败时不跑本 job 的 teardown，与先查后刷的作业一致。
    复核 #3915 第 1 轮订正：「刷机已启动」按 `_execute_step` 的 STARTED 边界计数（`_init_steps_started`），启动前丢租约
    不跑 teardown（P1）；检查期间 teardown 门保持关闭，检查抛异常时同样不跑（P2）。
    第 2–3 轮订正：保护放在 `_execute_step` 发出 STARTED 与计数之前（第 2 轮放在许可交接后、状态写入前，
    第 3 轮复核指出协调器写入 `EXECUTING_STEP` 时丢租约仍会漏过）。已知丢租约或已取消时 vbus 刷机步骤返回
    `not_executed:lease_lost`，不发 STARTED、不启动刷机，终态 abort，teardown SKIPPED。
    本项改了 Agent 代码输入集，期望 digest 会变，必须随一次 Agent 发布滚动。
- 依赖 U2（wrapper 常量）：本 PR 叠在 U2 分支上。
- 发布依赖：本单元改了 Agent 代码输入集（`api_client.py`、`claim_loop.py`、`usb_power_slots.py`），合入后期望 digest 会变。
  Agent 部分必须随控制面同批，在 B8 的联合 Agent 发布（G4 / #3875 / #3889）截止前合入；赶不上就顺延到下一次全队滚动，
  不得在切窗后单独合入（ADR-0063 D6 第 1 条；#3875 digest 守卫会把对不上的主机记为 `capability_unverified`）。

- 2026-11-10 #3918 U5a（ADR-0063 v1.1 D7）：Agent 按映射逐口读 sysfs `idVendor`/`idProduct`，上报 `capacity.usb_power_slots`
  （`{serial: [slot, vid_pid|null]}`）；分类函数放共享契约 `contracts/usb_presence.py`，供控制面与前端共用。映射宽松读取，
  任何格式问题都不上报，心跳照常；权威校验仍在 wrapper。只有带映射文件的主机多出这个键（16 台约 700 字节），其余主机心跳不变。
  改了 Agent 代码输入集，期望 digest 会变，必须随全机队 Agent 发布。

- 2026-11-10 #3918 U5b（ADR-0063 D7 第 5、6 条）：`DeviceOut.usb_presence` 从所属主机 `host.extra.capacity.usb_power_slots`
  按 serial 现算（`backend/services/vbus_usb_presence.py`），不落库；`PlanOut.is_vbus_flash` 用 `is_vbus_flash_plan_steps`
  （已启用 init 步骤排序后交给 D4 同一判定函数，只读步骤自身 params）。设备列表的陈旧隐藏豁免在线主机报告为关机态的映射设备。
  前端：设备页在「状态」格加 USB 徽标（没有新增列，表宽不变）；派发页在 vbus 计划下用 `markVbusSelectable` 给关机候选打
  `vbus_selectable`，`isSchedulable` 与就绪度据此放行，其余调用点不改。unauthorized 在 vbus 计划下仍阻断。

## Alternatives

- 只改控制面准入：不可行。设备全部关机的主机上 `effective_slots` 恒为 0，Agent 不会调用认领（ADR-0063 F11）。
- Agent 把 vbus 作业算进 `capacity`：会让健康闸门（所有 ADB 都不可用 → 0）失去意义，也会让普通作业借这个名额被认领。
- 认领侧按设备 `lsusb` 物理在线判定：lsusb 只计数，不对应设备行，没有可用的判据。
- 派发侧加全局开关：ADR-0063 §3 已否决。

## Verification

云端运行，本地 PostgreSQL 16 由 `TEST_DATABASE_URL` 指向。云端没有带 swap 上限的 cgroup，
`scripts/run_pytest.py` 因此 fail-closed，测试改用 `.venv/bin/python -m pytest` 直接跑。

- `backend/tests/services/test_vbus_flash_admission_3892.py`（17 条）：判定矩阵；放行只跳过一级（之后 `host_offline` 照报）；
  `device_retired` 仍按致命处理；vbus 计划经 prepare 和准入泵后物化出作业；非 vbus 计划照旧按 `device_offline` 回队；
  `complete_plan_run_dispatch` 对 vbus 计划放行、对非 vbus 计划转 FAILED（把该处放行改成 False 后用例变红）。
- `backend/tests/services/test_vbus_flash_claim_3892.py`（16 条）：旧 Agent 认领不到；`capacity=0` 时也能认领；
  不占普通名额；最早作业不是 vbus 的不认领；退役、ACTIVE 租约、unauthorized（ERROR）不放行；同一 host 在跑时不发第二个；
  在线设备上的 vbus 作业只走 vbus 名额、不挤占普通名额、两条也只认领一条，普通路径绕不过「在跑即不发」
  （去掉普通名额的 vbus 过滤后 4 条变红）；
  「同一设备较早非 vbus + 较晚 vbus」混合队列被 `uq_job_active_per_device` 结构性排除（断言插入失败）。
  变异：去掉 ERROR / unauthorized 过滤后 2 条变红。
- `backend/agent/tests/test_vbus_claim_slots_3892.py`（5 条）：名额跟随映射文件；路径与 wrapper 同源；
  槽位为 0 时也认领；无名额时请求体不变。
- 变异检查：去掉准入泵的放行、或去掉「在跑即不发」守卫后，对应用例变红。
- 复核 #3901 第 2 轮（review 5812783034，通过）后：prepare 改按快照判定（改成 False 后 1 条变红）；ERROR 与 unauthorized
  拆成各自独立的用例（单删任一排除条件都有 1 条变红）。三个目标文件 38 passed。
- 复核 #3901 后：`pytest backend/tests/services/ -k "claim or admission or dispatcher"` 223 passed。
- 相邻回归：claim、派发设备校验、准入队列 step2–4、`plan_dispatcher`、`agent_dual_write`、Agent 全量。
  Agent 全量 3079 passed，3 failed；这 3 条在 `origin/main` 同样失败（`test_aee_processor` 两条、进程组回收一条），与本改动无关。
- ruff、import-linter（5 kept）、`check_inner_imports`（610 ≤ 610）、`check_god_files_ceiling` 均通过。

- 2026-11-10 barrier 死锁订正：新增 `test_vbus_job_past_flash_does_not_block_next_device`（WAITING_BARRIER / PATROL_SLEEP）
  与 `test_vbus_job_still_flashing_blocks_next_device`（空 / EXECUTING_STEP / WAITING_EXECUTION_SLOT）；
  claim 文件 21 passed。变异：去掉子状态过滤，2 条变红；把 EXECUTING_STEP 也当空闲，1 条变红。
  遗漏原因：单测只按 Job.status 构造 RUNNING，没有覆盖多步 Plan 在 barrier 等待时仍为 RUNNING 的形态。
- 2026-11-10 init 残留检查推迟：`backend/agent/tests/test_vbus_init_check_order_3892.py` 钉住判据矩阵、
  两条路径的执行顺序与 teardown 归属。变异：取消推迟，3 条变红；刷机失败时不认领 teardown，1 条变红。
  复核 #3915 第 1 轮后增至 15 条：启动前丢租约（mock 与真实 `_execute_step` 各一条）、检查抛 `sqlite3.OperationalError`、
  真实步骤路径的「已启动」计数接线；teardown 为非空。变异：恢复「刷机后无条件开门」，3 条变红；去掉 STARTED 计数，1 条变红。
  第 2 轮后增至 17 条：真实 OperationScheduler 在许可交接后注入丢租约 / 取消（参数化 2 条）。
  第 3 轮后增至 19 条：在写入 `EXECUTING_STEP` 时注入丢租约 / 取消，断言无 STARTED、`abort / SKIPPED`（参数化 2 条；
  在第 2 轮提交 `4ce669e` 上 2 条均失败）。变异：去掉 STARTED 前保护，4 条变红。
  相邻回归：`test_return_init_u4_3729`、`test_lifecycle_engine`、`test_return_debts_u4_3729`、import 边界（`tests/test_agent_import_boundary.py`）、
  控制面 vbus 准入与认领两个文件均通过；Agent 全量 3169 passed、3 failed，这 3 条在 `origin/main` 同样失败（同上）。
  遗漏原因：ADR-0063 起草时只核对了派发与认领两道准入，没有核对每个 job 在 init 之前都会跑的 ADR-0061 残留检查。

- 2026-11-10 #3918 U5a：`backend/agent/tests/test_usb_power_presence_3918.py` 18 条（分类 6 条、逐口读取、无映射、
  5 种格式错误、sysfs 值非法、capacity 键只在有映射时出现且不改槽位、心跳 tick 透传 / 读取抛异常不影响心跳）。

- 2026-11-10 #3918 U5b：`backend/tests/api/test_vbus_usb_presence_3918.py` 10 条（serial 匹配、四态、无映射与他机为 null、
  详情接口、陈旧豁免只对在线主机的关机态生效、Plan 步骤判定与派发形态一致、`_plan_out` 透传）；前端
  `planExecuteReadiness.vbus3918.test.ts` 5 条与 `ExpandableDeviceTable.test.tsx` 徽标 1 条；相关 vitest 258 + 16 passed，`tsc --noEmit` 通过。

## Revisit

- 第二期槽位入库后，Agent 的 vbus 名额改由控制面按主机能力判定，不再看本地文件。
