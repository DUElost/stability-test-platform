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

- 2026-11-10 `flash_firmware@1.4.1`（15.93 单主机多台试点）：机型族路由表补 `MLD_NX3` / `MLD-NX3` → `MLD`。
  vbus 刷机的机型只来自步骤参数 `model`，未列机型在 1.4.0 上 fail-fast（no firmware family route），NX3 无法派发。
  只改路由表与抬头注释，其余逻辑与 1.4.0 一致；已按 ADR-0051 登记新版本，1.4.0 条目不动。
  工具代际（v5 / v6）仍由版本目录 `manifest.json` 的 `tool_generation` 决定，路由不改变代际判定。
  同一版本（未发布前追加）还放开 vbus 的 V5：552 机型只能用 flashtool（Owner 2026-11-10），15.93 实测 V5 `-p` 在 Linux 上按
  sysfs 口名过滤并能完成刷机（#3892 comment 6764214206）。V5 在 vbus 下带 `-p <槽位口>`，绑定与 `brom` 门控缺省开启；
  V5 回显 `com portName is: /dev/ttyACMn` 经 `/sys/class/tty` 解析成 sysfs 口后接进既有的逐行核对。非 vbus 的 V5 命令行不变
  （`_build_flash_argv` 只认显式的 `v5_port`，原有「V5 不长出 -p」测试照常通过）。ADR-0063 升 v1.1 订正 D3 第 4 条。
- 2026-11-10 #3918 U5a（ADR-0063 v1.2 D7）：Agent 按映射逐口读 sysfs `idVendor`/`idProduct`，上报 `capacity.usb_power_slots`
  （`{serial: [slot, vid_pid|null]}`）；分类函数放共享契约 `contracts/usb_presence.py`，供控制面与前端共用。映射宽松读取，
  任何格式问题都不上报，心跳照常；权威校验仍在 wrapper。只有带映射文件的主机多出这个键（16 台约 700 字节），其余主机心跳不变。
  改了 Agent 代码输入集，期望 digest 会变，必须随全机队 Agent 发布。

- 2026-11-10 #3918 U5b（ADR-0063 D7 第 5、6 条）：`DeviceOut.usb_presence` 从所属主机 `host.extra.capacity.usb_power_slots`
  按 serial 现算（`backend/services/vbus_usb_presence.py`），不落库；`PlanOut.is_vbus_flash` 用 `is_vbus_flash_plan_steps`
  （已启用 init 步骤排序后交给 D4 同一判定函数，只读步骤自身 params）。设备列表的陈旧隐藏豁免在线主机报告为关机态的映射设备。
  前端：设备页在「状态」格加 USB 徽标（没有新增列，表宽不变）；派发页在 vbus 计划下用 `markVbusSelectable` 给关机候选打
  `vbus_selectable`，`isSchedulable` 与就绪度据此放行，其余调用点不改。unauthorized 在 vbus 计划下仍阻断。
- 2026-11-10 `flash_firmware` 1.4.2（ADR-0063 v1.3 D3 第 4 条，Owner 在决策卡上选方案 1）：15.93 LX3 金丝雀 PlanRun 1255 中，
  V5 工具接管道时块缓冲，`com portName is: /dev/ttyACM0` 到达时节点已消失，1.4.1 按「解析不出口」止损，未写入固件。
  1.4.2 让 V5 vbus 经 `stdbuf -oL -eL` 启动工具（现场实测回显 6.22s 到达，节点 8.89s 消失），取锁前找不到 `stdbuf` 即失败；
  每次尝试起采样线程（约 0.1s）记录 ttyACM 节点 → sysfs 口，上电前清空。`_check_bound_echo` 把实时结果与记录合并，恰好一个口
  才可以用，没有或多于一个都止损。每行仍只查一次实时 sysfs。V6 与非 vbus 的 V5 命令行不变。尝试记录多一个 `tty_history` 键，便于现场核对。
  复核 #3924 第 1 轮（第 1、3、4 项需修改）后：每行先查实时再取快照；`reset()` 移到 wrapper 上电返回之后；每轮建上下文，回调以缺省参数
  绑定本轮上下文，上下文已换即不写状态；工具退出后 `stop()` 冻结记录并返回线程是否已停，`_final_v5_binding_reason` 按本轮回显
  （实时 ∪ 回显到达前开始的采样）重算，线程未停即判不可信；`_settle_lock` 先停采样再隔离。尝试记录增加 `tty_sampler_stopped`。
  复核 #3924 第 2 轮（第 1、3、4 项需修改）后：`_run_flash_tool_with_progress` 增加可选 `readers_status`，join 宽限后 reader 仍存活即
  `stuck=True`，最终判定据此判不可信（第二条回显卡在核对里时不能只凭第一条判成功）；`reset()` 放回调用 wrapper 之前，调用期间的采样保留——
  第 1 轮把它移到返回之后，会丢掉通电早于返回时目标口唯一的记录；调用期间邻口用过同名节点按歧义止损，这是有意的失败闭合（工具已在扫描）。
  `stop()` 不再取记录锁（信号处理器可能打断持锁主线程）、重复调用不再等待；采样线程 `start()` 放进 try，起不来按启动失败处理。
  测试夹具把门控状态文件改到 `tmp_path`，不再删除真实的 `/tmp/stp-flash-firmware.gated.json`。
  复核 #3924 第 3 轮按 ADR-0058 D11 第 3 条退回规划者（两轮连续出现「回显漏核」类新发现）。规划者裁定纳入有限清单而非登记已知限制，
  ADR-0063 v1.3 D3 第 4 条与 §5 第 2 项补写「输出完整消费」与「信号打断停止流程」两条判据。实现：`readers_status` 在提供时让 Popen 以
  `errors="replace"` 解码，并记录 `eof`（两个 reader 的 for 循环都自然结束）与 `errors`（读取异常、核对回调异常）；最终判定在 stuck、
  非 eof、有 errors 任一时判不可信。采样器停止改为布尔量 `_halt`，采样线程用一个无人 set 的 `_tick` 等待间隔，`stop()` 不再调用 `Event.set()`。
  复核 #3924 第 4 轮仍有两处：解析器只取每行第一处回显、非法字节落在节点名里时整条跳过；正常结算已置位闸门后收到信号，处理器直接返回、进程终止，
  隔离未执行。规划者再次修订 D3 第 4 条（「回显逐次核对」与「结算按步骤完成」），不再逐个补反例：vbus 下改由 `_check_vbus_bind_line` 统计
  一行里所有绑定标记，要求与严格解析出的回显个数相等并逐一核对；带端口 / tty 字样的行里出现替换字符即止损；`_settle_lock` 拆成
  停采样、隔离、恢复门控、释放锁四步，各自记完成，重入从未完成的一步继续。
  复核 #3924 第 5 轮用 P2 完整日志确认第 4 轮的标记 `port\s*name` 误杀 V5 的 `path name(/dev/ttyACM0), port name(/dev/ttyACM0)` 行；
  另有 `/dev/ttyACM0-neighbor` 只核前缀、标记与严格解析大小写规则不一致、信号重入时在途 wrapper 断电或 `authorized=1` 越过锁释放三处。
  规划者订正 D3 第 4 条：标记只认 `portName is` 与 `symbolic_link`，两侧都不区分大小写；V5 下行内每一处 `/dev/ttyACM` 引用都必须是完整节点名并解析到槽位口。
  新增「信号临界区」：`_usb_power` 的 wrapper 调用、`_set_authorized` 与 `_settle_lock` 包在 `_SignalCritical` 内，信号落在其中时处理器只记下、
  最外层退出时用 `os.kill` 重新投递，再执行结算。这样跨线程也成立（reader 线程里的重新门控）。
  复核 #3924 第 6 轮按 D11.3 再次退回规划者：分隔符（`;` `@` `,` `:`）仍能绕过完整节点核对；V6 `SYMBOLIC_LINK:` 计为标记却解析不出；
  信号临界区在计数锁内被打断时自锁，登记与投递的交接会丢信号，且 wrapper 在 SIGTERM 后继续运行时 Agent 8 秒强杀先于结算。
  规划者不再按反例逐个补黑名单：tty 引用改为白名单形态（数字之后只能是行尾、空白或 `)`）；每类标记的计数与解析共用不区分大小写的正则；
  信号处理器不延后、不取锁，先置位「结算中」，再最多等 3 秒让在途特权操作结束（非主线程看 `_INFLIGHT` 计数，主线程的在途 wrapper
  看 /proc 里本进程的 wrapper 子进程），然后结算；整个信号结算 7 秒内完成，子进程超时截到剩余时间内。排空超时的残余由 Owner 裁定登记为
  已知限制：保留门控状态文件交给下次启动的孤儿恢复，槽位供电由下次 vbus 作业重新上电。
  复核 #3924 第 7 轮第三次退回规划者：回显判据可按有限输入验收，但恢复判据没有闭合——没写清受影响的口何时落盘、下次恢复时旧写入
  是否已结束（复现：下次启动先恢复并删记录，随后旧 `authorized=0` 落地），预算耗尽没有处置规则（`_signal_timeout` 到期仍给 0.1 秒），
  主线程写完 0、未登记前收到信号会漏恢复，`fork`/`exec` 窗口里 wrapper 子进程还没有 wrapper 命令行，V5 遇带目标口的 `symbolic_link` 仍报成功。
  规划者把恢复正确性从排空中拿出来：写 0 之前先落盘（`_gate_other_mtk` 的 `before_write` 回调，记录只增不减，结算按并集恢复）；
  信号路径不删状态文件；孤儿恢复从抢锁之前移到持锁之后，并先等本机 `usb-power` / `usb-authorized` wrapper 全部结束（≤80 秒，超时不刷）；
  剩余预算 <0.5 秒时 `_Privileged` 拒绝一切特权操作；V5 出现 `symbolic_link` 即止损。旧顺序（抢锁之前恢复孤儿门控）还会撤掉正在持锁
  刷机的另一作业的门控，一并修正。
  复核 #3924 第 8 轮第四次退回规划者（正常结算后 reader 迟到写 0、wrapper 退出后 `uhubctl` 孙进程仍在跑、落盘失败仍写 0、
  恢复失败仍删记录、预算只查入口）。规划者判断：在途特权操作与门控恢复的时序是 1.4.1 起就有的问题（#1025、#1591），不是 1.4.2 的回归，
  继续在本 PR 内补判据会持续推迟 V5 回显修复，且关键前提（`/proc` 挂载、`uhubctl` 进程树）需要现场验证。Owner 2026-11-11 在决策卡上选拆分：
  `flash_firmware.py` 以第 4 轮 `cef0db0` 为底，只移植第 5–8 轮的回显解析改动（`portName is` 标记、tty 引用白名单、标记同源、V5 遇
  `symbolic_link` 止损）；信号临界区、在途排空、先落盘、持锁后恢复、预算全部撤回，对应测试删除，旧版本测试的 `before_write` 替身改回。
  ADR-0063 D3 第 4 条改为「范围说明」，反例与设计问题记入 #3933。

- 2026-11-11 `flash_firmware` 1.4.3（#3892）：1.4.2 金丝雀 PlanRun 1265 / Job 206967 仍在 V5 绑定口核对处止损
  （`tty_history={}`、`cannot be resolved`）。根因是 `_tty_usb_port` 用「路径段含 `:`」判断接口边界；
  真实 x86 sysfs 路径在 USB 端口之前有 `pci0000:00` / `0000:00:14.0` 两个含冒号的 PCI 段，循环在第一个
  就 break，实时核对与采样历史都解析不出口。1.4.3 只把 `<端口>:<配置>.<接口>` 形态当作接口边界，
  PCI 段跳过；不改变 1.4.1/1.4.2 的失败闭合语义与命令行。新增 `test_flash_firmware_v143.py`。

## Alternatives

- 只改控制面准入：不可行。设备全部关机的主机上 `effective_slots` 恒为 0，Agent 不会调用认领（ADR-0063 F11）。
- Agent 把 vbus 作业算进 `capacity`：会让健康闸门（所有 ADB 都不可用 → 0）失去意义，也会让普通作业借这个名额被认领。
- 认领侧按设备 `lsusb` 物理在线判定：lsusb 只计数，不对应设备行，没有可用的判据。
- 派发侧加全局开关：ADR-0063 §3 已否决。
- 1.4.2 只做行缓冲、不记历史：节点只存在约 2.7s，工具一旦换用自己的缓冲或输出变慢就会再次失败，记录历史是低成本的兜底。
- 1.4.2 用 PTY 代替 `stdbuf`：要改动现有 reader 线程与退出判定，改动面更大；`stdbuf` 已在现场实测可用。
- 1.4.3 改成扫描整条路径取最后一个端口形态段：同样能修，但会把「接口之后恰好又出现端口形态段」的异常路径也当合法；保留「接口目录之前最后一个端口段」的原语义更小。
- 1.4.3 改成只认 `usb\d+/` 之后的段：依赖 sysfs 层级命名，换平台/驱动形态更脆；接口正则与 `symbolic_link` 尾段同源。
- 站点临时包装工具目录（Owner 方案 2）：绕过包发布，Plan 显式指向站点目录，难以审计；Owner 选了产品修复。

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

- 2026-11-10 1.4.1：`backend/agent/tests/test_flash_firmware_v141.py` 2 条（NX3 两种写法路由到 MLD；既有 10 个键不变）；
  与 `test_flash_firmware_v1319.py` 合计 34 passed。`check_script_packages`：族树与最新登记等价。
  V5 vbus 追加后 `test_flash_firmware_v141.py` 共 7 条（V5 `-p` 只经 `v5_port`、tty 解析、V5 回显核对、vbus V5 带槽位口与
  `brom` 门控、关绑定仍拒绝）；`-k flash_firmware` 全部 254 passed。
  复核 #3917 第 1 轮后增至 16 条：V5 回显 tty 解析不出口即止损；非 vbus 不认 V5 回显；vbus V5 缺少可信绑定（从未回显、
  tty 解析不出、回显邻机口）三种都判失败、不重试、断电隔离；`gate_pid_scope` 取 full（参数 / 环境变量）与拼错值时，
  vbus 初始门控与重试门控仍为 brom（V5 三种、V6 环境变量一种）。变异：去掉「从未回显即失败」1 条变红；去掉 brom 强制
  4 条变红；去掉「解析不出即止损」2 条变红；V5 回显规则不限 vbus 1 条变红。`-k flash` 共 329 passed。
  复核 #3917 第 2 轮（B1-R2）后增至 20 条：每行回显的首次 tty 查询分别返回邻口、None、邻口，之后恢复为槽位口，三种都必须
  立即按错口止损、不重试、断电隔离，且每行只查一次 tty；三条在 `685848d` 上全部失败。遗漏原因：上一轮用例只用固定 tty 映射。
- 2026-11-10 #3918 U5a：`backend/agent/tests/test_usb_power_presence_3918.py` 18 条（分类 6 条、逐口读取、无映射、
  5 种格式错误、sysfs 值非法、capacity 键只在有映射时出现且不改槽位、心跳 tick 透传 / 读取抛异常不影响心跳）。
  复核 #3919 第 1 轮后读取器改为与 wrapper `parse_usb_power_slots` 同一套 D1 约束（去掉 64 条上限，加 64 KiB 文件上限），增至 41 条：
  复核给的 8 种 wrapper 拒绝样例（同口两 serial、重复 serial、端口 17、`schema: true`、条目多余字段、空白 serial 等）整份不上报；
  5 个 hub 共 65 槽全部上报；超 64 KiB 不上报；12 种输入与 wrapper 解析器差分一致；常量与 wrapper 同值。在第 1 轮提交上 23 条失败。
  复核 #3919 第 2 轮后改按原始字节判上限（文本模式会把 CRLF 折成 LF），增至 44 条：含 CRLF 的 65,536 / 65,537 字节文件
  与 wrapper 真实加载函数 `_load_usb_power_slots` 同接受同拒绝；非法 UTF-8 不上报。在第 2 轮提交上 1 条失败。

- 2026-11-10 #3918 U5b：`backend/tests/api/test_vbus_usb_presence_3918.py` 10 条（serial 匹配、四态、无映射与他机为 null、
  详情接口、陈旧豁免只对在线主机的关机态生效、Plan 步骤判定与派发形态一致、`_plan_out` 透传）；前端
  `planExecuteReadiness.vbus3918.test.ts` 5 条与 `ExpandableDeviceTable.test.tsx` 徽标 1 条；相关 vitest 258 + 16 passed，`tsc --noEmit` 通过。
  复核 #3921 第 1 轮后：新增 `host_is_live`（ONLINE 且心跳未超过 `HOST_HEARTBEAT_TIMEOUT_SECONDS`），徽标 `host_online` 与陈旧豁免共用；
  后端增至 12 条（状态 ONLINE、心跳过期两小时的主机：徽标为 false、列表与分页都不放出 30 天未见设备；阈值边界与 naive 时间），
  换回旧实现 2 条失败。vbus 候选判定加欠账拦截（任一专项 `debt`），前端专项增至 6 条。

- 2026-11-10 `flash_firmware` 1.4.2：`backend/agent/tests/test_flash_firmware_v142.py` 14 条（实时与历史合并 6 种：节点消失只剩目标口通过、
  无口、邻口、同名两个口、实时邻口而历史目标口；采样器记住已消失的节点、reset 清空、跨 reset 的采样丢弃、线程采样；端到端：回显晚于节点消失
  仍通过、上电前邻口记录不计入、回显晚到且是邻口则止损不重试并隔离、缺 `stdbuf` 时零切电零取锁、V6 命令行不变）。v1.4.1 测试的 V5 假件
  固定 `stdbuf` 路径与空 tty 列表，命令行断言改为 `stdbuf -oL -eL` 前缀。`-k flash` 共 347 passed。
  复核 #3924 第 1 轮后增至 21 条，新增 7 条对应复核反例：实时查询期间写入邻口、逐行核对后才落地的在途邻口、上一轮超时遗留回调、
  wrapper 通电过程中采到邻口、采样阻塞时 `stop` 返回 False 且记录冻结、采样未停判失败、信号结算先停采样。7 条在 `80f1ba7` 上全部失败；
  7 种变异（去遗留回调守卫、先快照后实时、不冻结、结算不停采样、忽略未停、最终判定不计历史、reset 移回上电前）各至少 1 条变红。
  复核 #3924 第 2 轮后增至 30 条，新增 10 条在 `0fae3c1` 上失败（另 1 条遗留回调压掉 BROM 超时的用例补的是第 1 轮守卫的可观测效果）。
  7 种变异（去遗留回调守卫、忽略 reader 未结束、runner 不报 stuck、stop 重复等待、stop 取锁、线程未起先赋值、reset 移到上电返回后）
  各至少 1 条变红。`-k flash` 363 passed。
  第 3 轮后增至 39 条，新增 9 条（真实子进程：stderr 非法字节后回显仍被核对、核对回调异常被记录；不传参数时解码不变；完整消费三种缺口
  判失败；读取异常被记录；`stop()` 不碰同步原语；隔离子进程内持锁注入 SIGTERM 后结算完成）。5 种变异（严格解码、吞掉回调异常、忽略 eof、
  `stop` 调 `Event.set`、吞掉读取异常）各至少 1 条变红。`-k flash` 372 passed。
  第 4 轮后增至 50 条，新增 11 条（逐次核对 6 例、非绑定行 3 例、同一行两处目标回显、结算中途收到信号）；7 条在 `a00de3c` 上失败。
  5 种变异（旧结算闸门、不比对标记数、不查替换字符、只核第一处、节点名不严格）各至少 1 条变红。`-k flash` 383 passed。
  第 5 轮后增至 65 条，新增 15 条（P2 真实日志夹具回放 4 条、tty 引用 5 例、大小写 1 例、真实子进程信号延后 3 条、特权调用在临界区 2 条）。
  7 种变异（旧 `port\s*name` 标记、引用只取数字前缀、不核行内引用、不延后信号、严格解析区分大小写、`authorized` 写入或 wrapper 调用不在临界区）各至少 1 条变红。
  第 6 轮重设计后为 92 条（删去第 5 轮的临界区用例 5 条，新增白名单 13 例、标记大小写 11 例、真实子进程信号 5 条、静态与预算 8 条等）。
  9 种变异（白名单放宽分隔符、V6 解析区分大小写、不排空、置位后不拒绝、不看 wrapper 子进程、不看 reader 在途、排空未完仍删状态文件、
  排空时长超过宽限）各至少 1 条变红；用 Agent 实际的 `_terminate_process_tree` 与 8 秒宽限复现审阅者的 SIGKILL 场景，结算在强杀前完成。
  第 7 轮后为 106 条，新增 V5 `symbolic_link` 3 例、先落盘、信号与在途时保留记录、恢复顺序、wrapper 识别 5 例、静止等待、预算耗尽。
  9 种变异（V5 放行 `symbolic_link`、不先落盘、信号路径删记录、正常结算忽略在途、预算耗尽不拒绝、僵尸计为存活、不分子命令、
  恢复不含意图记录、不等 wrapper 静止）各至少 1 条变红。旧版本测试里 `_gate_other_mtk` 的替身补了 `before_write` 形参。
  第 8 轮拆分后：`git diff cef0db0` 只剩文件头说明与回显解析区域（正则与 `_check_bound_echo` / `_check_vbus_bind_line`）；
  `test_flash_firmware_v142.py` 81 条通过（第 1–4 轮全部用例 + 第 5–7 轮回显用例），`-k flash` 414 passed。
  第 9 轮：节点名归一为 `ttyACM<数字>`、同一行同一节点只解析一次、回显按标记计数；新增 7 条（大写路径 × 两种节点状态 × 两种写法、
  同行重复引用 3 例），白名单通过用例的替身改为只认内核写法。3 种变异（不归一、不去重、按引用计回显）各至少 1 条变红；88 条通过。

- 2026-11-11 `flash_firmware` 1.4.3：`backend/agent/tests/test_flash_firmware_v143.py` 4 条（带 `pci0000:00` 前缀解析、
  无 PCI 前缀不变、缺节点返回 None、采样历史记住带 PCI 前缀的已消失节点）。`-k flash` 424 passed / 1 failed；
  唯一失败 `test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails` 在基线 `90079e29` 同样失败
  （本机用户在 dialout 组），与本次改动无关。反向验证：把 `_USB_INTERFACE_RE.match(part)` 还原为 `if ":" in part`
  后新增用例 2 条变红，恢复后 4 条全过。

## Revisit

- 第二期槽位入库后，Agent 的 vbus 名额改由控制面按主机能力判定，不再看本地文件。
- 若后续 sysfs 出现非 PCI 前缀但同样含冒号的层级（或接口目录形态变化），以 `_USB_INTERFACE_RE` 为准补形态，不再用「含冒号」近似。
