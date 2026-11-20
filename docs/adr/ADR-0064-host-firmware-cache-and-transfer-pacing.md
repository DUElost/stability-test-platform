# ADR-0064：Agent host 固件本地缓存、全局传输限流与心跳 NFS 隔离

- 状态：Proposed v0.1（2026-11-20 起草；#4076 专题讨论结论已由 Owner 确认，待独立复核后转 Accepted）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-11-20
- 决策者：Owner
- 标签：刷机, 缓存, NFS, 心跳韧性, Agent 主机
- 归属域：semantic-ownership flash-tool
- 关联：[#4076](https://github.com/DUElost/stability-test-platform/issues/4076)（专题讨论与 Owner 裁决）；
  [#4054](https://github.com/DUElost/stability-test-platform/issues/4054)（控制面 I/O 饱和拖垮 NFS 刷机与 Agent 心跳）；
  [#4075](https://github.com/DUElost/stability-test-platform/issues/4075)（MTK 535 实战实证；非缓存的失败重试与错误分类拆分独立推进）；
  [#4042](https://github.com/DUElost/stability-test-platform/issues/4042)（固件上架前置校验）；
  [#3995](https://github.com/DUElost/stability-test-platform/issues/3995)（刷机稳定性 Epic）；
  [#4006](https://github.com/DUElost/stability-test-platform/issues/4006) §8（展锐 BSL 私有缓存无回收者）；
  [#3498](https://github.com/DUElost/stability-test-platform/issues/3498)（脚本公共能力 L1/L4 分层）；
  [#3779](https://github.com/DUElost/stability-test-platform/issues/3779)（冻结 `pipeline_engine` 按族名特判）；
  [#3804](https://github.com/DUElost/stability-test-platform/issues/3804)（Agent 根分区写满）；
  [ADR-0042](./ADR-0042-settings-convergence-and-bare-read-boundary.md)（分域 Settings）；
  [ADR-0051](./ADR-0051-release-unit-and-content-addressing.md)（发布单元与内容寻址，§7 Revisit #1）；
  [ADR-0058](./ADR-0058-planned-batch-execution.md)（批次交付流程）；
  [ADR-0063](./ADR-0063-vbus-cycle-flash-without-adb.md)（`vbus_cycle` 刷机安全边界）；
  [存储与部署角色](../design/2026-storage-roles-and-aliases.md)（§4.2 / §4.4）

## 1. 背景与证据（2026-11-20，基线 `origin/main@aec9ea70a`）

### 1.1 现场问题

近两次集中刷机（2026-11-18 #4054、2026-11-19～11-20 #4075）与现行代码直读暴露出同一问题面的四个连锁故障：

1. **千兆口并发抢占拖死 BROM/DA 临界区**：`flash_firmware`（MTK V5/V6）与 `flash_firmware_unisoc`（展锐 Fastboot）在手机进入刷机态后跨 NFS 实时流式读取 4–17 GB 固件包。15+ 台 Agent host 同时开刷瞬间打满控制面共享千兆网卡（有效吞吐 ~110 MB/s），单机拉速暴跌，8 台设备在 DA 写包阶段撞上 `timeout_seconds=1200` 被强杀并滞留 `0e8d:2000`。
2. **同主机多设备重复拉取与展锐 BSL 缓存假命中**：一台主机串行刷 16–20 台设备时，MTK 与展锐 Fastboot 每次都重读整包；展锐 BSL（`backend/agent/scripts/flash_firmware_unisoc_bsl/flash_firmware_unisoc_bsl.py`）虽建有 `/var/tmp/stp_flash` 缓存，但每次命中都调用 `_sha256_file(dst) == _sha256_file(src)` 跨 NFS 重读源文件算全量哈希，且拷贝发生在主机刷机排他锁 `/tmp/stp-flash-firmware.lock` 内部、无空间守卫与淘汰回收（#4006 §8）。
3. **NFS 拥塞连坐 Agent 心跳**：`backend/agent/heartbeat_thread.py` 在心跳主线程同步调用 `backend/agent/heartbeat.py` 的 `check_mounts`（`os.path.ismount(point) and os.path.exists(point)`）。控制面 I/O 饱和时，hard mount 的 NFS 路径进入内核不可中断睡眠（`D` 状态），直接冻结心跳上报与租约续期，触发全网误判 `OFFLINE` 与在跑 Job 判 `UNKNOWN`（已命中 `docs/design/2026-storage-roles-and-aliases.md` §4.4 的复议触发器）。
4. **本地盘拓扑异构且无统一配额治理**：在线 Agent host 中多数拥有充裕 SSD 与独立 HDD，但也存在 37–48 GB 小系统盘尾机（如 `.33` 仅余 4.83 GB）以及仅配备单块 HDD 的主机；无统一配额、水位底线与定时清理时，缓存可能写满系统盘或触发 `HddSpillMonitor`（80% 线）。

### 1.2 实盘测量与代码事实

| # | 事实 | 来源 |
|---|---|---|
| F1 | 实测 `/mnt/stp-aee/firmware/` 体积：MTK V5 3.9–4.3 GB；MTK V6 6.5–8.9 GB（`P535AA-HONOR-NX2-16-261117V108` 全目录 6.91 GB，scatter `is_download=true` 为 5.51 GB，另含 `download_agent/` 约 1.3 MB）；展锐 BSL 5.2–17.5 GB（含稀疏空洞）；展锐 Fastboot `A57` 9.8–24 GB（同目录含 `.pac` 10.5 GB 与 `images/raw/super.img` 12.75 GiB）。 | 2026-11-20 本地只读实测 |
| F2 | MTK V6 `download_agent/flash.xml` 与 scatter XML 均使用相对路径，整套必需文件复制到 Agent host 本地目录后 `SPFlashToolV6` 与 V5 `flash_tool` 可原生直读，无需改写 XML。 | #4076、2026-11-20 复核 |
| F3 | `backend/agent/tool_cache.py` 已建立 Agent 侧内容寻址本地缓存范式（`STP_TOOLS_CACHE_ROOT` 默认 `{AGENT_INSTALL_DIR}/tools_cache`、`.stp-verified` 标记、临时目录原子 `os.replace`）。 | `backend/agent/tool_cache.py` |
| F4 | `backend/agent/system_monitor.py` 的 `get_disk_usage` 已提供非保留可写口径 `avail_gb` 与 `writable_percent`（#4057），`backend/agent/aee/paths.py` 已提供 `_mount_fstype_for_path` 文件系统类型识别。 | `system_monitor.py`、`aee/paths.py` |
| F5 | `backend/services/admission_pump.py` 的 `plan_admission_task` SAQ 超时为 600s；大体积固件多机传输（30 机 × 7 GB @ 1 Gbps > 30 min）与 `PRECHECK` 生命周期不兼容。 | `admission_pump.py` |
| F6 | `backend/api/routes/hosts.py` 已有 `POST /api/v1/hosts/{host_id}/flash-prereqs/ensure` 与 `/status` 按需归位入口；主机静态刷机依赖属于维护面，不属于高频心跳面。 | `backend/api/routes/hosts.py`、`backend/services/flash_prereqs.py` |

## 2. 决策

### D1 发布期不可变固件摘要契约（`staging_manifest.json`）

1. 上架工具在将标准固件版本发布到控制面根 `{STP_CONTROL_PLANE_STORAGE_ROOT}/firmware/{family}/{version}/` 时，必须同步校验并生成 `staging_manifest.json`。
2. `staging_manifest.json` 必须包含 `schema_version`（当前为 `1`）、`content_digest`（`sha256:<hex>`，按 `backend/agent/contracts/artifact_digest.py` 同口径基于规范化文件清单计算）、`total_bytes` 与精确的 `files` 列表（每项含相对路径 `relpath`、`size`、`sha256`）。
3. `files` 列表必须只收录该固件格式刷写所需的最小文件集合，并按格式确定性生成：
   - MTK V6：包含 `manifest.json`、`download_agent/` 目录内全部必要文件、scatter XML、所有 `is_download=true` 镜像以及同目录签名或校验文件（如 `.sig`、`checksum.ini`、`*.auth`），跳过调试符号（如 `APDB*`、`MDDB*`、`DbgInfo*`、`*.elf`）与未启用下载镜像；
   - MTK V5：包含 `manifest.json`、`da_file`、`scatter_file`、所有 `is_download: true` 镜像以及同目录签名或校验文件；
   - 展锐 Fastboot：包含 `manifest.json`、`images/plan.json` 及 `plan.json` 实际引用的分区镜像，跳过源 `.pac` 归档与冗余副本；
   - 展锐 BSL：包含 `manifest.json`、`images/plan.json`、FDL1/FDL2、userdata 与可写分区镜像；上架工具必须在发布期一次性完成 `super.img` 尾部零块裁剪并写入 `files` 列表，Agent host 不得在每台主机重复执行全量裁剪。
4. 已发布的固件版本目录必须视为不可变。固件内容变更时，发布者必须发布新版本目录；应急原地替换时，发布者必须同步重新生成并原子替换 `staging_manifest.json`，不得只替换镜像文件而保留旧摘要。
5. `staging_manifest.json` 中的每个 `relpath` 必须为相对路径且不得含 `..` 或指向目录外软链；Agent 校验到任一越界路径时必须整包拒绝。
6. 对于未生成 `staging_manifest.json` 的临时救砖 `firmware_dir`，Agent 可以进入未封包兼容模式：仅读取目录内文件元数据 `(relpath, size, mtime_ns)` 计算轻量指纹并整目录缓存，且必须在步骤 `metrics` 中显式标记 `firmware_cache.sealed = false`。标准仓库固件不得长期依赖该兼容模式。

### D2 Agent L1 统一固件缓存基座（`FirmwareCacheManager`）

1. 固件本地缓存的目录布局、容量守卫、并发锁、LRU 淘汰、定时清理与原子落盘必须归属 Agent L1 基础设施层（`backend/agent/firmware_cache.py`，对齐 #3498），各刷机脚本不得自建或复制缓存管理逻辑。
2. 缓存存储分层必须按本地块设备（`st_dev` 与挂载类型）抽象，不得假设主机一定同时具备 SSD 与 HDD：
   - **一级本地缓存根（Primary）**：由 `STP_FIRMWARE_CACHE_DIR` 指定，未设置时默认为 `{AGENT_INSTALL_DIR}/firmware_cache`（与 `tools_cache` 并列）；纯 SSD、纯 HDD 或单盘主机均直接落在一级本地缓存根；
   - **二级本地溢出根（Secondary）**：由 `STP_FIRMWARE_CACHE_FALLBACK_DIR` 指定，未设置时派生自 `STP_AEE_LOCAL_ROOT` 所在盘的同级目录（如 `/mnt/hdd/stp_firmware_cache`）。Agent 必须校验二级根与一级根的 `st_dev` 不同且可写；单盘或 `is_ssd_fallback_root` 主机上两者 `st_dev` 相同，Agent 必须自动跳过二级根；
   - Agent 必须校验所有候选缓存根所在挂载点的文件系统类型，不得将缓存写入 `nfs`、`nfs4`、`cifs`、`smb3` 或 `tmpfs`。
3. 所有缓存配置旋钮（`STP_FIRMWARE_CACHE_DIR`、`STP_FIRMWARE_CACHE_FALLBACK_DIR`、`STP_FIRMWARE_CACHE_RESERVE_GB` 默认 `20`、`STP_FIRMWARE_CACHE_MAX_GB` 默认 `60`、`STP_FIRMWARE_CACHE_MAX_PCT` 默认 `30`、`STP_FIRMWARE_CACHE_TTL_DAYS` 默认 `14`、`STP_FIRMWARE_CACHE_CLEANUP_INTERVAL_SECONDS` 默认 `21600`）必须收敛到 `backend/agent/settings.py`（ADR-0042），并随 `reload_config` 刷新。
4. 缓存落盘前，Agent 必须按非保留可写口径（`avail_gb` 与 `writable_percent`，对齐 `system_monitor.py` #4057）执行硬空间守卫：
   - 落盘后目标盘必须满足 `avail_after >= reserve_gb` 且该盘固件缓存总量 `<= min(max_gb, disk_total * max_pct / 100)`；
   - 若目标缓存盘与 `STP_AEE_LOCAL_ROOT` 处于同一块设备（`st_dev` 相同，含纯 HDD 单盘主机或二级溢出到日志盘的机器），落盘后该盘还必须满足 `writable_percent_after <= spill_threshold - 10%`（默认 `≤ 70%`），不得挤占日志盘触发 `HddSpillMonitor`（80%）。
5. 缓存命中与写入必须满足以下并发与崩溃安全不变量：
   - 每个缓存条目（键为 `<safe_label>_<digest16>`）必须配备独立锁文件 `.entry.lock`（以 `O_NOFOLLOW` 打开）；写入方持 `LOCK_EX`，刷机消费方在刷机全程持 `LOCK_SH`，淘汰与定时清理方删除前必须非阻塞尝试 `LOCK_EX | LOCK_NB`，获取失败时必须跳过该条目，不得删除正在被刷机使用的固件；
   - 命中已封包固件时，Agent 必须只校验本地 `.stp-fw-ready.json` 中的 `content_digest` 与本地文件完整性，不得为命中校验重读中心存储上的大镜像文件；命中后必须通过 `os.utime` 更新条目最近使用时间戳，不得依赖操作系统 `atime`；
   - 拷贝必须先写入 `<cache_root>/.staging/<key>.tmp.<pid>/`，并在单遍流式拷贝过程中同步计算 `sha256`（不得二次重读源盘）；本地磁盘写入建议在 Linux 上使用低 I/O 优先级（`ionice -c 3` 或等价 `ioprio_set`）；全部文件校验通过后，Agent 必须先落 `.stp-fw-ready.json` 再原子 `os.replace` 到最终目录。
6. 当一级与二级本地盘在淘汰全部未加锁旧条目后仍不满足第 4 条空间守卫时，Agent 默认必须以 `insufficient_local_cache_space` 显式失败（fail-closed），不得静默回落直读 NFS。只有当步骤参数显式设置 `allow_nfs_direct_fallback=true` 时，刷机脚本才可以降级直读中心存储，并在 `metrics` 中记录 `firmware_cache.fallback_reason`。
7. Agent 必须运行低频后台定时清理线程（默认每 6 小时一次，与写入时 LRU 淘汰共用 `.entry.lock` 规则）：
   - 删除超过 `STP_FIRMWARE_CACHE_TTL_DAYS`（默认 14 天）未被使用的未加锁缓存条目；
   - 清扫无锁持有的崩溃残留 `.staging/*.tmp.*` 目录；
   - 当任一缓存盘当前可用空间低于 `reserve_gb` 或与日志同盘时 `writable_percent` 超过 `spill_threshold - 10%` 时，主动按 `last_used_at` 从旧到新腾退未加锁缓存条目，直至回到安全水位或无可腾退条目。

### D3 控制面全局传输并发闸门（Redis 瞬时许可）

1. 控制面必须提供按源存储池限流的全局传输并发许可接口（`acquire` / `renew` / `release`，默认池 `firmware_nfs`，默认并发上限 `STP_FIRMWARE_TRANSFER_MAX_HOSTS=2`，单次许可 TTL 默认 `60s`）。
2. 传输并发许可属于瞬时跨进程协调态，控制面必须将其承载在 Redis 中，不得为此新增 PG 业务表（遵守 `AGENTS.md` Redis 边界）。
3. Agent 必须在发生缓存未命中（cache miss）且即将跨 NFS 拷贝前申请本机级传输许可，并在拷贝期间按 TTL/3 周期续期、拷贝结束或异常退出时立即释放。缓存命中时，Agent 不得申请传输许可。
4. 同主机多台设备并发请求同一固件时，Agent 必须通过 `.entry.lock` 在本机去重，每台主机同一时刻只申请一张传输许可，不得按设备数放大并发。等待许可或等待同机首任拷贝期间，脚本必须周期打印 `PROGRESS` 戳（`stage="firmware-stage-wait"`），避免被步骤停滞钟误杀。
5. 当控制面许可接口短暂不可达时，Agent 可以按带抖动的退避重试并在超过有界上限后记录 `permit_degraded=true` 降级继续或按策略失败，不得因单次网络抖动永久死等。

### D4 全刷机族统一接入与历史债务收口

1. 现有三个刷机脚本族——`flash_firmware`（MTK `adb_reboot` 与 `vbus_cycle`，覆盖 V5 与 V6）、`flash_firmware_unisoc`（展锐 Fastboot）与 `flash_firmware_unisoc_bsl`（展锐 BSL）——必须全部接入 D2 的 `FirmwareCacheManager`。
2. 所有刷机脚本必须在获取主机刷机排他锁 `/tmp/stp-flash-firmware.lock` **之前**完成固件缓存准备并持有对应条目的 `LOCK_SH`，在刷机进程退出时随文件描述符关闭由内核释放 `LOCK_SH`。缓存拉取耗时不得计入刷机工具自身的烧录超时（如 MTK `timeout_seconds=1200`、BSL `spd_dump_timeout_seconds`）。
3. `flash_firmware` 的 `vbus_cycle` 路径接入本地缓存时，实施者不得改变 ADR-0063 D1–D7 定义的任何切电、门控、`-p` 端口绑定、回显核对、锁内关机或失败断电隔离不变量。
4. `flash_firmware_unisoc_bsl` 必须删除脚本私有 `/var/tmp/stp_flash` 缓存拷贝代码及锁内拷贝逻辑，统一改用 D2 缓存基座；原 `/var/tmp/stp_flash` 遗留目录由运维清理或在 Agent 升级后择机移除。
5. 实施者不得为接入固件缓存而在 `backend/agent/pipeline_engine.py` 中新增按 `script_name` 字面量的分支特判（遵守 #3779）。

### D5 心跳非阻塞隔离与载荷克制（闭环 #4054）

1. Agent 心跳线程（`backend/agent/heartbeat_thread.py`）不得在心跳主循环中同步执行任何可能阻塞于 NFS 内核 `D` 状态的 `stat` / `lstat` / `os.path.exists` / `os.path.ismount` 调用。
2. Agent 必须将挂载在位判定改为读取本地内存文件 `/proc/self/mountinfo`（非阻塞），并将中心存储的可达性/可读写探针迁入独立后台探测线程（复用 `KernelUsbWatch` 的非阻塞快照模式，携带探测时间戳与 `probe_stale` 标志）。当后台探针因 NFS 拥塞阻塞时，心跳线程必须立即上报缓存状态与 `probe_stale=true` 并继续完成 HTTP 心跳与租约续期，不得阻塞。
3. 本 ADR 维持中心存储 `hard` mount 语义不变，不改 `soft`（避免未经验证的 `EIO` 传播到事件上送链），并在 `docs/design/2026-storage-roles-and-aliases.md` §4.4 登记本次复议闭环。
4. Agent 不得在常规心跳载荷中新增固件缓存版本明细或 vbus 主机静态环境检查字段（避免高频心跳膨胀）。vbus 主机静态依赖（`uhubctl`、`stdbuf`、wrapper `usb-power` 能力、`usb-power-slots.json` 校验）的归位与检查必须归属主机维护面（`flash-prereqs/ensure` 与按需查询接口），不进心跳。

### D6 预留边界与明确不做（本 ADR 第一期）

1. 本 ADR 第一期不新增「控制面主机级事前后台预置 UI 与编排任务」（原讨论 Slice 3），也不将大体积固件拉取放入 `PRECHECK` / `plan_admission_task`。D2 的 `FirmwareCacheManager` 与 D3 的传输许可接口必须保持独立可调用形态，为未来 50–150 台规模需要后台预热时预留无返工扩展口。
2. 本 ADR 不包含「vbus 刷机全 Web UI 化」（槽位入库 #3961、失败断电后供电态恢复 #4063 等）；控制面只预留统一的固件目标解析抽象，供后续 UI 专题消费。
3. 本 ADR 不包含 `#4075` 中 MTK 非 `vbus` 无复位能力跳过第 2 轮无效重试及错误分类改进，也不包含 `#3976` 的 `recycler pending_timeout` 修复；两者按独立常规单并行推进。

## 3. 备选方案与权衡

- **在各刷机脚本中复制或抽共享 helper 自行管理缓存**：否决。主机盘配额、跨盘溢出、Pin 安全 LRU、定时 TTL 清理、跨脚本共享锁与传输限流均属于宿主资源管理（#3498 L1 职责），放在脚本层会导致三族行为漂移且无法做后台定时清理。
- **把大体积固件预置放进 PlanRun `PRECHECK` / `plan_admission_task`**：否决。SAQ 准入任务超时 600s 且带 stale-PRECHECK reaper，30+ 台主机经千兆口传输需 30 分钟以上，必然击穿准入机制并阻断先落盘主机的流水线重叠。
- **第一期同步建设主机级后台预置任务与前端预热面板**：推迟（保留扩展口）。当前常见 15–20 台规模下，全局 `K=2` 限流 + 本地缓存已消除 90% 以上痛点（错峰总偏移约 18 分钟，远小于单机 60–90 分钟串行刷机时长）；等规模扩至 50+ 台且有明确换版提速诉求时再开启，避免超前建设。
- **空间不足时默认静默回落直读 NFS**：否决。静默回落会掩盖小盘/满盘故障并在大批量刷机中重新引入千兆打满与 BROM/DA 卡死风险；改为异盘本地 HDD 自动溢出 + 两盘均不足默认 fail-closed + 显式逃生阀。
- **把 vbus 静态就绪与缓存明细塞进 Agent 心跳**：否决（Owner 明确裁定）。高频心跳只保活性与基础容量，静态主机依赖归 `flash-prereqs` 维护面，缓存详情按需查询。
- **把 NFS 改为 `soft` mount 解决心跳阻塞**：否决。`soft` 会把断线以 `EIO` 暴露给事件上送与归档链，现有代码未对 `EIO` 做全链验证；心跳侧非阻塞隔离即可精准止血且零副作用。

## 4. 影响

- **Agent 侧**：新增 `backend/agent/firmware_cache.py`、后台定时 TTL 清理线程、`FirmwareCacheSettings`（`settings.py`）与非阻塞 `MountHealthProbe`；会改变 `agent-code` digest，需按正常 Agent 发布滚动。
- **控制面侧**：新增 Redis 支撑的 `transfer-permits` API（无 PG 数据库迁移）；`flash_prereqs` 后续可平滑扩展 vbus 静态项。
- **刷机脚本侧**：`flash_firmware`、`flash_firmware_unisoc`、`flash_firmware_unisoc_bsl` 各发新版本（ADR-0051：不原地改已发布版本），`flash_firmware_unisoc_bsl` 移除 `/var/tmp/stp_flash` 私有实现。
- **固件上架流程**：新增/扩展统一封包校验工具生成 `staging_manifest.json`（协同 #4042）；未封包旧目录可经兼容模式过渡，不阻断开发期使用。

## 5. 验收

1. **心跳非阻塞隔离（单测）**：注入挂起/超时的 NFS 探测桩，验证 `HeartbeatThread._tick()` 在有界时间内完成并照常发送 HTTP 心跳，同时 `mount_status` 如实标出 `probe_stale=true`；正常本地 `/proc/self/mountinfo` 判定准确。
2. **上架摘要工具（单测）**：覆盖 MTK V5、MTK V6（确认包含 `download_agent/*` 与 `.sig`、排除 `APDB`/`*.elf`/未启用镜像）、展锐 Fastboot（排除 `.pac`）与展锐 BSL（含发布期裁剪 `super.img`）；校验确定性 `content_digest`、拒绝越界相对路径。
3. **L1 `FirmwareCacheManager`（单测）**：
   - 拓扑兼容：SSD+HDD 双盘、纯 HDD 单盘、小系统盘（`.33` 模拟）溢出异盘 HDD、`is_ssd_fallback_root`（同 `st_dev` 跳过二级）、拒绝 NFS/tmpfs 路径；
   - 水位与配额：验证 `avail_gb` 与 `writable_percent`（含同日志盘 `≤ 70%` 约束）；两盘均不足时默认 fail-closed，显式 `allow_nfs_direct_fallback=true` 才返回降级；
   - 并发与崩溃安全：同机并发请求只拷贝一次；`LOCK_SH` pin 住的条目不被写入 LRU 或定时 TTL 清理误删；崩溃残留 `.staging/*.tmp.*` 被自动回收；已封包命中**零 NFS 数据文件读取**（反例断言源镜像未被 `open()`）；
   - 定时清理：超过 `TTL` 的未加锁条目被回收；盘余量跌破安全线时主动腾退。
4. **控制面全局传输许可（单测）**：并发上限 `K=2` 时第 3 台拿到等待响应而非并发放行；TTL 过期后槽位自动回收；`renew` 续期生效；`release` 幂等释放；无 PG 表新增。
5. **三刷机族接入（单测 + 隔离集成）**：
   - MTK `adb_reboot`（V5/V6）、MTK `vbus_cycle`（V5/V6，确认 ADR-0063 全部安全测试零回归）、Unisoc Fastboot、Unisoc BSL 均在主机刷机锁外完成缓存准备并持 `LOCK_SH` 指向本地目录；
   - `pipeline_engine.py` 零新增 `script_name` 字面量分支（#3779 守卫通过）；
   - `flash_firmware_unisoc_bsl` 无 `/var/tmp/stp_flash` 写入与无锁内拷贝。

## 6. 实施单元（按 ADR-0058 批次）

| 单元 | 内容 | 复核 |
|---|---|---|
| U0 | 本 ADR 文档 PR + 独立复核 → Owner 转 Accepted | Owner + 独立复核 |
| U1 | 心跳 NFS 非阻塞隔离（D5，#4054）+ 固件上架摘要工具（D1，协同 #4042） | 否（单测充分；不涉及不可逆硬件动作） |
| U2 | Agent L1 `FirmwareCacheManager` + 定时 TTL 清理 + `FirmwareCacheSettings`（D2） | 必须复核（涉及磁盘删除、并发锁与空间守卫） |
| U3 | 控制面 Redis 全局传输并发许可 API + Agent 许可客户端集成（D3） | 否 |
| U4 | 三大刷机族新版本接入（`flash_firmware`、`flash_firmware_unisoc`、`flash_firmware_unisoc_bsl` 退役私有缓存，D4） | 必须复核（触碰 MTK/vbus 与展锐刷机主链） |

U1 与 U3 可并行实施；U2 依赖 D1 清单格式；U4 依赖 U2 与 U3 接口定型。

## 7. Revisit

1. **50–150 台主机规模下，波次错峰等待（>50 min）成为换版瓶颈**：在 D2/D3 预留口上开启异步主机级后台预置（`POST /api/v1/firmware/prestage` + Agent `FirmwareStagingWorker`），不改动 D1/D4 契约。
2. **千兆单源出口物理吞吐成为瓶颈（如多站点 ADR-0041 或 150 host 常规全网换版）**：在 `FirmwareCacheManager` 内替换或扩展传输适配器（HTTP Range / P2P / 机架代理），上层刷机脚本零改动。
3. **vbus 全 Web UI 闭环立项（#3961 / #4063）**：在主机维护面 `flash-prereqs` 扩展 vbus 静态体检与一键归位，复用控制面固件目标解析抽象，仍不向高频心跳堆砌静态字段。

## 8. Owner 裁决记录（2026-11-20，#4076）

1. **归属层**：接受固件缓存收归 Agent L1（`FirmwareCacheManager`），不在脚本内复制 helper。
2. **源头摘要契约**：接受上架期生成 `staging_manifest.json`（含精简文件集与展锐 BSL 预裁剪），命中零 NFS 读取。
3. **存储分层与纯 HDD 兼容**：抽象为「一级本地盘 → 异盘本地溢出 → 默认 fail-closed（显式逃生阀才直读 NFS）」，原生兼容纯 HDD 主机与小 SSD 尾机。
4. **定时清理**：必须启用「写入时即时 LRU + 后台定时 TTL 清理（默认 14 天）+ 水位保护性腾退」双机制，均受 `LOCK_SH/LOCK_EX` 保护零误删。
5. **心跳克制**：仅修复 #4054 心跳同步探测 NFS 阻塞问题，不向心跳载荷新增固件缓存明细或 vbus 静态检查字段；vbus 主机静态前置归主机维护面（`flash-prereqs`）。
6. **分期裁剪与并发单边界**：砍掉超前的事前预置 UI/编排（仅保留后端可扩展口），先闭环 Slice 1–2；`#4075` 非缓存部分与 `#3976` 独立并行推进；vbus 全 Web UI 化归 `#3995` 后续专题。
