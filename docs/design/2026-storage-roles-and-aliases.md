# 存储与部署角色：统一语义（别称对照）

- **状态**：Living（2026-08-09）
- **目的**：角色、别称、env、IP 一一对应；冲突时以本文 + 代码为准
- **关联**：ADR-0025、[方案 C 存储](./2026-plan-c-storage-and-access.md)、[#205](https://github.com/DUElost/stability-test-platform/issues/205)（健康页双目标，切盘前冻结）

正文只用**推荐名**。口头/旧文档里的别称见各表「合法别称」。

---

## 1. 九个角色

| # | 推荐名 | 一句话 |
|---|--------|--------|
| 1 | **设备** | Android 被测机 |
| 2 | **Agent host** | 跑 Agent 的 Linux 机（约 20 台） |
| 3 | **控制面** | FastAPI / Dashboard / SAQ，生产 **永远** `192.0.2.202` |
| 4 | **中心存储（CIFS / NFS）** | 日志分享盘：`devices/` `dedup/` `jira/` `jobs/` `mtbf/` |
| 5 | **PG** | 业务库（元数据，不是日志文件） |
| 6 | **Redis** | 仅 SAQ broker（+ 可选 SocketIO adapter） |
| 7 | **扫描工具** | `start_log_scan.py`（Agent scan + 控制面 merge） |
| 8 | **JIRA** | 下游消费者；STP 只往中心存储 `jira/` 放路径 |
| 9 | **监控** | Prometheus + 控制面健康页等 |

**CIFS = NFS = 中心存储（角色 4）**。不是控制面，不是健康页，不是两台机器。口头、文档、键名里的 NFS/CIFS 都指这一台分享。

**文件服务器 / File Server** = 控制面 admin **共享存储健康页**（`/storage`），不是一台机器、不是角色 4。

带 NFS/CIFS 的**存储类 env**都应指向角色 4。主键 **`STP_AEE_NFS_ROOT`**；`STP_WATCHER_NFS_BASE_DIR` / `STP_AEE_CIFS_ROOT` 为弃用别名。控制面脚本根必须设 **`STP_SCRIPT_ROOT`**，不得再用 `STP_NFS_ROOT/scripts`。

---

## 2. 当前部署 vs 目标

```text
当前（过渡，流程调通）
  控制面 8.202  ──同机──  中心存储（CIFS/NFS）//192.0.2.202/jxtinno/sonic_tinno
  Agent ×20     ──mount──┘

目标
  控制面 8.202          （不迁）
  中心存储（CIFS/NFS）  //198.51.100.4/... 或 //192.0.2.4/...
  Agent ×20 + 控制面    ──mount 新 UNC──┘
```

| IP / 外号 | 现在 | 切盘后 |
|-----------|------|--------|
| **8.202** | 控制面；CIFS **碰巧同机** | **只**控制面 |
| **15.4** | ADR / 方案 C 里中心存储的**角色外号 / 目标盘**（上一代已用过），**不是** STP 此刻挂载 | 候选 CIFS 机 |
| **9.4** | 预计独立日志盘 | 候选 CIFS 机 |
| Agent `9.x` | Agent host | 仍是 Agent；**不要**和「CIFS 迁 9.4」混称 |

ADR-0025 / 方案 C 正文里大量「15.4」= **中心存储这个角色**，不是「生产 UNC 主机现在就是 15.4」。

---

## 3. 中心存储的两种写法（不是两个角色）

角色 4 只有一台分享 / 一块逻辑盘，两种坐标：

| 推荐名 | 类型 | 例子（当前过渡） | 谁用 |
|--------|------|------------------|------|
| **分享身份（UNC）** | 网络身份 | `//192.0.2.202/jxtinno/sonic_tinno` | fstab / `mount`；程序**不读** |
| **共享根（挂载点）** | 本机路径 | `/mnt/nfs/aee_events`、`/home/android/aee-nfs` | `STP_AEE_NFS_ROOT` |

20 台 Agent + 控制面的挂载路径字符串可以不同，必须指向**同一个 UNC**。  
切盘 = 改 UNC 主机 + remount；挂载点路径能不改就不改。

---

## 4. 别称对照（按角色）

### 4.1 设备

| 推荐名 | 合法别称 | 不要当成 |
|--------|----------|----------|
| 设备 | Android、被测机、`device`、`serial` / `device_serial`、`device_id` | `host_id`、Agent IP |
| SoC | `MTK` / `UNISOC` / `QCOM` / `UNKNOWN`（#73） | Agent host 机型 |

`/data/aee_exp`、`db_history` 是**设备本地**路径，不是 Agent HDD，也不是 CIFS。

### 4.2 Agent host

| 推荐名 | 合法别称 | 不要当成 |
|--------|----------|----------|
| Agent host | Agent、执行平面、Linux host、`host`、`host_id`、`android@<ip>`、`/opt/stability-test-agent` | 控制面、`debian13` |
| Agent SSD | `logs/runs/{job_id}/`、运行日志唯一副本 | CIFS |
| Agent HDD | L1、`STP_AEE_LOCAL_ROOT`、`aee-local`、`/mnt/hdd/aee_events` | `STP_AEE_NFS_ROOT`（那是 CIFS 挂载点） |
| LocalDB | `agent_state.db` | PG |

### 4.3 控制面

| 推荐名 | 合法别称 | 不要当成 |
|--------|----------|----------|
| 控制面 | 控制平面、backend、FastAPI、**8.202**（不迁） | CIFS（仅过渡期同机） |
| Dashboard | React、前端 | 整台控制面机 / CIFS |
| `.env.backend` | 生产唯一 env 源 | `backend/.env`（本地开发覆盖） |
| `logs/console` | `STP_RUN_CONSOLE_LOG_ROOT`、RunConsole | `/tmp`、CIFS |
| `merge_result/` | 控制面本机 merge 缓存 | CIFS `jira/` 最终件 |

### 4.4 中心存储（CIFS / NFS）

| 推荐名 | 合法别称 | 不要当成 |
|--------|----------|----------|
| 中心存储 | **CIFS**、**NFS**、中心日志服务器、L2、归档盘、sonic_tinno、`jxtinno` | 控制面、健康页 |
| 15.4 / 15.4 CIFS | 角色外号 / ADR 目标态 | 「现网 UNC 已在 15.4」 |
| `STP_AEE_NFS_ROOT` | 共享根主键（键名带 NFS，指向的就是中心存储挂载点） | `STP_FILE_SERVER_ADDRESS` |
| `STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR` | 弃用别名 → `STP_AEE_NFS_ROOT` | 第二块盘 |
| `STP_NFS_ROOT` | Agent 上 hot-update 镜像 `STP_AEE_NFS_ROOT`（旧脚本 env）。控制面本机值不下发；脚本扫描用 `STP_SCRIPT_ROOT` | 独立脚本盘 |
| `STP_AGENT_NFS_ROOT` | **已停用**（不再映射） | 第三块盘 |

子目录（角色 4 的内容，不是角色）：`devices/`、`dedup/`、`jira/{plan_run_id}/`、`jobs/{job_id}/`、
`mtbf/{project}/`（MTBF 清单/全局参数 + `results/{run_dir}.json`；控制面写配置、Agent 写 `results/`，见
[P0 设计 §4.4](../design/2026-08-mtbf-p0-runner-design.md)）。

### 4.5 PG / Redis

| 推荐名 | 合法别称 | 不要当成 |
|--------|----------|----------|
| PG | `DATABASE_URL`、生产库 **`stp`** | 日志文件；Compose **`stp_dev`** |
| Redis | `REDIS_URL`、SAQ broker | 业务数据、日志 |

### 4.6 扫描工具

| 推荐名 | 合法别称 | 说明 |
|--------|----------|------|
| 扫描工具 | `start_log_scan.py`、Start-Log-Scan、AEE_TNE、`-m 0` | Agent 上 **scan**；控制面上 **merge** |
| `STP_DEDUP_SCAN_PYTHON` / `_SCRIPT` | 同名、**两角色两套值** | 控制面读自己的；Agent 也读无前缀名 |
| `STP_AGENT_DEDUP_SCAN_*` | **仅控制面** | hot-update 写成 Agent 无前缀键 |
| `dedup/` | 中心存储上的目录 | ≠ 扫描工具 ≠ SAQ 整条链 |

推荐：工具叫**扫描工具**，目录叫 **`dedup/`**，SAQ 叫 **归档链**（scan→upload→merge→extract）。

### 4.7 JIRA

| 推荐名 | 合法别称 | 不要当成 |
|--------|----------|----------|
| JIRA | 提单、归档-3、extract、`jira/{plan_run_id}/` | 整块 CIFS；JIRA 服务进程 |

### 4.8 监控 / 共享存储健康页

| 推荐名 | 合法别称 | 说明 |
|--------|----------|------|
| 共享存储健康页 | 文件服务器、File Server、`/storage`、`FileServerPage`、`GET /api/v1/stats/file-server` | 控制面 admin UI，**盯** CIFS + 控制面负载 |
| `STP_FILE_SERVER_ADDRESS` | 健康页上**控制面**展示 IP | **不是** UNC，不是挂载点，不是 CIFS |
| Prometheus | `/metrics`、`STP_PROMETHEUS_URL`、`job=file-server` | 现刮 8.202 |

切盘后健康页应变为**双目标**（保留 8.202 + 增加 CIFS 机），见 [#205](https://github.com/DUElost/stability-test-platform/issues/205)。**当前冻结**，CIFS 仍在 8.202 时不要做。

---

## 5. 错绑速查

| 别称 | 常被当成 | 实际 |
|------|----------|------|
| 8.202 | CIFS 或控制面（含糊） | **控制面**；过渡期 CIFS 同机 |
| 15.4 | 现网 CIFS | 中心存储**外号/目标** |
| 9.4 | Agent 网段 | CIFS **候选机** |
| NFS / CIFS | 两种协议、两台机器 | **都是中心存储（同一角色、同一台分享）** |
| `STP_NFS_ROOT` | 独立脚本盘 | 应 = 中心存储挂载；CP 拿它拼 `/scripts` 是误用 |
| 文件服务器 | 中心存储 | 健康页 |
| `STP_FILE_SERVER_ADDRESS` | CIFS 根 | 健康页控制面 IP |
| `STP_DEDUP_SCAN_*` | 全球唯一路径 | 控制面 merge 一套 + Agent scan 一套 |
| HDD / CIFS / 15.4 | 互相替换 | HDD = Agent 第一落点；CIFS = 中心存储 |

---

## 6. env 与角色（程序真正读写）

| 键 | 角色 | 类型 |
|----|------|------|
| `API_URL` | Agent → 控制面 | URL |
| `STP_FILE_SERVER_ADDRESS` | 健康页 → 控制面展示 IP | IP（现 8.202，切盘**不改**） |
| `STP_AEE_LOCAL_ROOT` | Agent HDD | 本机路径 |
| `STP_AEE_NFS_ROOT` | **中心存储** 挂载点（主键） | 本机路径；控制面与 Agent 指向同一 UNC |
| `STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR` | 弃用别名（主键未设时回落） | 同盘路径或留空 |
| `STP_NFS_ROOT` | Agent 脚本 env 镜像中心存储挂载；CP 本机值不下发 | 本机路径 |
| `STP_DEDUP_SCAN_PYTHON` / `_SCRIPT` | 扫描工具（本角色路径） | 控制面 = merge；Agent = scan |
| `STP_AGENT_DEDUP_SCAN_*` | 控制面持有的 **Agent 侧**扫描工具路径 | hot-update 源键 |
| `STP_AEE_SHARE_ADDRESS` / `_UNC` | **尚未落地**；#205 切盘时给健康页 B 栏 | CIFS 机 IP / UNC |

`batch_hot_update` 只推 env，**不会**改 fstab。迁 CIFS 必须 remount。

---

## 7. 共享存储健康页（切盘后，#205）

页永远挂在控制面。CIFS 迁离 8.202 后拆成：

| 栏 | 盯谁 | 要点 |
|----|------|------|
| A | 控制面 8.202 | CPU/内存/本机是否已 **客户端挂载** CIFS；`STP_FILE_SERVER_ADDRESS` 不变 |
| B | 中心存储（CIFS）9.4 或 15.4 | 容量 + **服务端** nfsd/export；须刮存储机 Prometheus |
| C | Agent 挂载合规 | 心跳 `mount_status`，挂在 B 下 |
| D | 各 host 设备日志盘（#273） | 心跳 `extra.disk_usage_aee`（`STP_AEE_LOCAL_ROOT` 所在文件系统）；`device_log_disks` 汇总 + ≥90% warning / ≥95% critical 告警，与 HddSpill 触发口径一致 |

过渡期未设 `STP_AEE_SHARE_ADDRESS` 时 A/B 可同源，并标明「CIFS 与控制面同机」。

设备日志盘与 `/hosts` 页的系统盘（`disk_usage('/')`）语义不同：前者是 Agent AEE/日志
落盘（如 `/mnt/hdd`、`/home/android/aee-local`），水位直接影响 HddSpill 与
PRUNE_LOCAL 可用余量；后者是 Linux 系统盘。两处数据分别来自心跳
`disk_usage_aee` 与系统盘字段，不混用。

---

## 8. 修订

| 日期 | 变更 |
|------|------|
| 2026-08-09 | 初版：九角色 + CIFS=中心存储 + 健康页≠CIFS + 8.202 过渡 / 15.4·9.4 目标 + #205 |
| 2026-08-09 | 口头 **NFS = 中心存储**（与 CIFS 同角色）；`STP_NFS_ROOT` 仅作历史键名，不简称「NFS」 |
| 2026-08-20 | 健康页 §7 增 D 栏：各 host 设备日志盘水位展示（#273） |
| 2026-08-09 | 锁死 **NFS = CIFS = 中心存储**（同一台分享）。`STP_NFS_ROOT` 不是第二种 NFS，是同角色键的误用（CP 脚本默认根） |
| 2026-08-09 | 落地：HDD 不再回落中心存储；挂载点只认 `STP_AEE_NFS_ROOT`（WATCHER/CIFS 别名一层）；强制 `STP_SCRIPT_ROOT`；停 `STP_AGENT_NFS_ROOT` |
| 2026-08-09 | 活文档/UI/docstring 对齐：overview §6、健康页副标题、env 注释、上送路径注释；ADR 历史正文仍靠 Living 注记 |
