# ADR-0063：无 ADB 设备的 USB 切电刷机（`trigger_mode=vbus_cycle`）

- 状态：**Proposed** v0.4（2026-11-07 v0.4：Owner 裁定 §8 第 4 项（unauthorized 排除）与「设备必须已关机」前提，新增 D3 第 11–12 条，订正 D3 第 6 条键序；新增 §8 第 5 项待裁；v0.3：Owner 答复 §8 三项，见 §8；新增 §8 第 4 项待裁；v0.2：补 F11 与 D4 第 5–6 条，Agent 认领侧也要改，D6 第 1 条随之订正为随 Agent 代码发布；v0.1 2026-11-07 起草；Owner 已在项目对话中裁定「与 B8 并行实施、第一期不加迁移」，本稿待 Owner 答复 §8 第 5 项并经独立复核后转 Accepted）
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
设备关机、处于关机充电态（`0e8d:201c`）、User 版本关闭调试或系统假死时，ADB 不可用，现场只能人工插拔。
#3892 要求用可逐口切电的 USB Hub（`uhubctl`）代替人工插拔，并把「切电 → 刷写 → 开机 → 跳过 OOBE →
版本核验」做成单台闭环，单主机严格串行。

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

## 2. 决策

### D1 槽位映射：主机本地、root 属主的单一来源

1. 每台装有可控 Hub 的主机必须有一份映射文件 `/etc/stp-agent/usb-power-slots.json`，属主 `root:root`，
   权限 `0644`，内容为 serial 到槽位的映射：

   ```json
   {"schema": 1, "slots": [{"serial": "<adb serial>", "hub_location": "1-1.4", "port": 3}]}
   ```

2. 映射文件只能由 Ansible（inventory `host_vars`）写入；Agent 用户不得有写权限。
3. wrapper（D2）与刷机脚本（D3）都必须只从这份文件读取槽位；脚本不得接受步骤参数里的 `hub_location` 或 `port`。
4. 同一 `serial` 只能出现一次；同一 `(hub_location, port)` 只能出现一次。wrapper 读到违反任一条的文件时必须拒绝全部切电请求。
5. 第一期不得新增数据库迁移。槽位入库（R-05 的 rack/slot 字段）留作第二期，另立单元。

### D2 提权窄面：`usb-power` 子命令（修订 ADR-0037，对应其待裁草案 D8）

1. wrapper 新增子命令：

   ```
   stp-agent-priv usb-power --serial <serial> --action {on|off|cycle} [--delay <int>]
   ```

2. wrapper 必须按 `--serial` 在 D1 映射文件中查槽位；查不到时必须以非零退出拒绝。调用方不能指定任意 Hub 或端口。
3. `--serial` 必须匹配 `^[A-Za-z0-9._:-]{1,64}\Z`；`--delay` 只对 `cycle` 有效，取值 1–30；`allow_abbrev=False`。
4. wrapper 必须用固定 argv 调用 `/usr/sbin/uhubctl -l <hub_location> -p <port> -a <action> [-d <delay>]`，
   不经 shell；子进程环境只留 `LC_ALL=C` 与 `PATH=/usr/bin:/bin`，超时 60s。
5. wrapper 必须在调用前校验 `/usr/sbin/uhubctl` 是 root 属主、非 group/other 可写的普通文件，
   并校验 `hub_location` 在 `/sys/bus/usb/devices/` 下解析为 `/sys/devices` 内同名的真实目录（沿用 `usb-authorized` 的判定）。
6. `capabilities` 输出必须包含 `usb-power`；ADR-0037 §4 不变量第 2、3 条的强控制与测试清单必须同批补上本子命令。
7. sudoers 行不变（ADR-0037 D6：仍是 wrapper 单命令）。

### D3 刷机脚本：`flash_firmware` 新版本增加 `trigger_mode`

1. 实施者必须以 `flash_firmware` 的新版本落地（ADR-0051；不得原地改已发布版本），新增步骤参数
   `trigger_mode ∈ {"adb_reboot", "vbus_cycle"}`，缺省为 `adb_reboot`。
2. `trigger_mode` 缺省时，新版本的行为必须与上一版本逐项一致；现有版本测试必须全部通过。
3. `vbus_cycle` 的单台流程按顺序为：取主机锁 → 读 D1 槽位 → `usb-power off` → 等待
   `vbus_off_settle_seconds`（缺省 12，取值 5–30）→ 启动刷机工具进入 USB 扫描 → 工具就绪回调中
   `usb-power on` → 捕获与写包 → 等待开机与 ADB → 跳过 OOBE → 版本核验 → 释放主机锁。
4. 门控目标口必须取自槽位（sysfs 名 `<hub_location>.<port>`），不得在 `vbus_cycle` 下按 serial 反查。
5. 主机锁必须覆盖第 3 条从「取锁」到「版本核验」的全部阶段；锁外不得开启任何槽位的供电。
6. 跳过 OOBE 必须在本脚本内完成，配方与现行 `oobe_skip` 1.1.x 相同：写 `user_setup_complete` 与
   `device_provisioned` 两个标志 → force-stop setupwizard → 唤醒 224 → 解锁 82 → HOME 3，以两个标志回读为 1
   作为判据（v0.3 写成「224 → 82 → force-stop → 3」，与 `oobe_skip` 实际顺序不符，v0.4 订正）；
   脚本不得 import 其他脚本包的代码。
7. 各阶段必须有独立硬超时，缺省值：BROM 捕获 120s、写包沿用现行工具超时、开机到 ADB 300s、OOBE 60s、版本核验 60s。
8. 开启供电之后的任一阶段失败或超时，脚本必须在 `finally` 中对本槽位执行 `usb-power off`，再释放锁；
   SIGTERM / SIGINT / SIGHUP 处理器必须走同一收尾路径。
9. 脚本在取锁之前必须确认 wrapper `capabilities` 含 `usb-power` 且本机有 D1 映射文件、目标 serial 在其中；
   任一不满足时必须直接失败，并且不得执行任何切电动作。
10. 结果 JSON 必须带 `trigger_mode` 与 `vbus` 段（槽位、各阶段耗时、隔离是否执行）。
11. 设备必须已关机（Owner 2026-11-07）：开机态的手机断电再上电不经过 BROM，工具检测不到刷机口。脚本在取锁之前
    必须按下列规则满足或拒绝这一前提，拒绝时不得执行任何切电动作：
    - ADB 为 `device` 态时，脚本必须先 `adb reboot -p`，等设备从 adb 和槽位口上都消失
      （`vbus_poweroff_timeout_seconds`，缺省 60），超时即失败；
    - ADB 为 `unauthorized` 时，脚本必须直接失败：关机与重启都无法用命令做到（与 D4 第 2 条 `device_error` 照常拒绝同口径）；
    - 其它 ADB 状态（`offline`、不可见等）进切电流程；
    - 槽位口 `<hub_location>.<port>` 上已有不在可刷态（`0e8d` 且 PID ∈ `0003/2000/2001/3000/2026`）的 USB 枚举时，
      脚本必须判为开机态并失败；这一条识别「开机但没开 ADB」的设备。取锁后第一次切电前必须再复核一次。
12. 设备关机时读不到 `ro.product.model`。未给 `firmware_dir` 时，步骤必须用参数 `model` 声明机型，路由其余判据不变；
    刷后必须回读机型，与声明不一致判失败。

### D4 派发与认领准入：只为 vbus 刷机步骤放行 `device_offline`

1. 派发准入（F2）与 Agent 认领（F3）对同一判据放行：作业 `pipeline_def.lifecycle.init` 的第一个步骤是
   `script:flash_firmware` 且其参数 `trigger_mode == "vbus_cycle"`（派发侧按 Plan 快照判定，认领侧按作业的
   `pipeline_def` 判定，两处必须用同一个判定函数）。
2. 放行只豁免 `device_offline` 与认领侧的 ADB 状态过滤；`not_found`、`no_host`、`host_retired`、
   `device_retired`、`serial_conflict`、`device_error`、`host_offline`、`host_maintenance`、
   `device_return_debt`、`active_lease` 必须照常拒绝。
3. 被放行的设备必须已有设备行且绑定到某台主机；第一期不得为从未上线的设备自动建行。
4. 不满足第 1 条的作业，准入与认领行为必须与现行完全一致。
5. Agent 在本机存在 D1 映射文件时，必须在认领请求中带 `vbus_slots=1`，并且在 `available_slots ≤ 0` 时也调用认领接口；
   映射文件不存在时，Agent 不得带该字段，行为与现行一致。
6. 控制面必须把 vbus 刷机作业与普通作业分开计数：普通作业仍受 `capacity` 限制，放行的关机设备上的 vbus 作业
   每次认领最多返回 `vbus_slots` 个，且不占用 `capacity`。未带 `vbus_slots` 的请求（旧 Agent）不得认领关机设备上的作业。

### D5 与设备归还契约的关系

1. `vbus_cycle` 失败后槽位处于断电态，设备不会回到在线；这一结果按 ADR-0061 进入欠账，本 ADR 不新增例外。
2. 有欠账的设备不能再被 vbus 刷机派发（D4 第 2 条照常拒绝 `device_return_debt`）；需要人工清偿或放行。

### D6 发布与激活

1. 本 ADR 有四个发布面：wrapper + 映射文件 + `uhubctl` 包经 Ansible 只下发到装有可控 Hub 的主机；
   脚本新版本经包发布；准入改动随控制面常规部署；Agent 认领循环（D4 第 5 条）随 Agent 代码发布。
   前三者不得与 B8 的 Agent 代码滚动合批。Agent 认领循环的改动建议在 G4、#3875、#3889 的联合 Agent 发布
   截止前合入并随之滚动；理由：它在没有映射文件的主机上不改变行为，单独再滚一次全机队的代价高于合批。
2. 激活只发生在 Plan 显式选择 `trigger_mode=vbus_cycle` 时；不设全局开关。
3. 首次真机验收必须在 1 台主机、2 台设备上完成 #3892 验收清单后，才允许在其他主机下发映射文件。

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

1. wrapper：未登记 serial、重复登记、`--delay` 超限、缩写参数、非 root 属主的 `uhubctl` 均被拒绝（单测）。
2. 脚本：`trigger_mode` 缺省时与上一版本逐项一致（现有测试全绿）；`vbus_cycle` 各阶段超时都会触发 `finally` 断电（单测）。
3. 准入：只有满足 D4 第 1 条的作业放行 `device_offline`；其他拒绝理由不受影响（单测，覆盖派发与认领两处）。
4. 真机：1 台主机、2 台关机设备，串行完成全链路；注入卡开机，确认断电隔离后第 2 台照常完成。

## 6. 实施单元（按 ADR-0058 批次）

| 单元 | 内容 | 复核 |
|---|---|---|
| U1 | 本 ADR + ADR-0037 待裁草案 D8 | Owner 裁定 + 独立复核 |
| U2 | wrapper `usb-power`、Ansible 下发映射文件与 `uhubctl`、测试 | 必须复核（安全） |
| U3 | `flash_firmware` 新版本 `trigger_mode=vbus_cycle`、测试 | 必须复核（难回退：物理断电） |
| U4 | 派发与认领准入放行、Agent 认领循环 `vbus_slots`、测试 | 必须复核 |

U2、U3、U4 可以并行实施；激活顺序为 U2 部署 → U3 发布 → U4 部署（控制面 + 随 B8 的 Agent 滚动）→ 真机验收。

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

待裁：

5. 已关机、lsusb 可识别的设备在槽位口上的 VID:PID。D3 第 11 条目前只放行 BROM/Preloader 态，其它枚举一律判为开机态；
   §1.1 记过关机充电态呈现为 `0e8d:201c`，而 201c 也是 MLD 机型的正常 ADB 态，两者在 USB 层无法区分。
   Owner 给出现场 lsusb 原文后，实施者以新脚本版本补放行集合；在此之前这类设备会被拒绝（保守失败，不切电）。
