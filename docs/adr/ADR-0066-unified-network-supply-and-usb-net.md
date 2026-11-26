# ADR-0066：统一网络供给模块（WiFi 与 USB 反向供网）及出口策略预留

- 状态：Proposed v0.2（2026-11-26 起草 v0.1，同日按 PR #4185 第一轮独立复核收口四项 P1 阻塞与三项 P2 契约补充修订为 v0.2；待二轮复核与 Owner 裁决，业务代码尚未实施）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-11-26
- 版本记录：
  - v0.2（2026-11-26）：按 PR #4185 一轮独立复核（`52f3242c2`）修订 D2–D8 与 §5——补齐手动供网与全类型任务基于 `DeviceLease(MAINTENANCE)` 的同事务原子互斥、先记账后变更与回收/在途自愈串行+ADR-0061 欠账阻断 VBUS 刷机、wrapper root 属主授权+物理 USB 口身份绑定+三层网桥隔离、`usb_net` 真实 DNS/出口与默认路由核验、单机跨池硬上限事务口径、双向出口匹配与新旧步骤混排兼容、WiFi 变更快照回收与视图全量覆盖。
  - v0.1（2026-11-26）：初稿，完成架构分支对齐与本机双机实证。
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
  [ADR-0061](./ADR-0061-device-return-contract.md)（设备归还契约与欠账租约）；
  [ADR-0063](./ADR-0063-vbus-cycle-flash-without-adb.md)（`vbus_cycle` 刷机与检查时机边界）；
  `backend/models/resource_pool.py`、`backend/services/resource_pool.py`、`backend/services/admission_pump.py`、`backend/services/lease_manager.py`、`backend/services/plan_wifi.py`、`backend/services/plan_dispatcher_core.py`、`backend/services/plan_dispatcher_sync.py`、`backend/agent/stp_agent_priv.py`、`frontend/src/pages/wifi/WifiPage.tsx`

---

## 1. 背景与实测证据（2026-11-26，基线 `origin/main@7d25b3b04`）

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
| F6 | `backend/agent/stp_agent_priv.py`（ADR-0037）是唯一被 NOPASSWD sudoers 授权的提权入口，其信任边界建立在 root 属主配置文件冻结而非 Agent 可写配置；`Host.agent_capabilities`（#4044）已在每拍心跳权威声明当前 Agent 实例支持的能力列表。 | `stp_agent_priv.py`、`ADR-0037`、`host.py` |
| F7 | `admission_pump.py:admission_transaction` 采用固定锁序 `PlanRun → PlanRunHost → Host(ORDER BY host_id) → Device(ORDER BY device_id)` 校验活跃作业与 `DeviceLease(ACTIVE)`；`lease_manager.py` 通过 `device.lease_generation` 自增提供 fencing token；ADR-0061 D5 使用 `DeviceLease(MAINTENANCE)` 承载归还欠账并在派发前阻断 VBUS 刷机（ADR-0063 D4/D5）。 | `admission_pump.py`、`lease_manager.py`、`ADR-0061`、`ADR-0063` |

---

## 2. 决策

> 以下 D1–D8 节规则句遵循 [ADR-0059](./ADR-0059-contract-and-sop-writing-conventions.md) 写作约定：仅使用「必须 / 不得 / 建议 / 可以」，并明确执行者。

### D1 顶层架构与正交分层（网络供给 × 接入子模块 × 出口策略）

1. 控制面与前端必须将既有「WiFi 资源池」升级为统一的「网络供给（Network Supply）」域，并沿用 `resource_pool` 表作为供给池配置的唯一事实源，不得为不同接入方式拆分平行资源池表。
2. `resource_pool.resource_type` 必须收敛为受控枚举，包含两个平级接入子模块：
   - `"wifi"`：设备通过 `wlan0` 接入无线 AP；
   - `"usb_net"`：设备通过 USB RNDIS（`rndis0 → usb0`）接入所属 Agent host 的本地 NAT 网桥。
3. 出口策略必须作为与接入方式正交的配置属性 `config.egress` 存放于 `resource_pool.config` 中，合法值枚举为 `"direct"`（缺省，普通直连）与 `"vpn"`（预留，YouTube 等海外访问出口）。历史记录缺少 `config.egress` 键时，控制面必须将其等同于 `"direct"` 处理。
4. Phase 2 启用 `"vpn"` 出口时，平台必须优先采用**网关侧出口分流**（`usb_net` 在 Agent host 侧经独立出口网桥或策略路由导向透明代理/TUN；`wifi` 指向已配置海外旁路由的专属 SSID），控制面与 Agent 不得将「在测试机上安装并分发第三方 VPN 应用及订阅凭据」作为平台主路径。
5. 未启用网络供给的专项（如离线 Monkey、Aging DDR、PowerCycle、刷机）不得消耗任何网络供给池配额，Agent 也不得为此类设备修改 USB 复合模式或 WiFi 状态。

### D2 生命周期、设备占用原子互斥、单机配额与手动调试守卫

1. 网络供给必须支持「任务级按需启停（主路径）」与「管理员手动调试启停（辅助路径）」两种生命周期入口：
   - **任务级入口**：PlanRun 在准入物化事务内分配供给池名额，在 `init` 开启供给，在 `teardown` 及任何异常终态按 D4 强回收；
   - **手动调试入口**：管理员可以在「网络供给」管理页对当前空闲的未退役设备手动开启或断开临时调试供网，也可以对异常滞留的供网会话执行强制释放或按 ADR-0061 人工放行。
2. 控制面必须新增设备级活跃供网事实表 `device_network_session`（遵循单数表名规范），作为「已启用供网设备」视图与状态追踪的唯一权威；每条记录必须包含 `id`、`device_id`、`host_id`、`resource_pool_id`、`source`（`"job"` | `"manual"`）、`job_instance_id`（`source="job"` 时非空，`source="manual"` 时为空）、`lease_id`（关联 `device_leases.id`）、`generation`（写入时的 `device.lease_generation` fencing token）、`resource_type`、`egress`、`legacy_path`（布尔值，标识存量 `connect_wifi`/`monkey_setup` 路径）、`status`（`"PROVISIONING"` | `"ACTIVE"` | `"RELEASING"` | `"RELEASE_FAILED"` | `"RELEASED"`）、`assigned_ip`、`prior_network_snapshot`（JSONB，记录变更前 WiFi 状态等）、`created_by`、`expires_at`（`source="manual"` 时必填，默认 `now + 4h`）与 `released_at`。
3. 控制面必须通过部分唯一索引强制同一台设备同一时刻至多持有一条未释放（`status IN ('PROVISIONING', 'ACTIVE', 'RELEASING', 'RELEASE_FAILED')`）的 `device_network_session` 记录。
4. **设备占用原子互斥（覆盖离线任务、刷机、VBUS 刷机与归还欠账）**：
   - 手动开启供网必须复用 ADR-0019 `DeviceLease`（`lease_type = MAINTENANCE`，`reason = "manual_network_supply"`，`holder = <admin_username>`）作为设备占用载体，不得仅凭 `device_network_session` 独立表达占用；
   - 手动开启接口必须按与 `admission_pump.py:admission_transaction` 一致的无死锁锁序（`Host FOR UPDATE` → `Device FOR UPDATE` → 目标 `ResourcePool FOR UPDATE`）开启单个数据库事务，并在锁内原子完成全部校验：① 设备未退役且在线；② 无活跃 `JobInstance`（`PENDING` / `RUNNING` / `UNKNOWN`）；③ 无任意类型的 `ACTIVE` `DeviceLease`（包括运行中 `JOB`/`SCRIPT` 租约、ADR-0061 归还欠账 `MAINTENANCE` 租约及其他维护租约）；④ 无未释放 `device_network_session`；⑤ 满足本条第 6 款的跨池单机与池并发配额；
   - 校验通过后，控制面必须在同一事务内推进 `device.lease_generation`、写入 `DeviceLease(MAINTENANCE)` 并写入绑定该 `lease_id` 与 `generation` 的 `device_network_session(source="manual", status="PROVISIONING")`；
   - `admission_transaction` 在持有 `Host FOR UPDATE` 与 `Device FOR UPDATE` 锁期间，必须经 `_classify_dispatch_devices_sync` 同步拒绝任意存在 `ACTIVE` `DeviceLease`（自然拦截手动供网 `MAINTENANCE` 租约）或未释放 `device_network_session` 的目标设备；上述互斥必须对联网任务、普通离线任务、普通刷机任务与 `vbus_cycle` 刷机任务同等生效；
   - PlanRun 触发期（入队前）控制面可以做前置快速提示（明确返回手动调试供网占用人并提示先释放），但最终互斥仲裁必须由上述同事务锁与 `DeviceLease` 唯一索引保障。
5. **手动调试超时与代次守卫**：
   - 管理员可以对未释放的 `source="manual"` 会话显式续期（单次续期步长与总时长上限由控制面配置约束）；
   - 当 `source="manual"` 会话超过 `expires_at` 时，控制面定时巡检必须校验 `(session_id, generation)` 匹配后将其状态推进为 `"RELEASING"` 并下发回收指令；
   - 在回收未获得 D4 第 4 条双侧确认前，控制面不得释放该会话绑定的 `DeviceLease(MAINTENANCE)`，也不得将 `device_network_session` 标记为 `"RELEASED"`。
6. **跨池单机配额与池并发配额原子口径**：
   - 每台 Agent host 的 `usb_net` 绝对并发硬上限 `usbnet_max_devices`（缺省 `8`，范围 `1–32`）必须由该主机 root 属主配置文件权威定义（见 D6 第 1 条），并由 Agent 随心跳上报控制面存入 `Host` 显式列（不得写进 `Host.extra` 裸键）；单个逻辑 `usb_net` 池的 `config.max_devices_per_host` 作为池级软上限，该池在某主机上的生效上限必须取 `min(host.usbnet_max_devices, pool.config.max_devices_per_host)`；
   - 无论任务准入物化还是手动开启，控制面必须在持有 `Host FOR UPDATE` 与 `ResourcePool FOR UPDATE` 锁的同一事务内，按**分配后的数量**执行三项原子校验：① 本宿主跨全部 `usb_net` 池的未释放会话数（含 `source="job"` 与 `source="manual"`，覆盖 `PROVISIONING` / `ACTIVE` / `RELEASING` / `RELEASE_FAILED`）加上本次该宿主待新增 `usb_net` 设备数不得超过 `host.usbnet_max_devices`；② 目标池在本宿主的未释放会话数加上本次待新增数不得超过该池在宿主的生效上限；③ 目标池全局活跃 `JobInstance` 占用与未释放手动会话之和加上本次待新增数不得超过 `pool.max_concurrent_devices`。

### D3 Plan 编排、API 契约、出口匹配与向后兼容

1. PlanRun 触发接口（`POST /api/v1/plans/{plan_id}/run` 及干跑预览端点）与 `plan_run.run_context` 必须新增 `network_pool_id: Optional[int]`；控制面必须继续接受既有 `wifi_pool_id` 作为兼容别名，当请求同时携带 `network_pool_id` 与 `wifi_pool_id` 且两者不等时，控制面必须返回 `400 Bad Request`。AI 助手派发工具（ADR-0031-A）必须同步兼容两键并沿用「必须显式传 ID、不得自动推断」原则。
2. 平台必须新增统一脚本族 `setup_network` 与 `teardown_network`（按 ADR-0051 以独立发布单元登记与版本化）：
   - `setup_network` 的步骤参数必须支持可选能力约束 `required_type`（`""` | `"wifi"` | `"usb_net"`）、`required_egress`（`"direct"` | `"vpn"`，缺省 `"direct"`）、`require_validated`（布尔值，缺省 `false`）、`disable_wifi_on_usb_net`（布尔值，缺省 `true`）与 `required`（布尔值，缺省 `false`）；未指定 `network_pool_id` 且 `required=false` 时，`setup_network` 必须跳过并回报 `skipped=true`；
   - `setup_network` 必须在同一 Plan 模板下根据派发器注入的池类型自动分派至 WiFi 连接实现或 USB 反向供网实现，使联网 Monkey、在线视频待机等专项在启动时可以通过选择不同资源池切换接入方式，无需按接入方式维护重复 Plan；
   - `teardown_network` 必须与 `setup_network` 成对出现于 `teardown` 阶段，支持可选参数 `disconnect_on_teardown`（布尔值，缺省 `false`，仅作用于 `wifi` 子模块；`usb_net` 无论该参数取值如何都必须强制全量回收）。
3. **Phase 1 出口闸门与双向匹配校验**（升级 `backend/services/plan_wifi.py` 为通用网络供给准入模块）：
   - Phase 1 中，控制面必须拒绝创建、启用或分配 `resource_type="usb_net"` 且 `config.egress="vpn"` 的资源池（该组合留待 Phase 2 宿主网关 VPN 出口验收通过后解锁）；`resource_type="wifi"` 可以登记 `config.egress="vpn"`（用于已配置海外旁路由的专属 SSID）；
   - 控制面必须强制执行**双向出口匹配**：步骤声明 `required_egress="direct"`（含缺省）时，控制面不得分配 `config.egress="vpn"` 的池；步骤声明 `required_egress="vpn"` 时，控制面不得分配 `config.egress="direct"` 的池；
   - 存量 `connect_wifi` 未显式传入 `network_pool_id`/`wifi_pool_id` 触发自动选池时，控制面必须仅从 `resource_type="wifi"` 且 `config.egress == "direct"`（缺省视同 `"direct"`）的活跃池中按负载选取，不得误选 `egress="vpn"` 池；
   - 存量 `monkey_setup` 未传入池且步骤参数未硬编码 SSID 时，必须保持 v2.0.0 跳过语义；
   - 同一 Plan 同时包含 `setup_network` 与存量 `connect_wifi` 或 `monkey_setup` 启用步骤时，控制面必须对全部启用网络步骤的 `required_type` 与 `required_egress` 取交集（存量 `connect_wifi` 隐含 `required_type="wifi"`，存量 `monkey_setup` 注入池时隐含 `required_type="wifi"`）；若交集为空（例如 `setup_network(required_type="usb_net")` 与 `connect_wifi` 同存），控制面必须在 Plan 保存与触发校验期直接拒绝该非法组合；
   - 不含任何启用网络消费步骤的 Plan 携带 `network_pool_id` / `wifi_pool_id` 时，控制面必须直接拒绝请求。
4. **WiFi 变更快照与回收语义**：
   - `setup_network`（`wifi` 分支）与手动调试 `wifi` 供网在执行任何 `svc wifi enable` 或 `connect-network` 之前，必须读取并持久化设备原 `wifi_on` 状态与原连接 SSID 到 `device_network_session.prior_network_snapshot`；
   - 任务级 `teardown_network`（`wifi` 分支）默认仅释放平台池分配与 `device_network_session`，不强制关闭 WiFi；当步骤显式设置 `disconnect_on_teardown=true` 且本次任务确曾开启 WiFi 或切换 SSID 时，`teardown_network` 必须仅撤销自身造成的变更（断开新建连接或恢复 `wifi_on=0`）；
   - 手动调试 `wifi` 会话在到期或管理员释放时，Agent 必须按 `prior_network_snapshot` 撤销本次手动会话造成的 WiFi 开启或 SSID 切换并回读确认，随后控制面才可以释放 `device_network_session` 与其绑定的 `MAINTENANCE` 租约。
5. 资源池配置剥密与脱敏必须保持 fail-closed 白名单机制：`GET /api/v1/resource-pools/available` 与 `loads` 只可以暴露白名单内的非机密键（`wifi` 延续 `ssid`、`band`、`router_ip`、`mac_filter`，新增通用键 `egress` 与 `usb_net` 公开键 `bridge_name`、`subnet_cidr`、`max_devices_per_host`）；`password` 及未来任何代理凭据不得进入普通用户端点、审计明文或前端可见步骤参数。

### D4 `usb_net` 准入、真实出口核验与强回收契约

1. Phase 1 的 `usb_net` 仅承诺已 root（设备端 `id -u == 0`）的 Android 设备（MTK 平台先行；展锐机型在完成实机 `svc usb setFunctions rndis/ncm` 与 `EthernetTracker` 探测并登记兼容结论前，实施者不得放开其 `usb_net` 准入）。
2. 当 PlanRun 或管理员手动调试请求为设备启用 `usb_net` 时，控制面与 Agent 必须依次校验：
   - 所属 Agent host 已在最近一次心跳的 `Host.agent_capabilities` 中声明 `"usb_net"`（前提见 D6 第 4 条，含 root 属主授权）；
   - 目标设备具备 root 权限（`id -u == 0`）且 `dumpsys ethernet` 报告 `Ethernet State: enabled`。
   任一条件不满足时，控制面或 `setup_network` 脚本必须 fail-closed 并返回可操作错误（提示改选 `wifi` 池或检查主机授权），不得静默降级或挂起等待。
3. **先登记回收义务再变更设备，并以真实业务出口与默认路由作为 `ACTIVE` 判据**：
   - 在向设备下发 `svc usb setFunctions rndis` 之前，控制面必须已完成 `device_network_session(status="PROVISIONING", generation=...)` 落库，且 Agent 必须先在本地持久化受控记录（包含 `session_id`、`generation`、`serial`、`usb_devpath`、`recovery_required=true`、`auto_heal_enabled=true`），确保此后任一时刻发生 Agent 崩溃或设备掉线均有回收锚点；
   - `setup_network`（`usb_net` 分支）必须依次执行：经 `stp-agent-priv usbnet-ensure-bridge` 确保目标网桥、隔离规则与 DHCP-only `dnsmasq`（`--port=0`）就绪 → 设备执行 `svc usb setFunctions rndis` 并等待该串号 ADB 重连（默认 `20s` 超时）→ 获取该设备物理 USB 路径与对应接口身份后经 `stp-agent-priv usbnet-attach` 挂桥 → 设备将 `rndis0` 改名为 `usb0` 并拉起（若固件原生为 `usb0` 则跳过改名）→ 回读 `usb0` 已获派目标子网 IPv4 且能够 `ping` 通宿主网桥网关；
   - **真实出口与默认路径核验（防假阳性 `ACTIVE`）**：仅获派 IP 与网关可达不得判为 `ACTIVE`。脚本必须追加完成两项有界核验后才可以上报成功并将 `device_network_session` 置为 `"ACTIVE"`：
     1. **绑定 `usb0` 的 DNS 与业务出口探测**：经 `usb0` 完成可配置目标的有界 DNS 解析与 HTTP/TCP 连通探测（单次默认超时 `5s`，不经过其他网卡），证明宿主机 NAT、转发链与上游 DNS 全链可用；
     2. **默认网络路径核验**：对依赖系统默认网络的场景，脚本必须回读 `dumpsys connectivity` 确认 `Active default network` 指向 `usb0`（`Transports: ETHERNET`）。若 `wlan0` 或蜂窝网络仍占据默认网络，当 `disable_wifi_on_usb_net=true` 时脚本必须先快照原 `wifi_on` 状态再关闭 `wlan0` 并复验默认网络切换至 `usb0`；若仍未切换或 `disable_wifi_on_usb_net=false`，脚本必须 fail-closed 报错并触发回收，不得报 `ACTIVE`；
     3. **强 `VALIDATED` 专项独立门控**：当步骤声明 `require_validated=true` 时，脚本必须额外核验 `usb0` 网络能力包含 `VALIDATED`，未满足时必须失败。
4. **双侧确认强回收与未确认占留（前置阻断 VBUS 刷机）**：
   - 任何 `usb_net` 会话（无论任务正常结束、步骤失败、平台中止、部分开启崩溃、手动 TTL 过期还是管理员强释）执行回收时，Agent 必须按 D5 第 2 条先撤销自愈资格并与在途自愈串行，再依次执行：① 设备侧恢复 `svc usb setFunctions` 纯 `adb`（若此前因 `disable_wifi_on_usb_net` 关闭了 WiFi 则按快照恢复）并回读确认 `sys.usb.state == adb`；② 宿主侧经 `stp-agent-priv usbnet-detach` 确认对应网卡已摘桥（接口已随设备切回纯 `adb` 消失且网桥无该成员视为宿主侧已确认）；
   - 只有当「设备侧纯 `adb` 回读确认」与「宿主侧摘桥确认」**两者同时达成**，且控制面核验 `(session_id, generation)` 匹配时，控制面才可以将 `device_network_session` 置为 `"RELEASED"` 并释放绑定租约，随后 Agent 才可以从本地受控表移除该记录；
   - **任务路径未确认**：若回收时设备不可达、回读失败、Agent 崩溃或 `setup_network` 在切 `rndis` 后中途失败，控制面必须在将 `JobInstance` 置为终态的同一数据库事务内，按 **ADR-0061 D5** 将该设备的 `JOB` 租约原子转换为 `MAINTENANCE` 归还欠账租约并登记 `usb_net` 专项未关闭欠账条目（推进 `lease_generation`）；由此，后续任何 `vbus_cycle` 刷机任务在**派发与认领阶段**即被 ADR-0063 D4/D5 的 `device_return_debt` 阻断，绝不会漏至刷机后的推迟探针；设备恢复可达后由 Agent 按 ADR-0061 自动清偿（回切纯 `adb` + 回读确认 + 摘桥）或由管理员/Owner 人工放行；
   - **手动路径未确认**：若手动会话在到期或管理员强释时设备不可达或回收回读失败，控制面必须将 `device_network_session` 保持在 `"RELEASING"` 或 `"RELEASE_FAILED"` 且**不得释放其 `DeviceLease(MAINTENANCE)`**，使设备继续保持不可派发（阻断普通、刷机与 VBUS 任务），直至设备重连自动完成双侧确认回收，或由管理员/Owner 执行带原因与审计记录的 ADR-0061 人工放行；
   - **迟到回执与代次校验**：任何迟到的上线回执、回收回执、TTL 巡检或强释指令都必须校验 `(session_id, generation)`；与当前会话不匹配或代次过期者必须幂等丢弃，不得误释后续新会话或新租约。

### D5 Agent 常驻轻量自愈（与回收串行及失联清偿）

1. Agent 必须在本地运行目录维护持久化的受控 `usb_net` 设备表（每条记录含 `session_id`、`generation`、`serial`、`usb_devpath`、`status`、`auto_heal_enabled`、`recovery_required`），随任务开启/回收及控制面手动会话指令原子落盘更新。Agent 不得对不在该受控表中的任何设备执行 `svc usb setFunctions` 或网卡操作。
2. **回收与在途自愈串行协议**：
   - Agent 必须为每台设备维护本地互斥锁，所有自愈动作与回收动作在触碰设备或调用 `stp-agent-priv` 前都必须获取该设备锁；
   - 收到回收触发（`teardown_network`、中止、手动强释、TTL 到期或本地 `recovery_required=true` 的崩溃遗留会话）时，Agent 必须先将该受控表条目的 `auto_heal_enabled` 置为 `false`、`status` 置为 `"RELEASING"` 并持久化（**不得在双侧确认前移出受控表**），再持设备锁等待或终止当前在途自愈，然后执行 D4 第 4 条回收与回读；确认成功并收到控制面释放确认后，Agent 才可以移出本地受控表。
3. 仅当受控表条目满足 `auto_heal_enabled == true` 且 `status == "ACTIVE"`，且目标设备在长稳运行中重启重连后缺失 `usb0` 或未绑定宿主网桥时，Agent 才可以持设备锁按 D4 第 3 条重走恢复流程（切 `rndis` → 校验物理 USB 口并挂桥 → 改名 `usb0` → DNS/出口与默认路径复验），并携带 `(session_id, generation)` 上报恢复事件与新 `assigned_ip`。
4. Agent 必须对单台设备的连续自愈失败设置指数退避与有界上限（例如连续 3 次失败后停止高频重试并上报告警事件），不得在设备反复重启时形成重枚举风暴或阻塞其他设备心跳。
5. 当受控表条目处于 `status IN ("RELEASING", "RELEASE_FAILED")` 且目标设备从失联恢复可达时，Agent 必须持设备锁自动补执行 D4 第 4 条回收与双侧确认，并携带 `(session_id, generation)` 向控制面回报清偿。

### D6 Agent host 提权边界、目标校验、网桥隔离与跨主机拓扑（修订 ADR-0037 草案项）

1. **Root 属主授权锚点（对齐 ADR-0037 §4 不变量）**：Agent host 侧是否授权启用 `usb_net`、已授权的 `egress` 列表（Phase 1 仅限 `direct`）、受控网桥名/子网 CIDR、上游 DNS 白名单与单机硬上限 `usbnet_max_devices`（缺省 `8`），必须写入 root 属主、非 group/other 可写、非 symlink 的配置文件（`/etc/stp-agent-priv.conf` 扩展节或同级 `/etc/stp-usbnet.conf`，父目录为 root 属主真实目录），由运维经 Ansible/root 安装期下发。Agent 侧 `backend/agent/settings.py` 中的 `STP_USB_NET_ENABLED`（默认 `false`）仅作为进程运行开关；若 root 属主配置未显式授权 `usbnet_enabled=true`，`stp-agent-priv` 必须对全部 `usbnet-*` 子命令 fail-closed 退出。
2. **单一提权白名单子命令**：Agent host 侧全部需要 root / `CAP_NET_ADMIN` 的网络操作必须收敛至 `/usr/local/sbin/stp-agent-priv`（ADR-0037），Agent 与脚本不得新增独立 root systemd 服务，也不得使用 `sudo sh -c`：
   - `usbnet-ensure-bridge --egress <direct|vpn>`：幂等创建 root 配置绑定的受控网桥（`br-stpusb-dir` / 预留 `br-stpusb-vpn`）、配置固定保留子网、安装本条第 4 款三层防火墙规则，并管理严格绑定该网桥的 DHCP-only `dnsmasq`（`--port=0 --bind-interfaces`）；
   - `usbnet-attach --iface <ifname> --usb-devpath <bus-port> --egress <direct|vpn> [--serial <serial>] [--mac <mac>]`：核验目标身份后设为 NetworkManager 未托管、清空旧 IP、挂入目标网桥并启用网桥口 L2 隔离；
   - `usbnet-detach --iface <ifname>`：校验目标网卡属于受控网桥成员与 USB 子系统后幂等摘桥（已不存在视为幂等成功）；
   - `usbnet-teardown --egress <direct|vpn>`：主机关闭能力或维护时停用 `dnsmasq`、摘除受控防火墙规则并删除受控网桥；
   - `usbnet-status`：只读输出当前授权状态、单机硬上限、受控网桥、成员列表、租约与 `dnsmasq` 健康态。
3. **目标物理 USB 口与接口身份强绑定校验**：`stp-agent-priv` 的所有 `usbnet-*` 解析器必须设置 `allow_abbrev=False`，且 `usbnet-attach` 必须经 `/sys/class/net/<iface>/device` 逐级 `O_NOFOLLOW` 核验以下全部条件后才可以挂桥：
   - `--iface` 符合合法网卡字符集且非宿主主上联口、`lo`、`docker0` 或任意网桥；
   - 目标网卡内核驱动必须为白名单驱动（`rndis_host` 或 `cdc_ncm`）；
   - 目标网卡对应的上游 USB 设备 `devpath`（总线-端口拓扑路径）必须与 `--usb-devpath` 全等；提供 `--serial` 或 `--mac` 时必须与内核 sysfs 实读值全等；
   - 核验通过后必须校验 `ifindex` 未发生变化再执行挂桥；任一核验失败（如误传同机其他设备网卡、或重枚举后接口名被另一物理口复用）必须非零退出并拒绝操作。
4. **三层默认拒绝网桥访问边界**：`usbnet-ensure-bridge` 与 `usbnet-attach` 必须落实以下隔离策略，不得仅依赖 `DOCKER-USER` 转发链：
   - **测试机 → 宿主机本机服务（`INPUT` 链）**：对源自受控网桥 `br-stpusb-*` 的入站流量默认 `DROP`，仅显式放行 DHCP（`UDP 67/68`）、ICMP echo-request（用于网关连通自检）以及（若 root 配置显式启用本地 DNS 转发时）绑定该网桥 IP 的 `UDP/TCP 53`；必须阻断测试机访问宿主机 SSH、控制面 HTTP、PostgreSQL、Redis 及其他一切本机监听端口；
   - **测试机 → 宿主管理网段与内网（`FORWARD` / `DOCKER-USER` 链）**：`stp-agent-priv` 必须配置默认阻断规则，不得将 `br-stpusb-*` 流量转发至宿主所属内网管理网段、其他 Docker 网桥及 RFC1918 / link-local 地址段（仅白名单放行 root 配置中登记的上游 DNS 服务器 `UDP/TCP 53` 及 Phase 2 显式授权的 VPN 出口网关），其余仅放行发往公网的出站流量与 `RELATED,ESTABLISHED` 回包；
   - **同桥测试机二层互访（L2 隔离）**：`usbnet-attach` 必须对每个挂桥 USB 网卡执行 `bridge link set dev <iface> isolated on`，阻断同网桥多台测试机之间的二层互访。
5. **能力上报与跨主机逻辑池拓扑**：
   - 只有当主机 root 配置授权 `usbnet_enabled=true`、`STP_USB_NET_ENABLED=true`、内核具备 `rndis_host` 驱动、宿主已安装 `dnsmasq` 且 `stp-agent-priv usbnet-status` 自检通过时，Agent 才可以在心跳 `Host.agent_capabilities` 中声明 `"usb_net"`（常量定义于 `backend/agent/contracts/capabilities.py`，遵守 ADR-0054）并上报 `usbnet_max_devices`；
   - 一个逻辑 `usb_net` 资源池可以服务多台已声明 `"usb_net"` 能力的 Agent host（按 `host_group` 过滤；`host_group=NULL` 表示全部已授权开启主机），各主机使用各自独立的本地私有 NAT 子网（默认 `10.200.0.0/22`）就地桥接本机 USB 设备，使单次跨主机 PlanRun 可以复用同一个逻辑 `usb_net` 池。

### D7 前端「网络供给」管理面与权限边界

1. 前端必须将既有 `/wifi`（「WiFi 资源池」）升级为统一「网络供给」管理页面（建议主路由 `/network-supply`，并保留 `/wifi` 自动重定向以兼容旧链接），页面导航与管理写接口维持 `adminOnly: true`（延续 #2360 权限基线）。
2. 「网络供给」页必须提供两个主 Tab：
   - **供给池管理**：按 `resource_type`（`wifi` / `usb_net`）分类型创建、编辑、启停与查看并发负载，统一展示出口标签（`普通直连` / `VPN 出口`）、`host_group` 作用域与单机上限；
   - **已启用供网设备**：以 `device_network_session`（`status IN ('PROVISIONING', 'ACTIVE', 'RELEASING', 'RELEASE_FAILED')`）为权威数据源，**全量覆盖**新 `setup_network` 任务、手动调试会话以及存量 `connect_wifi`/`monkey_setup` 活跃任务（派发物化期为存量 WiFi 路径同步写入 `legacy_path=true` 的会话记录并在界面标注「存量 WiFi 路径」），不混入未启用供网的离线设备；展示设备串号、所属 Host、供网方式、出口策略、分配 IP、实时状态（含 `RELEASING`/`RELEASE_FAILED` 异常态）、来源（PlanRun 链接 / 手动调试人）与剩余有效期，并提供管理员**一键强制释放**、**调试续期**、**ADR-0061 人工放行入口**以及**对闲置设备手动开启调试供网**的操作。
3. PlanRun 发起弹窗中的网络选择器必须从原「WiFi 选择器」升级为「网络供给选择器」：调用 `GET /api/v1/resource-pools/available`，按当前 Plan 全部启用网络步骤的交集约束（`required_type` / `required_egress`）过滤并清晰标注池类型与出口标签，不得向非管理员暴露凭据字段。

### D8 显式不做（Out of Scope）与复议触发器

| 暂不做项 | 理由 | 复议触发器 |
|---|---|---|
| Phase 1 非 root 设备的本地 `VpnService`（如 `gnirehtet`）自动回退 | 引入辅助 APK 分发、系统授权弹窗与 VPN 槽位占用，违背「无 VPN 简单供网」核心诉求 | 出现规模化非 root 量产机必须走 USB 供网且无法扩容机房 WiFi AP 时 |
| Phase 1 生产开启 `usb_net` 的 `egress="vpn"` 透明代理链路 | 当前待机 YouTube 专项尚在筹备期，先冻结字段、双向出口匹配与架构边界，避免过早引入宿主机代理运维复杂度 | 在线视频待机专项正式立项并确认合规宿主出口节点与监控要求时 |
| 全机群默认开启 `usb_net` 常驻网桥 | 多数主机仅跑离线/刷机专项，无需额外网络守护与提权面 | 超过半数在线 Agent host 常态化承接联网专项时 |
| 移除既有 WiFi 模块与 `connect_wifi` / `monkey_setup` 存量路径 | MTBF、真实无线稳定性覆盖、非 root 与重启自连不可替代 | 仅当全部测试专项均不再验证 WiFi 驱动与无线场景时（预期不会触发） |

---

## 3. 备选方案与权衡

| 备选方案 | 放弃原因 |
|---|---|
| **方案 1：用 USB 反向供网彻底取代并移除 WiFi 模块** | 违背手机稳定性测试目标（摘除 `wlan0` 驱动/固件/功耗缺陷暴露面）、破坏 MTBF 套件（依赖真实 WiFi 与 `UiAutomatorTestData.xml`）、无法覆盖非 root 量产机。 |
| **方案 2：`adb reverse` + 宿主 HTTP 代理 + `settings put global http_proxy`** | 本机实测（F3）在离线设备上 `Active default network: none`，DNS/ICMP/UDP 全不通，多数 Android App 直接判无网，仅对极少数裸 HTTP 客户端有效。 |
| **方案 3：`gnirehtet`（`adb reverse` + 设备端本地 `VpnService`）** | 需安装与维护设备端 APK 及授权、占用唯一 VPN 槽位、与实机可能运行的诊断工具冲突，且用户态包转发吞吐显著低于内核态 USB RNDIS。 |
| **方案 4：独立 `stp-usbnetd` root systemd 服务** | 破坏 ADR-0037「单一提权入口」原则，额外增加一套跨主机安装、升级、健康探测与进程守护运维面。 |
| **方案 5：手动供网仅写 `device_network_session`，不持 `DeviceLease`** | v0.1 复核已用内存数据库复现：离线任务与刷机任务不写 `device_network_session`，两侧各自读空闲后可并发写入 `PROVISIONING` 会话与 `PENDING` Job；改用 ADR-0019 `DeviceLease(MAINTENANCE)` + 同锁序彻底闭合。 |
| **方案 6：仅靠 `DOCKER-USER` 放行转发、不设 `INPUT` 与 L2 隔离** | `DOCKER-USER` 只管 `FORWARD` 链，无法阻断测试机直连宿主机 SSH/DB/Redis/8000 等本机端口，也无法阻断同桥测试机互访。 |

---

## 4. 影响与风险

1. **安全与提权面（ADR-0037）**：`stp-agent-priv` 新增 `usbnet-*` 子命令前必须通过独立安全复核与边界单元测试（覆盖 root 属主未授权拒绝、非 USB 接口/非目标 `usb-devpath`/重枚举接口名复用拒绝、`INPUT`/`FORWARD`/L2 隔离规则验证）。
2. **与刷机 / VBUS 切电的交互（ADR-0061 / ADR-0063）**：`rndis,adb` 改变设备 USB PID（`0e8d:2005`）；D4 第 4 条确保任何未确认切回纯 `adb` 的任务或手动会话都保留 `MAINTENANCE` 占用或欠账租约，从而在 VBUS 刷机**派发与认领前**即阻断（不依赖刷机后探针），且全程不得修改 `persist.sys.usb.config`。
3. **USB 2.0 Hub 带宽竞争**：高密度 10/16 口 USB 2.0 Hub 共享 480 Mbps 带宽；D2 第 6 条与 D6 第 1 条通过 root 属主主机硬上限 `usbnet_max_devices`（跨池、任务+手动+未确认回收合并计数）严格兜底，建议优先在 USB 3.0 直连口或低密度 Hub 宿主机试点。
4. **国内直连无 `VALIDATED` 标志**：F4 实测普通 TCP/DNS/HTTP 正常，但强校验 `NET_CAPABILITY_VALIDATED` 的组件在纯国内 `direct` 出口下可能等待验证；D4 第 3 条通过可配置的 `require_validated` 显式分层门控，Phase 2 接入 `vpn` 出口后 `gstatic.com/generate_204` 将自然通过验证。
5. **跨芯片兼容缺口**：展锐机型（`unisoc`）的 USB gadget 函数名（`rndis`/`ncm`）与 `EthernetTracker` 接口过滤正则尚未真机探测，已列为 Phase 0 前置验证项。

---

## 5. 分期落地切片与验收门槛（按 ADR-0058 批次交付规划）

> 本 ADR 转 `Accepted` 后，按 ADR-0058 批次流程拆分实施单元，不一次性大改主干。以下逐阶段列出正向闭环与必测反例。

| 阶段 | 切片内容 | 验收门槛（含必测反例） |
|---|---|---|
| **Phase 0（前置探路与裁决）** | ① 完成本 ADR 与 ADR-0037 D10（`usbnet-*` 提权子命令）独立复核并转 `Accepted`；② 抽测 1 台展锐实机确认 `svc usb setFunctions rndis/ncm` 与 `EthernetTracker` 正则行为（决定展锐是否进 Phase 1）。 | ADR Accepted；展锐实机探测结论落文档。 |
| **Phase 1a（数据面与控制面契约）** | ① Alembic 迁移新增 `device_network_session` 表（含部分唯一索引与 `generation`/`legacy_path`/`prior_network_snapshot`）及 `Host.usbnet_max_devices` 显式列；② `resource_pool` 校验支持 `usb_net`、Phase 1 拒绝 `usb_net+vpn` 与双向出口匹配；③ 手动供网同事务获取 `DeviceLease(MAINTENANCE)` 与 `admission_transaction` 共用锁序；④ 跨池单机硬上限事务核算；⑤ `network_pool_id`（保留 `wifi_pool_id` 兼容）、新旧步骤混排交集校验与存量 WiFi 会话投影；⑥ 手动会话 TTL 过期巡检、代次校验与未确认保持占用逻辑。 | 后端测试全绿，必须覆盖以下反例：① **手动开启 vs 普通离线 PlanRun / 刷机 PlanRun / VBUS PlanRun 并发竞态**（同事务仅一方成功，绝不同时落 `PROVISIONING` + `PENDING`）；② **存在无活跃 Job 的归还欠账 `ACTIVE` 租约时**手动开启被拒；③ **跨两个 `usb_net` 逻辑池 + 手动 + `RELEASING` 占用合并触顶 `host.usbnet_max_devices`** 时拒绝新分配；④ **`connect_wifi` 未传池自动选 `wifi(direct)`（不误选 `vpn` 池）、`monkey_setup` 无 SSID 跳过、新旧步骤混排约束交集与矛盾拒绝**；⑤ **过期 `(session_id, generation)` 迟到回执/TTL 强释**不得误释新会话；⑥ 凭据剥密白名单零回归。 |
| **Phase 1b（提权边界与 Agent 供网闭环）** | ① `stp-agent-priv` 新增 `usbnet-*` 白名单子命令、root 属主授权校验、物理 USB 口与接口身份核验及 `INPUT`/`FORWARD`/L2 三层隔离与边界单测；② `backend/agent/settings.py` 收敛 `STP_USB_NET_*`（默认关）与心跳上报 `"usb_net"` + `usbnet_max_devices`；③ 新增脚本族 `setup_network` / `teardown_network`（含 `wifi` 变更快照按需回退与 `usb_net` 真实 DNS/出口/默认路由校验）；④ Agent 本地受控表持久化、设备锁串行回收与在途自愈、失联清偿与 ADR-0061 归还欠账对接。 | Wrapper 单测 + 在 1–2 台试点主机完成双机真机验收，必须覆盖以下反例：① **root 属主未授权时** wrapper 全拒；② **误传同机另一设备 USB 网卡 / 重枚举接口名复用**被 wrapper 拒绝；③ **测试机访问宿主本机端口（如 SSH / 8000）、宿主内网管理段 IP、同桥另一台测试机**全部被阻断；④ **网关通而外网/NAT/DNS 断**时 `setup_network` 必须失败且不得转 `ACTIVE`；⑤ **WiFi 仍为默认网络**时按契约关 WiFi 复验或 fail-closed，不得假成功；⑥ **刚切 RNDIS 后模拟 Agent 崩溃 / 回收时拔线失联**，确认保留欠账/占用并在**后续 VBUS 刷机派发前被阻断**，重连后自动清偿回纯 `adb` 再跑刷机零冲突；⑦ **在途自愈与 `teardown`/强释并发**无竞态，回收后不二次切回 RNDIS。 |
| **Phase 1c（前端「网络供给」管理面）** | ① `/wifi` 升级为「网络供给」（`/network-supply` + `/wifi` 重定向），落地「供给池管理」与「已启用供网设备」双 Tab（含存量 WiFi 路径标注、`RELEASING`/`RELEASE_FAILED` 态展示、手动调试启停/续期/强释/人工放行）；② PlanRun 发起弹窗升级为「网络供给选择器」。 | Vitest + 隔离预览实机截图核验（普通用户无凭据泄漏、视图覆盖存量与新路径、冲突/欠账状态提示清晰）。 |
| **Phase 2（按需激活 VPN 出口）** | ① 解锁 `usb_net` 的 `egress="vpn"` 准入并在试点宿主网关侧打通 `br-stpusb-vpn` 透明代理/TUN 出口与隔离策略；② 在线视频待机专项 Plan 声明 `required_egress="vpn"` + `require_validated=true` 跑通 YouTube 定时播放闭环。 | 待机专项真机连续 24h YouTube 播放验收通过，验证 `VALIDATED` 点亮且手机零 VPN App、零宿主本机端口暴露。 |

---

## 6. 关联实现与文档

- 数据模型与资源池服务：`backend/models/resource_pool.py`、`backend/services/resource_pool.py`、`backend/api/routes/resource_pools.py`
- 准入事务、租约与归还：`backend/services/admission_pump.py`、`backend/services/lease_manager.py`、`backend/agent/device_return.py`
- 派发与准入校验：`backend/services/plan_wifi.py`、`backend/services/plan_dispatcher_core.py`、`backend/services/plan_dispatcher_sync.py`、`backend/api/routes/plans.py`
- 既有联网脚本：`backend/agent/scripts/connect_wifi/connect_wifi.py`、`backend/agent/scripts/monkey_setup/monkey_setup.py`
- Agent 提权与能力：`backend/agent/stp_agent_priv.py`、`backend/agent/contracts/capabilities.py`、`backend/models/host.py`
- 前端管理面：`frontend/src/pages/wifi/WifiPage.tsx`、`frontend/src/layouts/navItems.ts`
