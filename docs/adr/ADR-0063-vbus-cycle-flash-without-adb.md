# ADR-0063：无 ADB 设备的 USB 切电刷机（`trigger_mode=vbus_cycle`）

- 状态：**Proposed** v0.12（2026-11-10 v0.12：阶段 B 真机验收（PlanRun 1242）发现派发时已关机的设备过不了 ADR-0061 D6 第 1 条的 init 残留检查（adb 探针），刷机前必然失败并记成欠账；Owner 裁定选甲，新增 D5 第 3 条（vbus 刷机作业的残留检查推迟到第一个步骤成功之后），D6 第 1 条增补其发布面，§5 第 3 项同步；v0.11：阶段 B 真机验收（PlanRun 1242）发现 D4 第 6 条的串行守卫把停在 INIT→PATROL barrier 的作业算作在跑，两台设备互相等待；订正为只计仍在刷机的作业（`execution_state` 非 `WAITING_BARRIER`/`PATROL_SLEEP`），§5.3 同步；v0.10：按 #3899 定向复核（comment 6731346242）P2 订正 D2 第 6 条与 D3 第 9 条（参数级能力标记 `usb-power/expect-slot`）、D3 第 4 条与第 11 条的措辞、D6 第 1 条的执行主体，§5.2 补旧 wrapper 反例；v0.9：按 #3899 定向复核（comment 6708089815）新增 `--expect-slot` 切电前比对（D2 第 1、8 条，D3 第 13 条），消解 D3 第 8 与第 13 条的冲突，补写作约定与 §5 验收清单；v0.8：Owner 裁定 §8 第 6 项选甲，D2 第 9 条的残余面第一期接受，§8 无待裁项；v0.7：按 U2–U4 独立复核订正 D3 第 4、6、8 条与 D4 第 6 条——vbus 必须开启 V6 端口绑定，OOBE 只取跳过部分，刷后异常同样隔离，vbus 作业不论设备在线与否只走 vbus 名额；v0.6：按独立复核（PR #3899 comment 6706217246）收口 D1/D2/D3/D4/D6 与写作约定，D2 新增第 8 条残余面待 Owner 裁定（§8 第 6 项），§6 拆分 U2 激活步骤；v0.5：Owner 提供关机态 lsusb（`0e8d:2046`），§8 第 5 项转已裁，D3 第 11 条放行集合补 2046；v0.4：Owner 裁定 §8 第 4 项（unauthorized 排除）与「设备必须已关机」前提，新增 D3 第 11–12 条，订正 D3 第 6 条键序；新增 §8 第 5 项待裁；v0.3：Owner 答复 §8 三项，见 §8；新增 §8 第 4 项待裁；v0.2：补 F11 与 D4 第 5–6 条，Agent 认领侧也要改，D6 第 1 条随之订正为随 Agent 代码发布；v0.1 2026-11-07 起草；Owner 已在项目对话中裁定「与 B8 并行实施、第一期不加迁移」，本稿待定向复核通过后转 Accepted）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-11-07
- 决策者：Owner
- 标签：刷机, 提权, USB Hub, 派发准入, Agent 主机
- 归属域：semantic-ownership flash-tool
- 关联：[#3892](https://github.com/DUElost/stability-test-platform/issues/3892)（需求载体）；
  [风险台账 R-05](../notes/architecture/2026-09-03-platform-risk-register.md)；
  [ADR-0037](./ADR-0037-agent-host-privilege-boundary.md)（本稿 D2 修订其白名单，对应其待裁草案 D8）；
  [ADR-0051](./ADR-0051-release-unit-and-content-addressing.md)（脚本按新版本发布）；
  [ADR-0058](./ADR-0058-planned-batch-execution.md)（本需求命中 D7「协调激活 + 安全」强判据，按批次交付）；
  [ADR-0061](./ADR-0061-device-return-contract.md)（失败后的设备归还与欠账）；
  [刷机路由设计](../design/2026-08-honor-flash-firmware-routing.md)

## 1. 背景与证据（2026-11-07，main `3e605a1`）

### 1.1 需求

现行 `flash_firmware` 依赖设备开机且 ADB 在线：刷前用 `adb reboot` 让设备流经 BROM/Preloader 窗口。
设备关机（插着 USB 时呈现 `0e8d:2046`，§8 第 5 项）、User 版本关闭调试或系统假死时，ADB 不可用，现场只能人工插拔。
#3892 要求用可逐口切电的 USB Hub（`uhubctl`）代替人工插拔，并把「切电 → 刷写 → 开机 → 跳过 OOBE →
版本核验」做成单台闭环，单主机严格串行。
`0e8d:201c` 是 MLD-LX2/LX3/NX2 开机后的正常 ADB 态（也是 HONOR DA 态），不是关机态；槽位口出现 201c 即按开机态处理（D3 第 11 条）。

### 1.2 现行代码事实

| # | 事实 | 位置 |
|---|---|---|
| F1 | 设备心跳缺失超过 `DEVICE_OFFLINE_TIMEOUT`（默认 60s）后，控制面把设备行改为 `OFFLINE`。关机设备因此一律是 `OFFLINE`。 | `backend/api/routes/heartbeat.py:142-169` |
| F2 | 派发准入对 `OFFLINE` 设备以 `device_offline` 拒绝。 | `backend/services/plan_dispatcher_sync.py:197` |
| F3 | Agent 认领再过滤一次：`adb_connected=False`、`adb_state∈{offline,unknown}` 或 `status=OFFLINE` 的设备不进入候选。 | `backend/services/agent_claim.py:209-216` |
| F4 | `adb reboot` 前若设备不在 `device` 态，脚本跳过 reboot，工具停在 USB 上等待；现场人工插拔即可被捕获。vbus 方案等价于把「人工插拔」换成「工具就绪后开启端口供电」。 | `backend/agent/scripts/flash_firmware/flash_firmware.py:1444` |
| F5 | 门控（隐藏非目标 MTK 口）按 `STP_DEVICE_SERIAL` 在 sysfs 反查目标口；关机设备没有可查的 serial，必须改用静态槽位。 | `flash_firmware.py:522`、`:2213` |
| F6 | 主机锁 `/tmp/stp-flash-firmware.lock` 由单个脚本进程以 `flock` 持有；锁不能跨 Pipeline 步骤。 | `flash_firmware.py:346`、`:1283` |
| F7 | 刷后已有 `boot_completed` + USB 拓扑稳定等待（`boot_stabilize_seconds`）与 `ro.build.version.incremental` 核验。 | `flash_firmware.py:116`、`:871`、`:1885` |
| F8 | wrapper `stp-agent-priv` 不随热更新下发，更新需逐台跑 `update_agent.yml`（ADR-0037 §4「运维代价」、§5 第 5 条先例）。 | `tools/ansible/playbooks/update_agent.yml:134-163` |
| F9 | 脚本按包分发，不进 Agent 代码载荷（ADR-0051 Phase 3）；已发布版本不可原地改。 | `stp_agent_priv.py` `FIXED_EXCLUDES` 的 `scripts/` 项 |
| F10 | `Device` 表没有机架或槽位字段（R-05）。 | `backend/models/host.py`（`class Device`） |
| F11 | Agent 的认领槽位 = 在线健康设备数 − 活跃设备数；在线健康设备为 0 时健康闸门也返回 0。槽位 ≤ 0 时 Agent 不调用认领接口，且只保留前 `available_slots` 个作业。设备全部关机的刷机主机因此永远不会认领。 | `backend/agent/capacity_reporter.py:106-109`、`backend/agent/claim_loop.py:51-60` |

### 1.3 与 B8 的关系

B8（#3800）在途单元 G4（#3877 / PR #3897）、#3875（PR #3896）、#3889 改动的是扫描、上送、影子轮与心跳计数，
与本 ADR 涉及的文件（刷机脚本树、wrapper、派发与认领准入、Agent 认领循环）没有交集。本 ADR 第一期不加迁移，
因此不进入 B8 的迁移链排序。脚本走包分发、wrapper 走 Ansible；但 Agent 认领循环（F11、D4 第 5 条）是
Agent 代码，会改变控制面的期望 digest，而 #3875 的影子能力守卫要求主机 digest 与期望值一致，
所以这部分必须进入 B8 的 Agent 代码滚动（见 D6 第 1 条）。
其余三个发布面（wrapper、脚本包、控制面准入）都不在 Agent 代码载荷内，不改变期望 digest。

## 2. 决策

### D1 槽位映射：主机本地、root 属主的单一来源

1. 每台装有可控 Hub 的主机必须有一份映射文件 `/etc/stp-agent/usb-power-slots.json`，属主 `root:root`，
   权限 `0644`；父目录 `/etc/stp-agent` 必须为 `root:root 0755`，不得 group/other 可写（防同目录 rename 替换；
   Agent 进程要读这份文件，目录必须可搜索）。内容为 serial 到槽位的映射：

   ```json
   {"schema": 1, "slots": [{"serial": "<adb serial>", "hub_location": "1-1.4", "port": 3}]}
   ```

2. 映射文件必须只由 Ansible（inventory `host_vars`）写入；其他主体不得写入，Agent 用户不得有写权限。
3. wrapper（D2）与刷机脚本（D3）都必须只从这份文件读取槽位；脚本不得接受步骤参数里的 `hub_location` 或 `port`。
4. 映射文件中 `serial` 必须唯一，`(hub_location, port)` 必须唯一；`schema` 必须为 1，每条只含 `serial`、`hub_location`、`port`
   三个字段，`hub_location` 必须匹配 `usb-authorized` 的端口名正则，`port` 必须是 1–16 的整数。wrapper 读到违反任一条的文件时
   必须拒绝全部切电请求（`uhubctl -p` 接受端口列表与范围，未校验的 `port` 可以一次切断整个 Hub）。
5. 第一期不得新增数据库迁移。槽位入库（R-05 的 rack/slot 字段）留作第二期，另立单元。

### D2 提权窄面：`usb-power` 子命令（修订 ADR-0037，对应其待裁草案 D8）

1. wrapper 新增子命令：

   ```
   stp-agent-priv usb-power --serial <serial> --expect-slot <hub_location>:<port> --action {on|off|cycle} [--delay <int>]
   ```

2. wrapper 必须按 `--serial` 在 D1 映射文件中查槽位；查不到时必须以非零退出拒绝。wrapper 不得接受用来指定切电目标的
   Hub 位置或端口参数；`--expect-slot` 只用于第 8 条的比对，切电目标必须只取自映射。
3. wrapper 必须要求 `--serial` 匹配 `^[A-Za-z0-9._:-]{1,64}\Z`；必须要求 `--expect-slot` 存在，且其 `hub_location`
   匹配 D1 第 4 条的正则、`port` 为整数 1–16；必须只在 `--action cycle` 时接受 `--delay`，取值 1–30，越界即拒绝；
   parser 必须设 `allow_abbrev=False`。
4. wrapper 必须用固定 argv 调用 `/usr/sbin/uhubctl -l <hub_location> -p <port> -a <action> [-d <delay>]`，
   不经 shell；子进程环境只留 `LC_ALL=C` 与 `PATH=/usr/bin:/bin`，超时 60s。
5. wrapper 必须在调用前校验：
   - `/usr/sbin/uhubctl` 是 root 属主、非 group/other 可写的普通文件；
   - 映射文件以 `O_NOFOLLOW` 打开，`fstat` 为普通文件、root 属主、非 group/other 可写、不超过 64 KiB；父目录同样为 root 属主且非 group/other 可写；
   - `hub_location` 在 `/sys/bus/usb/devices/` 下解析为 `/sys/devices` 内同名的真实目录（沿用 `usb-authorized` 的判定）。
6. `capabilities` 输出必须包含 `usb-power` 与参数级标记 `usb-power/expect-slot`（后者表示本子命令实现了第 1 条的 `--expect-slot` 比对）；ADR-0037 §4 不变量第 2、3 条的强控制与测试清单必须同批补上本子命令。
7. 安装链不得新增或修改 sudoers 行（ADR-0037 D6：仍是 wrapper 单命令）。
8. wrapper 必须在调用 `uhubctl` 之前比对查表得到的槽位与 `--expect-slot`；不一致时必须以 exit 2 拒绝，stderr 带
   `STP_USB_POWER_SLOT_MISMATCH`，且不得执行任何切电（映射在调用方与 wrapper 两次读取之间被改写时，不会先切错一次）。
   wrapper 成功时必须在 stdout 回显本次实际使用的 `serial`、`hub_location`、`port` 与 `action`，供调用方做纵深核对（D3 第 13 条）。
9. 信任边界的残余面：wrapper 不校验主机锁、作业或租约，Agent 用户可以对映射里任一已登记 serial 直接切电，
   包括同主机上另一台正在跑测试的设备。本边界只保证「不能指定任意 Hub 或端口」，不保证「只能操作当前作业的设备」。
   Owner 2026-11-07 裁定第一期接受这一残余面（§8 第 6 项，甲）；第二期槽位入库时，规划者必须重新评估作业绑定。

### D3 刷机脚本：`flash_firmware` 新版本增加 `trigger_mode`

1. 实施者必须以 `flash_firmware` 的新版本落地（ADR-0051；不得原地改已发布版本），新增步骤参数
   `trigger_mode ∈ {"adb_reboot", "vbus_cycle"}`，缺省为 `adb_reboot`。
2. `trigger_mode` 缺省时，新版本的行为必须与上一版本逐项一致；现有版本测试必须全部通过。
3. `vbus_cycle` 的单台流程按顺序为：取主机锁 →（ADB `device` 态时 `adb reboot -p`，见第 11 条）→ `usb-power off` → 等待
   `vbus_off_settle_seconds`（缺省 12，取值 10–30，下限对齐 #3892 的 10–15s 放电要求）→ 启动刷机工具进入 USB 扫描 → 工具就绪回调中
   `usb-power on` → 捕获与写包 → 等待开机与 ADB → 跳过 OOBE → 版本核验 → 释放主机锁。
4. 门控目标口必须取自槽位（sysfs 名 `<hub_location>.<port>`），不得在 `vbus_cycle` 下按 serial 反查。
   `vbus_cycle` 必须开启端口绑定（V6 工具带 `-p <槽位口>`，并逐行核对工具回显的绑定口）。工具为 V5 或主机不是 Linux 时，
   即使步骤参数 `require_port_binding=true`，脚本也必须在取锁前失败；`require_port_binding=false` 时，脚本同样必须在取锁前失败（复核 #3902：关掉绑定等于让工具扫全部 MTK 口）。
5. 主机锁必须覆盖第 3 条从「取锁」到「版本核验」的全部阶段；锁外不得开启任何槽位的供电。
6. 跳过 OOBE 必须在本脚本内完成，配方取现行 `oobe_skip` 1.1.x 的跳过部分：写 `user_setup_complete` 与
   `device_provisioned` 两个标志 → force-stop setupwizard → 唤醒 224 → 解锁 82 → HOME 3，以两个标志回读为 1
   作为判据（v0.3 写成「224 → 82 → force-stop → 3」，与 `oobe_skip` 实际顺序不符，v0.4 订正）；
   单条 adb 命令的超时不得超过本阶段剩余预算。`oobe_skip` 的 `adb root` 与 `system_locales` 不属于跳过 OOBE，
   脚本不得在锁内执行；需要时由 Plan 在后续步骤用 `ensure_root` / `oobe_skip` 完成（与 §8 第 2 项「`ensure_root` 不进锁」同口径）。
   脚本不得 import 其他脚本包的代码。
7. 脚本必须为各阶段设置独立硬超时，缺省值：BROM 捕获 120s、写包沿用现行工具超时、开机到 ADB 300s、OOBE 60s、版本核验 60s。
8. 开启供电之后的任一阶段失败、超时或抛出异常，脚本必须对本槽位执行 `usb-power off`，再释放锁；只有全链成功且没有抛出异常时，
   脚本才可以保持供电。第 13 条第 2 款的情形是唯一例外：脚本不得再做隔离断电。隔离断电同样必须带脚本读到的槽位，
   wrapper 因映射已改写而拒绝时，脚本必须把结果记为隔离失败；
   SIGTERM / SIGINT / SIGHUP 处理器必须走同一收尾路径。`usb-power off` 本身失败时，脚本仍必须释放锁，并在结果 JSON
   标记隔离失败。SIGKILL 不可捕获，门控由下一任脚本按现有 gate-state 机制恢复；槽位供电状态由下一任 vbus 刷机的首次断电覆盖。
9. 脚本在取锁之前必须确认 wrapper `capabilities` 同时含 `usb-power` 与 `usb-power/expect-slot`（只报 `usb-power` 的旧 wrapper 未实现 `--expect-slot`），且本机有 D1 映射文件、目标 serial 在其中；
   任一不满足时必须直接失败，并且不得执行任何切电动作。
10. 结果 JSON 必须带 `trigger_mode` 与 `vbus` 段（槽位、各阶段耗时、隔离是否执行）。
11. 设备必须已关机（Owner 2026-11-07）：开机态的手机断电再上电不经过 BROM，工具检测不到刷机口。脚本必须按下列规则
    满足或拒绝这一前提，拒绝时不得执行任何切电动作。取锁之前脚本必须只做只读探测（ADB 状态、槽位口枚举、wrapper 能力、映射）；既有的孤儿门控恢复（上一任脚本被 SIGKILL 后把残留的 `authorized=0` 恢复为 1，见第 8 条）是唯一例外，它不切电，但可能改变被上一任门控端口的 `authorized`（本 job 的槽位口恰是上一任的非目标口时，该口的 USB 授权状态会变）；锁内第一次切电前的复核覆盖这一变化：
    - ADB 为 `device` 态时，脚本必须在取锁前按 serial 反查 sysfs，确认设备就在映射槽位口上，不符即失败；
      然后在取锁后、第一次 `usb-power off` 之前执行 `adb reboot -p`，等设备从 adb 消失、且槽位口不再呈现开机态
      （可刷态与关机态 `0e8d:2046` 都算已关机；`vbus_poweroff_timeout_seconds`，缺省 60），超时即失败；
    - ADB 为 `unauthorized` 时，脚本必须直接失败：关机与重启都无法用命令做到（与 D4 第 2 条 `device_error` 照常拒绝同口径）；
    - 其它 ADB 状态（`offline`、不可见等）进切电流程；
    - 槽位口 `<hub_location>.<port>` 上已有不在可刷态（`0e8d` 且 PID ∈ `_BROM_GATE_PIDS` = `0003/2000/2001/3000/2026`，
      不含 `201c`）、也不在关机态（`0e8d:2046`，§8 第 5 项）的 USB 枚举时，脚本必须判为开机态并失败；这一条识别
      「开机但没开 ADB」的设备。取锁后第一次切电前必须再复核一次。
12. `trigger_mode=vbus_cycle` 且未给 `firmware_dir` 时，设备读不到 `ro.product.model`，步骤必须用参数 `model` 声明机型；
    脚本必须在切电前按族表与固件 manifest 的 `models` 校验声明值，刷后必须回读机型，与声明不一致判失败。
    `adb_reboot` 路径不读本参数，指纹路由不变。
13. 槽位一致性按两款执行：
    - 第 1 款：脚本每次调用 `usb-power` 都必须带 `--expect-slot <脚本读到的 hub_location>:<port>`。wrapper 以
      `STP_USB_POWER_SLOT_MISMATCH` 拒绝（D2 第 8 条，此时没有任何口被切）时，脚本必须立即失败；第 8 条的隔离断电照常尝试，
      映射仍不一致时 wrapper 会再次拒绝，结果记为隔离失败。
    - 第 2 款：断电、上电、隔离三处，wrapper 回显的槽位仍与脚本读到的不一致（说明 wrapper 没有按 D2 第 8 条比对）时，
      脚本必须立即失败，并且不得再执行任何切电动作，包括第 8 条的隔离断电；上电回显不符或上电失败时，脚本必须杀掉刷机工具
      并终止尝试，不得重试；隔离回显不符时，脚本必须把结果记为隔离失败。

### D4 派发与认领准入：只为 vbus 刷机步骤放行 `device_offline`

1. 派发准入（F2）与 Agent 认领（F3）对同一判据放行：作业 `pipeline_def.lifecycle.init` 的第一个步骤是
   `script:flash_firmware` 且其参数 `trigger_mode == "vbus_cycle"`。两处必须用同一个判定函数。派发侧有三个调用点，
   必须都接入，且都按冻结的 Plan 快照判定（判定参数不得有放行缺省值）：
   - `prepare_plan_run`（排队理由）；
   - 准入泵终检 `admission_pump.admission_transaction`（用 `pr.plan_snapshot`）；
   - legacy `complete_plan_run_dispatch` → `_validate_dispatch_devices_sync`。
   认领侧按作业的 `pipeline_def` 判定。
2. 放行只豁免 `device_offline` 与认领侧的 ADB 状态过滤；`not_found`、`no_host`、`host_retired`、
   `device_retired`、`serial_conflict`、`device_error`、`host_offline`、`host_maintenance`、
   `device_return_debt`、`active_lease` 必须照常拒绝。认领侧的 ADB 过滤原本顺带挡住了 `ERROR`（unauthorized）设备，
   豁免它之后，控制面必须在 vbus 候选查询中显式排除 `status=ERROR` 与 `adb_state=unauthorized`。
3. 被放行的设备必须已有设备行且绑定到某台主机；第一期控制面不得为从未上线的设备自动建行。
4. 不满足第 1 条的作业，控制面的准入与认领行为必须与现行完全一致。
5. Agent 在本机存在 D1 映射文件时，必须在认领请求中带 `vbus_slots=1`，并且在 `available_slots ≤ 0` 时也调用认领接口；
   映射文件不存在时，Agent 不得带该字段，行为与现行一致。
6. 控制面必须把 vbus 刷机作业与普通作业分开计数：普通作业仍受 `capacity` 限制；放行的关机设备上的 vbus 作业
   每次认领最多返回 `vbus_slots` 个，不占用 `capacity`。`vbus_slots` 是独立的 host 级并发上限，取值 0–1，缺省 0；
   vbus 候选查询在 `capacity=0` 时也必须执行。未带 `vbus_slots` 的请求（旧 Agent）不得认领关机设备上的作业。
   关机设备上只有最早一条 PENDING 作业是 vbus 刷机作业时，控制面才可以认领它；`uq_job_active_per_device` 保证一台设备同时只有
   一条 PENDING/RUNNING 作业，所以不存在「较早非 vbus + 较晚 vbus」的混合队列。本 host 已有仍在刷机的 vbus 作业时，
   控制面不得再返回新的 vbus 作业。「仍在刷机」指作业为 RUNNING，且运行子状态 `execution_state` 不是 `WAITING_BARRIER`
   或 `PATROL_SLEEP`；子状态为空（刚认领、首个心跳未到）也算仍在刷机。停在 INIT→PATROL barrier 的作业在等同 PlanRun 的
   下一台设备到达，控制面不得把它算作仍在刷机（阶段 B 真机验收 PlanRun 1242：算进来会让两台互相等到 barrier 超时）。
   vbus 刷机作业不论设备是否在线，控制面都必须只经 vbus 名额认领：控制面必须先从普通候选中滤掉 vbus 刷机作业，再按 `capacity` 截断，
   使 vbus 作业既不被普通名额认领、也不挤占普通名额（复核 #3901：否则普通路径可绕过上一句的守卫）。vbus 名额的候选是本 host
   上未退役、非 `ERROR`、非 `unauthorized`、无 ACTIVE 租约的设备，在线与关机设备都在内。未带 `vbus_slots` 的请求
   （旧 Agent）认领不到任何 vbus 作业，作业保持 PENDING（D6 第 4 条）。

### D5 与设备归还契约的关系

1. `vbus_cycle` 失败后槽位处于断电态，设备不会回到在线；这一结果按 ADR-0061 进入欠账，本 ADR 不新增例外。
2. 有欠账的设备不得再被 vbus 刷机派发（D4 第 2 条照常拒绝 `device_return_debt`）。清偿或放行必须由 Owner 或管理员按 ADR-0061 执行。
3. ADR-0061 D6 第 1 条要求 Agent host 在每个 job 跑 init 步骤之前做残留检查，探针走 adb；派发时已关机的设备在刷机前
   必然得不到探针结果（阶段 B 真机验收 PlanRun 1242：设备 B 等满 600s 后 `init_return_check:failed`，并记成欠账）。
   对满足 D4 第 1 条的作业，Agent host 必须先执行 `lifecycle.init` 的第一个步骤（vbus 刷机），该步骤成功后再做残留检查，
   检查通过后才执行其余 init 步骤。刷机步骤不建立任何专项足迹，ADR-0061 D6 第 1 条「建立本 job 足迹之前先确认」的要求不变；
   刷后设备已开机，探针能给出真实回读。判据与控制面共用 `backend/agent/contracts/vbus_flash.py`。
   - 刷机步骤已启动且返回失败时，Agent host 不得执行残留检查，并且必须执行本 job 的 teardown（与先查后刷的作业一致）。
   - 刷机步骤启动前（含执行许可交接之后、进入 STARTED 之前）已知租约丢失或作业已取消时，Agent host 不得启动刷机，
     必须以 abort 结束本 job，且不得执行本 job 的 teardown（本 job 没有任何动作；复核 #3915 P1）。
   - 推迟的残留检查返回失败或抛出异常时，Agent host 必须以 init 失败结束本 job，且不得执行本 job 的 teardown
     （与先查后刷的作业一致；复核 #3915 P2）。
   - 对不满足 D4 第 1 条的作业，Agent host 必须保持现行的残留检查时机。

### D6 发布与激活

1. 本 ADR 有五个发布面：wrapper + 映射文件 + `uhubctl` 包经 Ansible 只下发到装有可控 Hub 的主机；
   脚本新版本经包发布；准入改动随控制面常规部署；Agent 认领循环（D4 第 5 条）随 Agent 代码发布。
   D5 第 3 条（init 残留检查推迟）是第五个发布面，实施者必须让它随 Agent 代码发布。
   前三者不改变 Agent 期望 digest，实施者不得把前三者与 B8 的 Agent 代码滚动合批。Agent 认领循环与 D5 第 3 条的改动，
   实施者都必须随一次联合 Agent 发布滚动（认领循环首选 G4、#3875、#3889 那一次；赶不上截止就顺延到下一次），并且不得单独部署 Agent。
   对 D5 第 3 条的改动，实施者必须同批发布控制面与全机队 Agent。理由：它们改变期望 digest，
   #3875 守卫要求全机队 digest 一致；它在没有映射文件的主机上不改变行为。U4 部署前，实施者必须核对控制面
   `compute_desired_artifact_digest` 与该次联合发布一致；影子开关保持关闭，直到全机队 digest 匹配。
2. 激活只发生在 Plan 显式选择 `trigger_mode=vbus_cycle` 时；不设全局开关。
3. 首次真机验收必须在 1 台主机、2 台设备上完成 #3892 验收清单；只有验收通过后，实施者才可以在其他主机下发映射文件。
4. 实施者必须只把 vbus Plan 派到已具备映射文件与新 Agent 的主机；旧 Agent 不带 `vbus_slots`，作业会一直 PENDING。

## 3. 备选与否决

- **wrapper 接受 `--location` / `--port`（#3892 原写法）**：否决。sudoers 不约束 wrapper 参数（ADR-0037 D6），
  按位置与端口切电等于让 Agent 用户能断开主机上任何 Hub 口，包括正在跑测试的设备。按 serial 查 root 属主映射，
  参数空间收窄到映射里登记的槽位。
- **槽位写进 `Device` 表**：推迟到第二期。要加迁移，必须排在 B8 迁移链（PR #3896、#3897）之后，拖慢交付。
- **OOBE 与自检作为后续 Pipeline 步骤**：否决用于锁内阶段。锁不能跨步骤（F6），#3892 要求锁覆盖到主界面确认。
  `check_device` / `ensure_root` 不进锁（§8 第 2 项已裁）。
- **新建独立脚本族 `flash_firmware_vbus`**：否决。门控、进度戳、工具驱动与重试逻辑必须复用；跨族不能 import，复制会让两份刷机实现漂移。
- **派发侧加全局开关**：否决。激活面已经由 Plan 参数显式表达，再加开关只多一处配置漂移。

## 4. 影响

- 新增一个提权子命令，ADR-0037 白名单与测试清单同步扩展。
- 准入多一条按作业内容判定的分支；`device_offline` 不再是对所有作业都成立的拒绝理由。
- 刷机主机需要额外装 `uhubctl` 与映射文件；没有映射文件的主机行为不变。

## 5. 验收

沿用 #3892 验收清单，并补充：

1. wrapper（单测）：
   - 未登记 serial、`--delay` 超限或用于非 `cycle`、缩写参数、缺 `--expect-slot` 或其取值非法，均被拒绝；
   - `--expect-slot` 与映射不一致时，在调用 `uhubctl` 之前拒绝；
   - 映射文件：symlink、group/other 可写、非 root 属主、超过 64 KiB、父目录可写或非 root 属主、`schema`/字段/`port` 1–16 非法、
     serial 或槽位重复，均整份拒绝；
   - `uhubctl` 非 root 属主或 group/other 可写时拒绝；`hub_location` 不在内核 sysfs 树内时拒绝；
   - 固定 argv、环境只留 `LC_ALL`/`PATH`、超时 60s、非 root 拒绝；成功时回显实际槽位。
2. 脚本（单测）：
   - `trigger_mode` 缺省时与上一版本逐项一致（现有测试全绿）；
   - 取锁前各闸失败时零切电：映射、`capabilities`（含旧 wrapper 只报 `usb-power`、缺 `usb-power/expect-slot` 的反例）、V6 端口绑定、`unauthorized`、`device` 态端口身份、槽位口开机态
     （`0e8d:2046` 放行、`201c` 拒绝）、未声明 `model`、`verify_version` 关闭；
   - `device` 态的 `adb reboot -p` 在取锁之后执行；
   - 上电后各阶段失败、超时、抛出异常与收到信号，都在释放锁前断电隔离；成功时保持供电；
   - wrapper 以槽位不一致拒绝时不切任何口；回显不一致时不再切电。
3. 准入（单测，覆盖派发与认领两处）：
   - 只有满足 D4 第 1 条的作业放行 `device_offline`，三个派发调用点都覆盖；其他拒绝理由不受影响；
   - 认领：`ERROR`/`unauthorized`、退役、ACTIVE 租约不放行；`capacity=0` 时 vbus 名额照常认领；旧 Agent 认领不到 vbus 作业；
     在线设备上的 vbus 作业只走 vbus 名额；本 host 已有仍在刷机的 vbus 作业时不再返回新的；
     停在 `WAITING_BARRIER` / `PATROL_SLEEP` 的 vbus 作业不阻挡同 PlanRun 的下一台。
   - Agent（单测）：vbus 刷机作业的执行顺序是「刷机 → 残留检查 → 其余 init 步骤」；非 vbus 作业仍先做残留检查；
     刷机已启动且失败时不做残留检查且执行本 job 的 teardown；刷机启动前丢租约、推迟的残留检查失败或抛出异常时，
     不执行本 job 的 teardown（D5 第 3 条）。
4. 真机：1 台主机、2 台关机设备，串行完成全链路；注入卡开机，确认断电隔离后第 2 台照常完成。

## 6. 实施单元（按 ADR-0058 批次）

| 单元 | 内容 | 复核 |
|---|---|---|
| U1 | 本 ADR + ADR-0037 待裁草案 D8 | Owner 裁定 + 独立复核 |
| U2 | wrapper `usb-power`、Ansible 下发映射文件与 `uhubctl`、测试（激活拆为 U2a / U2b / U2c，见下） | 必须复核（安全） |
| U3 | `flash_firmware` 新版本 `trigger_mode=vbus_cycle`、测试 | 必须复核（难回退：物理断电） |
| U4 | 派发与认领准入放行、Agent 认领循环 `vbus_slots`、测试 | 必须复核 |

U2、U3、U4 可以并行实施。激活顺序：
1. U2a：wrapper 与 `uhubctl` 下发到装有可控 Hub 的主机（`update_agent.yml` + `ensure_usb_power_slots.yml` 的安装部分）；
2. U2b：映射文件只下发到验收主机；
3. U3 发布；
4. U4 部署（控制面 + 随联合 Agent 发布滚动，D6 第 1 条）；
5. 真机验收（D6 第 3 条）；
6. U2c：验收通过后，其余主机下发映射文件。

## 7. Revisit

1. 第二期槽位入库（R-05）落地后，D1 映射文件改为由控制面下发或由数据库生成；届时重审 D2 的查表来源。
2. 若出现「从未上线的新设备也要 vbus 刷机」的需求，重审 D4 第 3 条。

## 8. Owner 裁定与待裁项

已裁（2026-11-07，Owner 在项目对话中答复）：

1. Hub：16 口 USB Hub，芯片 Realtek RTS5411（USB 3.2 节点 `0bda:0411`，USB 2.1 节点 `0bda:5411`）。
   Owner 已用 `uhubctl` 实测可以单独给一个口断电。D3 的真机验收以该 Hub 为准。
2. `check_device` / `ensure_root` 不进锁：设备落到 OOBE 之前，这两步没有意义。锁内只做到 OOBE 跳过与版本核验；
   这两步作为同一 Plan 的后续步骤在锁外执行。
3. 待刷设备在平台都已有设备行（平台另有 lsusb 方式确认物理在线，但 ADB 不一定在线）。D4 第 3 条成立。
   注：lsusb 只计物理连接数，不建设备行，也不改设备状态；关机设备的状态仍按 F1 变为 `OFFLINE`。

4. ADB 为 `unauthorized` 的设备（控制面置 `ERROR`，派发按 `device_error` 拒绝）不放行，且以后也不加入：
   平台无法用 adb 让它关机或重启，新旧刷机方式都不可行。
   凡能用命令或编程手段满足「已关机」前提的设备（`device` 态先 `adb reboot -p`；`offline` 等已关机且 lsusb 可识别的设备）
   都可以进入 vbus 刷机计划，见 D3 第 11 条。

5. 已关机、lsusb 可识别的设备在槽位口上呈现 `0e8d:2046`（Owner 2026-11-07 现场 lsusb：MLD-LX3 关机 18 台全为
   `0e8d:2046 MediaTek Inc. MLD-LX3`，`adb devices` 为空）。D3 第 11 条据此放行 2046。
   开机但没开 ADB、同样呈现 2046 的设备在 USB 层无法与关机态区分，这类设备会在 BROM 捕获超时后失败并断电隔离（D3 第 8 条）。
   其它机型的关机态 PID 若不同，以新脚本版本补放行集合。

已裁（续）：

6. D2 第 9 条的残余面：Agent 用户可对任一已登记 serial 直接切电，wrapper 不校验主机锁、作业或租约。
   **已裁（Owner 2026-11-07）：甲**，第一期接受，记为残余风险。备选：
   - 甲：第一期接受，写入残余风险。同一 Agent 用户现在已能经 `usb-authorized` 隐藏任一 MTK 口、经 adb 重启任一设备，
     风险等级相同；第二期槽位入库时再评估作业绑定。
   - 乙：wrapper 校验调用时刻持有主机刷机锁（`/tmp/stp-flash-firmware.lock` 的 flock 持有者为调用方进程树）。
     只证明「有刷机在进行」，不证明是本 serial 的作业。
   - 丙：控制面签发一次性 token（作业 id + serial + 有效期），wrapper 校验。需要新的控制面接口与密钥管理，超出第一期。
