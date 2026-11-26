# ADR-0066：统一网络供给模块（WiFi 与 USB 反向供网）及出口策略预留

- 状态：Proposed v0.1（2026-11-26 起草，已完成设计分支对齐，待独立复核与 Owner 裁决；业务代码尚未实施）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-11-26
- 决策者：Owner
- 标签：网络供给, WiFi, USB 反向供网, 资源池, Agent 主机, 提权边界
- 归属域：n/a（新概念域「网络供给」，当前 `docs/design/2026-semantic-ownership.md` 尚无对应表行；待本 ADR 转 Accepted 时按其 §7 补登记 `network-supply`）
- 关联：
  [ADR-0019](./ADR-0019-android-device-lease-and-capacity-scheduling.md)（设备租约与容量调度）；
  [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)（PlanStep 与参数约束）；
  [ADR-0023](./ADR-0023-script-traceability.md)（脚本溯源与 `ResourceAllocation` 事实源）；
  [ADR-0031-A](./ADR-0031-A-appendix-phase3-core-write-tools.md)（AI 助手派发工具面 `wifi_pool_id`）；
  [ADR-0037](./ADR-0037-agent-host-privilege-boundary.md)（Agent host 提权边界 `stp-agent-priv`，本 ADR D6 拟增补 `usbnet-*` 白名单子命令草案）；
  [ADR-0042](./ADR-0042-settings-convergence-and-bare-read-boundary.md)（分域 Settings 收敛）；
  [ADR-0051](./ADR-0051-release-unit-and-content-addressing.md)（发布单元与脚本版本化）；
  [ADR-0054](./ADR-0054-agent-control-plane-shared-contracts.md)（Agent 与控制面共享契约包）；
  [ADR-0058](./ADR-0058-planned-batch-execution.md)（批次交付流程）；
  [ADR-0059](./ADR-0059-contract-and-sop-writing-conventions.md)（契约写作约定）；
  [ADR-0061](./ADR-0061-device-return-contract.md)（设备归还契约）；
  [ADR-0063](./ADR-0063-vbus-cycle-flash-without-adb.md)（`vbus_cycle` 刷机与 USB 状态边界）；
  `backend/models/resource_pool.py`、`backend/services/resource_pool.py`、`backend/services/plan_wifi.py`、`backend/services/plan_dispatcher_core.py`、`backend/services/plan_dispatcher_sync.py`、`backend/agent/stp_agent_priv.py`、`frontend/src/pages/wifi/WifiPage.tsx`

---

## 1. 背景与实测证据（2026-11-26，基线 `origin/main@0dc5d6fcf`）

### 1.1 业务场景与问题本质

STP 平台中，绝大多数稳定性专项（离线 Monkey、Aging DDR、GPU、PowerCycle、刷机等）无需设备联网；对时已由 `sync_device_time` 通过本地 `cmd alarm` 脱网完成，APK 与资源由 `adb push` 下发。真正需要设备联网的仅限少数特定专项：

1. **联网 Monkey 专项**：需要基础外网访问，部分场景关注 App 联网压测而非 WiFi 硬件本身；
2. **MTBF 专项**：依赖真实 `wlan0` 连接与套件 `UiAutomatorTestData.xml` 中的 `wifiName`/`wifiPWD` 配置，测试对象包含无线链路与功耗路径；
3. **在线视频待机专项（如定时浏览 YouTube）**：以普通网络访问为主，同时需要海外出口（VPN 链路）的扩展空间。

现状存在两方面结构性矛盾：
- **机房无线 AP 瓶颈 vs 宿主宽带闲置**：高密度测试机房的无线路由器带机量与速率受限，而挂载设备的 Linux Agent host 普遍具备高带宽有线网络；
- **模型泛化但产品面窄化**：数据库层早已采用泛化的 `resource_pool`（含 `resource_type` 与 `config: JSONB`）与 `resource_allocation` 表，但前端页面（`/wifi`）和调度胶水（`wifi_pool_id`、`plan_wifi.py`）仅暴露单一的 WiFi 供给形态，缺少按「接入方式 × 出口策略」统一建模以及对当前启用供网设备的集中观测与回收面。

与此同时，**WiFi 模块不能被 USB 反向供网替代**：MTBF 套件强依赖 `wlan0`、长稳需真实覆盖 WiFi 驱动/固件/休眠唤醒路径、部分系统组件（如 `com.mediatek.ims`）显式监听 `TRANSPORT_WIFI`，且 `connect_wifi` 免 root、重启可自连。因此正确方向是将原「WiFi 资源池」升格为统一的 **网络供给（Network Supply）** 模块，把 **WiFi** 与 **USB 反向供网** 作为平级子模块共存，并与 **出口策略（`direct` / `vpn`）** 正交解耦。

### 1.2 真机实验与代码事实

| # | 事实 | 来源 |
|---|---|---|
| F1 | 在本机离线实机（样机 A，MTK `HNMLD-M1`，Android 16 / SDK 36，root adbd，`wlan0 DOWN`，无 VPN）上实测：`svc usb setFunctions rndis` 切换为 `rndis,adb` 后 ADB 在 **3.07s** 内重连；Android 侧 `EthernetTracker` 的接口匹配正则为 `(usb\|eth)\d+`，将设备侧 `rndis0` 改名为 `usb0` 后，Android 原生 `EthernetTracker` + `IpClientManager` 在 **3s** 内自动完成 DHCP 并向 `ConnectivityService` 注册 `Active default network`（`Transports: ETHERNET`，`NOT_METERED & INTERNET & NOT_VPN`）。实测 `ping` 宿主 RTT **0.36 ms**、公网 `223.5.5.5` **19.75 ms**、`www.baidu.com`（DNS + ICMP）**21.47 ms**、`HTTP HEAD www.baidu.com` 返回 `200 OK`，Host→Android 实测下行吞吐 **260.9 Mbps（31.1 MB/s）**（同期同机连接 5G WiFi 的样机 B 链路协商速率为 Tx 78 / Rx 14 Mbps）。 | 2026-11-26 本机实机实验（已全量无痕清理） |
| F2 | 在本机双机（样机 A + 样机 B）上实测宿主单网桥（`br-stpusb-exp`）+ 单份 DHCP-only `dnsmasq`（`--port=0`，不占宿主 53 端口）+ 单条 `MASQUERADE` NAT：两台设备同时获派 `10.200.x.x` 独立地址并并发访问外网正常；执行 `svc usb setFunctions` 恢复纯 `adb` 后两机均完全回退至实验前状态。 | 2026-11-26 本机双机并发实验 |
| F3 | 对比实测 `adb reverse` + 宿主 HTTP 代理 + `settings put global http_proxy`：在离线样机 A 上 `ConnectivityManager` 仍报 `Active default network: none`，ICMP 与 DNS 全不可达，无法作为通用 Android 上网主方案。 | 2026-11-26 本机对比实验 |
| F4 | `usb_net` 实测工程约束：① `ip link set rndis0 name usb0` 强依赖设备 root（`CAP_NET_ADMIN`）；② 切 `rndis,adb` 会闪断 ADB 2–3s 且 USB PID 由 `0e8d:201c` 变为 `0e8d:2005`；③ 设备重启后恢复纯 `adb` 且 `usb0` 消失；④ 国内直连无翻墙时，Android 16 默认探测 `gstatic.com` 失败导致 `VALIDATED` 标志未点亮（普通 TCP/DNS/HTTP 正常，强校验 `NET_CAPABILITY_VALIDATED` 的任务受影响）。 | 2026-11-26 本机实测 |
| F5 | `backend/models/resource_pool.py` 已定义 `resource_pool(resource_type, config, max_concurrent_devices, host_group, is_active)` 与 `resource_allocation(job_instance_id, resource_pool_id, device_id, allocated_params)`；`/api/v1/resource-pools/available` 已通过 `PUBLIC_CONFIG_KEYS` 白名单向非管理员剥密返回可用池。 | `resource_pool.py`、`resource_pools.py` |
| F6 | `backend/agent/stp_agent_priv.py`（ADR-0037）是唯一被 NOPASSWD sudoers 授权的提权入口；`Host.agent_capabilities`（#4044）已在每拍心跳权威声明当前 Agent 实例支持的能力列表。 | `stp_agent_priv.py`、`host.py` |
| F7 | `monkey_setup` v2.0.0 起在未注入 SSID 时跳过 WiFi 子步骤；`sync_device_time` 已脱网完成校时；`connect_wifi` 与 `monkey_setup` 是当前唯二消费 WiFi 凭据的脚本。 | `plan_dispatcher_core.py`、`test_wifi_optional_at_run_time.py` |

---

## 2. 决策

> 以下 D1–D8 节规则句遵循 [ADR-0059](./ADR-0059-contract-and-sop-writing-conventions.md) 写作约定：仅使用「必须 / 不得 / 建议 / 可以」，并明确执行者。

### D1 顶层架构与正交分层（网络供给 × 接入子模块 × 出口策略）

1. 控制面与前端必须将既有「WiFi 资源池」升级为统一的「网络供给（Network Supply）」域，并沿用 `resource_pool` 表作为供给池配置的唯一事实源，不得为不同接入方式拆分平行资源池表。
2. `resource_pool.resource_type` 必须收敛为受控枚举，包含两个平级接入子模块：
   - `"wifi"`：设备通过 `wlan0` 接入无线 AP；
   - `"usb_net"`：设备通过 USB RNDIS（`rndis0 → usb0`）接入所属 Agent host 的本地 NAT 网桥。
3. 出口策略必须作为与接入方式正交的配置属性 `config.egress` 存放于 `resource_pool.config` 中，合法值枚举为 `"direct"`（缺省，普通直连）与 `"vpn"`（预留，YouTube 等海外访问出口）。
4. Phase 2 启用 `"vpn"` 出口时，平台必须优先采用**网关侧出口分流**（`usb_net` 在 Agent host 侧经独立出口网桥或策略路由导向透明代理/TUN；`wifi` 指向已配置海外旁路由的专属 SSID），控制面与 Agent 不得将「在测试机上安装并分发第三方 VPN 应用及订阅凭据」作为平台主路径。
5. 未启用网络供给的专项（如离线 Monkey、Aging DDR、PowerCycle、刷机）不得消耗任何网络供给池配额，Agent 也不得为此类设备修改 USB 复合模式或 WiFi 状态。

### D2 生命周期、双入口状态模型与手动调试安全守卫

1. 网络供给必须支持「任务级按需启停（主路径）」与「管理员手动调试启停（辅助路径）」两种生命周期入口：
   - **任务级入口**：PlanRun 在派发期分配供给池名额，在 `init` 开启供给，在 `teardown` 及任何异常终态自动回收；
   - **手动调试入口**：管理员可以在「网络供给」管理页对当前无活跃 `JobInstance` 占用的闲置设备手动开启或断开临时调试供网，也可以对异常滞留的供网会话执行强制释放。
2. 控制面必须新增设备级活跃供网事实表 `device_network_session`（遵循单数表名规范），作为「已启用供网设备」视图与冲突判定的唯一权威；每条活跃记录必须包含 `device_id`、`host_id`、`resource_pool_id`、`source`（`"job"` | `"manual"`）、`job_instance_id`（`source="job"` 时非空，`source="manual"` 时为空）、`resource_type`、`egress`、`status`（`"PROVISIONING"` | `"ACTIVE"` | `"RELEASING"` | `"RELEASED"` | `"FAILED"`）、`assigned_ip`、`created_by`、`expires_at`（`source="manual"` 时必填，默认 `now + 4h`）与 `released_at`。
3. 控制面必须通过部分唯一索引强制同一台设备同一时刻至多持有一条未释放（`status IN ('PROVISIONING', 'ACTIVE', 'RELEASING')`）的 `device_network_session` 记录。
4. 对于 `source="manual"` 的手动调试会话，管理员可以显式续期（每次续期延长固定窗口，上限由服务端配置约束）；当会话超过 `expires_at` 时，控制面定时巡检必须将其推进为 `"RELEASING"` 并下发 Agent 回收指令，不得放任手动调试会话永久滞留。
5. PlanRun 在准入与派发前，控制面必须校验目标设备集合中不存在未释放的 `source="manual"` 会话；若命中任一设备，控制面必须直接拒绝派发并返回明确错误（列出冲突设备与所属调试人，提示先释放），不得静默抢断正在调试的设备。
6. 控制面核算资源池当前负载时，必须将活跃 `JobInstance` 对应的 `resource_allocation` 与未释放的 `source="manual"` 会话合并计数；对 `resource_type="usb_net"` 的池，控制面在分配前还必须额外校验每台目标 Agent host 的活跃 `usb_net` 设备数不超过 `config.max_devices_per_host`（缺省 `8`，范围 `1–32`），防止压垮单机 USB 2.0 Hub 带宽。

### D3 Plan 编排、API 契约与向后兼容

1. PlanRun 触发接口（`POST /api/v1/plans/{plan_id}/run` 及干跑预览端点）与 `plan_run.run_context` 必须新增 `network_pool_id: Optional[int]`；控制面必须继续接受既有 `wifi_pool_id` 作为兼容别名，当请求同时携带 `network_pool_id` 与 `wifi_pool_id` 且两者不等时，控制面必须返回 `400 Bad Request`。AI 助手派发工具（ADR-0031-A）必须同步兼容两键并沿用「必须显式传 ID、不得自动推断」原则。
2. 平台必须新增统一脚本族 `setup_network` 与 `teardown_network`（按 ADR-0051 以独立发布单元登记与版本化）：
   - `setup_network` 的步骤参数必须支持可选能力约束 `required_type`（`""` | `"wifi"` | `"usb_net"`）与 `required_egress`（`"direct"` | `"vpn"`，缺省 `"direct"`）；未指定 `network_pool_id` 时，`setup_network` 可以按可选语义跳过（与 `monkey_setup` v2.0.0 一致），除非步骤显式设置 `required=true`；
   - `setup_network` 必须在同一 Plan 模板下根据派发器注入的池类型自动分派至 WiFi 连接实现或 USB 反向供网实现，使联网 Monkey、在线视频待机等专项在启动时可以通过选择不同资源池切换接入方式，无需按接入方式维护重复 Plan；
   - `teardown_network` 必须与 `setup_network` 成对出现于 `teardown` 阶段，负责在任务结束时执行网络状态收口。
3. 派发与触发前校验（升级 `backend/services/plan_wifi.py` 为通用网络供给准入模块）必须强制执行以下匹配规则：
   - 仅含存量 `connect_wifi` 或 `monkey_setup`（未升级到 `setup_network`）的 Plan，控制面必须限定只可以选用 `resource_type="wifi"` 的活跃池；
   - 含 `setup_network` 且声明了 `required_type` 的 Plan（例如 MTBF 声明 `required_type="wifi"`），控制面必须拒绝不匹配类型的池；
   - 含 `setup_network` 且声明了 `required_egress="vpn"` 的 Plan，控制面必须拒绝 `config.egress != "vpn"` 的池；
   - 不含任何网络消费步骤的 Plan 携带 `network_pool_id` / `wifi_pool_id` 时，控制面必须直接拒绝请求。
4. 资源池配置剥密与脱敏必须保持 fail-closed 白名单机制：`GET /api/v1/resource-pools/available` 与 `loads` 只可以暴露白名单内的非机密键（`wifi` 延续 `ssid`、`band`、`router_ip`、`mac_filter`，新增通用键 `egress` 与 `usb_net` 公开键 `bridge_name`、`subnet_cidr`、`max_devices_per_host`）；`password` 及未来任何代理凭据不得进入普通用户端点、审计明文或前端可见步骤参数。

### D4 `usb_net` 准入、执行与 `teardown` 强回收契约

1. Phase 1 的 `usb_net` 仅承诺已 root（设备端 `id -u == 0`）的 Android 设备（MTK 平台先行；展锐机型在完成实机 `svc usb setFunctions rndis/ncm` 与 `EthernetTracker` 探测并登记兼容结论前，实施者不得放开其 `usb_net` 准入）。
2. 当 PlanRun 或管理员手动调试请求为设备启用 `usb_net` 时，控制面与 Agent 必须依次校验：
   - 所属 Agent host 已在最近一次心跳的 `Host.agent_capabilities` 中声明 `"usb_net"`；
   - 目标设备具备 root 权限（`id -u == 0`）且 `dumpsys ethernet` 报告 `Ethernet State: enabled`。
   任一条件不满足时，控制面或 `setup_network` 脚本必须 fail-closed 并返回可操作错误（提示改选 `wifi` 池或检查主机能力），不得静默降级或挂起等待。
3. `setup_network`（`usb_net` 分支）必须按以下闭环执行并在最终步骤回读核验后才报成功：
   - 调用 `stp-agent-priv usbnet-ensure-bridge` 确保本机目标出口对应的网桥、NAT 与 DHCP-only `dnsmasq`（`--port=0`）处于健康态；
   - 在设备执行 `svc usb setFunctions rndis` 并等待该串号 ADB 重连（有界超时，默认 `20s`）；
   - 通过 USB 物理拓扑（优先匹配设备 USB 路径/串号对应的 `enx*` 接口，避免多机并发误绑他人网卡）调用 `stp-agent-priv usbnet-attach` 将宿主侧接口挂入目标网桥；
   - 在设备端将 `rndis0` 改名为 `usb0` 并拉起（若目标固件原生即出 `usb0` 则跳过改名）；
   - 在设备端轮询回读 `usb0` 已获派目标子网 IPv4 地址、`dumpsys connectivity` 存在活跃 `Ethernet` 网络且能够 `ping` 通宿主网桥网关后，脚本才可以上报成功并将 `assigned_ip` 写入 `metrics` 与 `device_network_session`。
4. 强回收不变量：任何启用了 `usb_net` 的 `JobInstance`（无论正常完成、步骤失败还是被平台中止）在结束路径中，Agent 必须通过 `teardown_network` 及设备归还链（ADR-0061）执行以下收口动作：
   - 在设备在线时执行 `svc usb setFunctions` 恢复纯 `adb` 并等待回读 `sys.usb.state == adb`；
   - 调用 `stp-agent-priv usbnet-detach` 将残留宿主侧接口从网桥摘除（幂等，接口已消失视为成功）；
   - 从本地活跃 `usb_net` 跟踪表中移除该设备，并向控制面回报会话释放。
   上述回收不得遗留 `rndis,adb`（PID `0e8d:2005`）状态，防止污染后续 `flash_firmware` 与 `vbus_cycle`（ADR-0063）。

### D5 Agent 常驻轻量自愈（长稳重启恢复）

1. Agent 必须在本地维护受控的活跃 `usb_net` 设备表（持久化于 Agent 运行目录，随任务 `setup_network`/`teardown_network` 及控制面手动调试会话同步更新），并在后台巡检循环中**仅对该表内设备**执行轻量自愈。
2. 当活跃 `usb_net` 表内某台设备在长稳测试期间发生异常重启（如 KE）后重新进入 `adb device` 在线态时，若 Agent 检测到该设备已失去目标 `usb0`（或退回纯 `adb`）且宿主网桥未绑定其接口，Agent 必须自动重走与 D4 第 3 条等价的恢复流程（切 `rndis` → 挂桥 → 改名 `usb0` → 回读连通），并上报结构化恢复事件与更新 `assigned_ip`。
3. Agent 必须对单台设备的连续自愈失败设置退避与有界上限（例如连续 3 次失败后转入退避窗并上报告警事件），不得在设备反复重启时形成高频重枚举风暴或阻塞其他设备心跳。
4. Agent 不得对未登记在本地活跃 `usb_net` 表中的任何设备执行 `svc usb setFunctions` 或网卡操作。
5. 当手动调试会话到达 `expires_at` 或收到控制面强制释放指令时，Agent 必须先将设备移出活跃自愈表，再执行 D4 第 4 条回收动作，杜绝「边回收边自愈」竞态。

### D6 Agent host 提权边界、网桥拓扑与能力门控（修订 ADR-0037 草案项）

1. Agent host 侧所有需要 `CAP_NET_ADMIN` / root 权限的网络操作，必须全部收敛至 `/usr/local/sbin/stp-agent-priv`（ADR-0037）新增的受限白名单子命令，Agent 与脚本不得新增独立 root systemd 守护进程，也不得使用 `sudo sh -c`：
   - `usbnet-ensure-bridge --egress <direct|vpn>`：幂等创建固定前缀网桥（`br-stpusb-dir` / 预留 `br-stpusb-vpn`）、配置固定保留子网、设置针对该网桥的最小 `iptables` `MASQUERADE` 与 `DOCKER-USER`/`FORWARD` 放行规则，并管理严格绑定该网桥的 DHCP-only `dnsmasq`（`--port=0 --bind-interfaces`）；
   - `usbnet-attach --iface <ifname> --egress <direct|vpn>`：将指定 USB 网卡设为 NetworkManager 未托管、清空旧 IP 并挂入目标网桥；
   - `usbnet-detach --iface <ifname>`：幂等摘除目标网卡与清理规则；
   - `usbnet-teardown --egress <direct|vpn>`：主机关闭能力或运维维护时停用 `dnsmasq`、摘除规则并删除受控网桥；
   - `usbnet-status`：只读输出当前受控网桥、挂载成员、租约与 `dnsmasq` 健康态。
2. `stp-agent-priv` 必须对所有网络参数执行严格边界校验：
   - `--iface` 必须通过字符集白名单校验且经 `/sys/class/net/<iface>/device` 验证其父总线属于 `usb`，不得接受宿主主上联口（如 `enp*`、`eth*`）、`lo`、`docker0` 或任意非 USB 网桥；
   - `--egress` 必须限定为固定枚举（Phase 1 仅开放 `direct`），子网 CIDR 与网桥名必须由 wrapper 内部白名单表解析（或读 root 属主配置文件），不得接受调用方传入任意网桥名或任意 `iptables` 表达式；
   - 宿主上联出口网卡必须由 wrapper 自动从默认路由解析或读 root 属主配置，不得由非特权调用方覆写。
3. Agent host 的 `usb_net` 能力必须**默认关闭**（opt-in）。所有配置旋钮（如 `STP_USB_NET_ENABLED` 默认 `false`、`STP_USB_NET_SUBNET` 默认 `10.200.0.0/22`、`STP_USB_NET_DNS`、`STP_USB_NET_RECONCILE_INTERVAL_SECONDS` 默认 `15`）必须收敛至 `backend/agent/settings.py`（ADR-0042）。
4. 只有当主机显式开启 `STP_USB_NET_ENABLED=true`、内核具备 `rndis_host` 驱动、宿主已安装 `dnsmasq` 且 `stp-agent-priv capabilities` 声明支持 `usbnet` 时，Agent 才可以在心跳 `Host.agent_capabilities` 中上报 `"usb_net"`（能力常量定义于 `backend/agent/contracts/capabilities.py`，遵守 ADR-0054）。
5. 一个逻辑 `usb_net` 资源池可以服务多台已上报 `"usb_net"` 能力的 Agent host（按 `host_group` 作用域过滤；`host_group=NULL` 表示全部已开启主机）。每台 Agent host 使用本地独立私有 NAT 子网（默认 `10.200.0.0/22`，因经本机 `MASQUERADE` 出站，各主机之间互不冲突），使单次跨多台主机的 PlanRun 可以一次选中同一个逻辑 `usb_net` 池。

### D7 前端「网络供给」管理面与权限边界

1. 前端必须将既有 `/wifi`（「WiFi 资源池」）升级为统一「网络供给」管理页面（建议主路由 `/network-supply`，并保留 `/wifi` 自动重定向以兼容旧链接），页面导航与管理写接口维持 `adminOnly: true`（延续 #2360 权限基线）。
2. 「网络供给」页必须提供两个主 Tab：
   - **供给池管理**：按 `resource_type`（`wifi` / `usb_net`）分类型创建、编辑、启停与查看并发负载，统一展示出口标签（`普通直连` / `VPN 出口（预留）`）、`host_group` 作用域与单机上限；
   - **已启用供网设备**：仅列出当前存在未释放 `device_network_session` 的设备（不混入未启用供网的离线设备），展示设备串号、所属 Host、供网方式（WiFi / USB）、出口策略、分配 IP、实时状态、来源（PlanRun 链接 / 手动调试人）、剩余有效期，并提供管理员**一键强制释放**、**调试续期**以及**对闲置设备手动开启调试供网**的操作入口。
3. PlanRun 发起弹窗中的网络选择器必须从原「WiFi 选择器」升级为「网络供给选择器」：调用 `GET /api/v1/resource-pools/available`，按当前 Plan 声明的 `required_type` / `required_egress` 过滤并清晰标注池类型与出口标签，不再向非管理员暴露凭据字段。

### D8 显式不做（Out of Scope）与复议触发器

| 暂不做项 | 理由 | 复议触发器 |
|---|---|---|
| Phase 1 非 root 设备的本地 `VpnService`（如 `gnirehtet`）自动回退 | 引入辅助 APK 分发、系统授权弹窗与 VPN 槽位占用，违背「无 VPN 简单供网」核心诉求 | 出现规模化非 root 量产机必须走 USB 供网且无法扩容机房 WiFi AP 时 |
| Phase 1 生产开启 `egress="vpn"` 透明代理链路 | 当前待机 YouTube 专项尚在筹备期，先预留协议字段与架构边界，避免过早引入宿主机代理运维复杂度 | 在线视频待机专项正式立项并确认合规宿主出口节点与监控要求时 |
| 全机群默认开启 `usb_net` 常驻网桥 | 多数主机仅跑离线/刷机专项，无需额外网络守护与提权面 | 超过半数在线 Agent host 常态化承接联网专项时 |
| 移除或重构既有 WiFi 模块与 `connect_wifi` | MTBF、真实无线稳定性覆盖、非 root 与重启自连不可替代 | 仅当全部测试专项均不再验证 WiFi 驱动与无线场景时（预期不会触发） |

---

## 3. 备选方案与权衡

| 备选方案 | 放弃原因 |
|---|---|
| **方案 1：用 USB 反向供网彻底取代并移除 WiFi 模块** | 违背手机稳定性测试目标（摘除 `wlan0` 驱动/固件/功耗缺陷暴露面）、破坏 MTBF 套件（依赖真实 WiFi 与 `UiAutomatorTestData.xml`）、无法覆盖非 root 量产机。 |
| **方案 2：`adb reverse` + 宿主 HTTP 代理 + `settings put global http_proxy`** | 本机实测（F3）在离线设备上 `Active default network: none`，DNS/ICMP/UDP 全不通，多数 Android App 直接判无网，仅对极少数裸 HTTP 客户端有效。 |
| **方案 3：`gnirehtet`（`adb reverse` + 设备端本地 `VpnService`）** | 需安装与维护设备端 APK 及授权、占用唯一 VPN 槽位、与实机可能运行的诊断工具冲突，且用户态包转发吞吐显著低于内核态 USB RNDIS。 |
| **方案 4：独立 `stp-usbnetd` root systemd 服务** | 破坏 ADR-0037「单一提权入口」原则，额外增加一套跨主机安装、升级、健康探测与进程守护运维面。 |
| **方案 5：每个 `usb_net` 池 1:1 绑定单台 Host** | 跨多台 Agent host 发起单次 Monkey/待机 PlanRun 时无法选中同一个池，运维配置随主机数线性膨胀；改用「逻辑池 + 各机本地 NAT 子网 + 单机上限」彻底消除该摩擦。 |
| **方案 6：手动调试供网在 PlanRun 派发时静默抢断清理** | 容易打断工程师正在进行的实机抓包/联网定位，改为「默认 TTL 自动回收 + 派发期显式拦红提示先释放」更安全可解释。 |

---

## 4. 影响与风险

1. **安全与提权面（ADR-0037）**：`stp-agent-priv` 新增网络配置子命令前必须通过独立安全复核与边界单元测试（验证 `--iface` 非 USB 接口/路径穿越/非法网桥名/非白名单 `egress` 均被硬拒）。
2. **与刷机 / VBUS 切电的交互（ADR-0063）**：`rndis,adb` 改变设备 USB PID（`0e8d:2005`），必须严格依赖 D4 第 4 条的 `teardown` + 设备归还链（ADR-0061）回收；若设备异常掉电导致无法在设备侧切回纯 `adb`，需核验其重启后默认恢复 `persist.sys.usb.config=adb`（不得修改 `persist.sys.usb.config`）。
3. **USB 2.0 Hub 带宽竞争**：高密度 10/16 口 USB 2.0 Hub 共享 480 Mbps 带宽；D2 第 6 条通过 `max_devices_per_host` 限制单机并发并优先建议在 USB 3.0 直连口或低密度 Hub 宿主机试点 `usb_net`。
4. **国内直连无 `VALIDATED` 标志**：F4 实测普通 TCP/DNS/HTTP 正常，但强校验 `NET_CAPABILITY_VALIDATED` 的组件（如部分 GMS/WorkManager 任务）在纯国内 `direct` 出口下可能等待验证；Phase 1 在试点文档中明确说明，Phase 2 接入 `vpn` 出口后 `gstatic.com/generate_204` 将自然通过验证。
5. **跨芯片兼容缺口**：展锐机型（`unisoc`）的 USB gadget 函数名（`rndis`/`ncm`）与 `EthernetTracker` 接口过滤正则尚未真机探测，已列为 Phase 0 前置验证项。

---

## 5. 分期落地切片与验收门槛（按 ADR-0058 批次交付规划）

> 本 ADR 转 `Accepted` 后，按 ADR-0058 批次流程拆分实施单元，不一次性大改主干。

| 阶段 | 切片内容 | 验收门槛 |
|---|---|---|
| **Phase 0（前置探路与裁决）** | ① 完成本 ADR 与 ADR-0037 D10（`usbnet-*` 提权子命令）独立复核并转 `Accepted`；② 抽测 1 台展锐实机确认 `svc usb setFunctions rndis/ncm` 与 `EthernetTracker` 正则行为（决定展锐是否进 Phase 1）。 | ADR Accepted；展锐实机探测结论落文档。 |
| **Phase 1a（数据面与控制面契约）** | ① Alembic 迁移新增 `device_network_session` 表与部分唯一索引；② `resource_pool` 校验支持 `usb_net` 与 `egress`；③ 派发器与触发 API 支持 `network_pool_id`（保留 `wifi_pool_id` 兼容）、`required_type`/`required_egress` 校验与手动调试冲突拦截；④ 手动会话 TTL 过期巡检与释放接口。 | 后端单测全绿（含旧 `wifi_pool_id` 零回归、单机并发上限、手动冲突拦红、凭据剥密守卫）。 |
| **Phase 1b（提权边界与 Agent 供网闭环）** | ① `stp-agent-priv` 新增 `usbnet-*` 白名单子命令与边界单测；② `backend/agent/settings.py` 收敛 `STP_USB_NET_*`（默认关）及 `Host.agent_capabilities` 上报 `"usb_net"`；③ 新增脚本族 `setup_network` / `teardown_network`（含 `wifi` 与 `usb_net` 双分支）；④ Agent 轻量活跃表与重启自愈巡检 + ADR-0061 归还收口。 | 在 1–2 台试点主机完成双机真机验收：任务开启 → 断电/重启自愈 → `teardown` 恢复纯 `adb` → 紧接跑 `flash_firmware` 零冲突。 |
| **Phase 1c（前端「网络供给」管理面）** | ① `/wifi` 升级为「网络供给」（`/network-supply` + `/wifi` 重定向），落地「供给池管理」与「已启用供网设备」双 Tab（含手动调试启停/续期/强释）；② PlanRun 发起弹窗升级为「网络供给选择器」。 | Vitest + 隔离预览实机截图核验（普通用户无凭据泄漏、管理员全流程闭环）。 |
| **Phase 2（按需激活 VPN 出口）** | ① 试点宿主网关侧打通 `br-stpusb-vpn` 透明代理/TUN 出口与 `usbnet-ensure-bridge --egress vpn`；② 在线视频待机专项 Plan 声明 `required_egress="vpn"` 跑通 YouTube 定时播放闭环（验证 `VALIDATED` 点亮且手机零 VPN App）。 | 待机专项真机连续 24h YouTube 播放验收通过。 |

---

## 6. 关联实现与文档

- 数据模型与资源池服务：`backend/models/resource_pool.py`、`backend/services/resource_pool.py`、`backend/api/routes/resource_pools.py`
- 派发与准入：`backend/services/plan_wifi.py`、`backend/services/plan_dispatcher_core.py`、`backend/services/plan_dispatcher_sync.py`、`backend/api/routes/plans.py`
- 既有联网脚本：`backend/agent/scripts/connect_wifi/connect_wifi.py`、`backend/agent/scripts/monkey_setup/monkey_setup.py`
- Agent 提权与能力：`backend/agent/stp_agent_priv.py`、`backend/agent/contracts/capabilities.py`、`backend/models/host.py`
- 前端管理面：`frontend/src/pages/wifi/WifiPage.tsx`、`frontend/src/layouts/navItems.ts`
