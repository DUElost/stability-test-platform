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
  - `prepare_plan_run`：vbus 计划不把 `device_offline` 记为排队理由；
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
  - 旧 Agent（不带 `vbus_slots`）认领不到任何 vbus 作业，作业保持 PENDING（ADR-0063 D6 第 4 条）。
- Agent（新增 `backend/agent/usb_power_slots.py`）：
  - 存在 `/etc/stp-agent/usb-power-slots.json` 时 `vbus_slots=1`，此时即使 `effective_slots ≤ 0` 也照样调用认领，`capacity` 传 0；
  - 映射文件不存在时，请求体与旧版逐字一致（不带该键）；
  - 路径与 wrapper 常量同源（测试锁定）。
- 依赖 U2（wrapper 常量）：本 PR 叠在 U2 分支上。
- 发布依赖：本单元改了 Agent 代码输入集（`api_client.py`、`claim_loop.py`、`usb_power_slots.py`），合入后期望 digest 会变。
  Agent 部分必须随控制面同批，在 B8 的联合 Agent 发布（G4 / #3875 / #3889）截止前合入；赶不上就顺延到下一次全队滚动，
  不得在切窗后单独合入（ADR-0063 D6 第 1 条；#3875 digest 守卫会把对不上的主机记为 `capability_unverified`）。

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
- `backend/tests/services/test_vbus_flash_claim_3892.py`（14 条）：旧 Agent 认领不到；`capacity=0` 时也能认领；
  不占普通名额；最早作业不是 vbus 的不认领；退役、ACTIVE 租约、unauthorized（ERROR）不放行；同一 host 在跑时不发第二个；
  在线设备上的 vbus 作业只走 vbus 名额、不挤占普通名额、两条也只认领一条，普通路径绕不过「在跑即不发」
  （去掉普通名额的 vbus 过滤后 4 条变红）；
  「同一设备较早非 vbus + 较晚 vbus」混合队列被 `uq_job_active_per_device` 结构性排除（断言插入失败）。
  变异：去掉 ERROR / unauthorized 过滤后 2 条变红。
- `backend/agent/tests/test_vbus_claim_slots_3892.py`（5 条）：名额跟随映射文件；路径与 wrapper 同源；
  槽位为 0 时也认领；无名额时请求体不变。
- 变异检查：去掉准入泵的放行、或去掉「在跑即不发」守卫后，对应用例变红。
- 复核 #3901 后：`pytest backend/tests/services/ -k "claim or admission or dispatcher"` 223 passed。
- 相邻回归：claim、派发设备校验、准入队列 step2–4、`plan_dispatcher`、`agent_dual_write`、Agent 全量。
  Agent 全量 3079 passed，3 failed；这 3 条在 `origin/main` 同样失败（`test_aee_processor` 两条、进程组回收一条），与本改动无关。
- ruff、import-linter（5 kept）、`check_inner_imports`（610 ≤ 610）、`check_god_files_ceiling` 均通过。

## Revisit

- 第二期槽位入库后，Agent 的 vbus 名额改由控制面按主机能力判定，不再看本地文件。
