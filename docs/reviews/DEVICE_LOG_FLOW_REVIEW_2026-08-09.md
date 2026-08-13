# Android 设备日志流转：总体框架与实现审查

**日期**：2026-08-09  
**版本**：v3.0（文档闭环——缺陷清单 + DoD + 落地顺序 + 架构/运维补项索引；实现见 §七~§九 阶段 1~4）  
**目的**：定清楚日志从哪里来、经过哪些存储层、最终落到哪里、各层之间怎么协作——作为后续平台扩展（高通/展锐）和存储切换（15.253→15.4/9.4）的基线文档。

> **语义**：口头 **CIFS / NFS = 中心存储**；侧栏「文件服务器」= 控制面健康页，**不是**中心存储；`STP_FILE_SERVER_ADDRESS` = 健康页上的控制面 IP，**不是** UNC。对照 [`../design/2026-storage-roles-and-aliases.md`](../design/2026-storage-roles-and-aliases.md)。

---

## 零、总体框架与设计基调

### 0.1 一句话定位

> 多 Agent 采集 + 三级存储漏斗 + 中心汇总——把散布在 20+ 台 Linux host 上的数百台 Android 设备的崩溃日志、运行日志、扫描报表，按 PlanRun 维度收敛到中心日志服务器上的**唯一可引用目录**，供 JIRA 提单、运维排查、报表聚合使用。

### 0.2 三层日志分类

| 类别 | 来源 | 触发方 | 典型产物 | 存储路径 |
|------|------|--------|----------|----------|
| **AEE 设备日志** | Android 设备 `/data/aee_exp` | Watcher 检测 `db_history` 增量 → `adb pull` | `ZZ_INTERNAL`、`__exp_main.txt`、`db.fatal.00.dbg`、`mobilelog/`、`bugreport/` | HDD → CIFS `devices/` |
| **Job 运行日志** | Agent `pipeline_engine` 子进程 stdout/stderr | PlanRun 调度 | `logs/runs/{job_id}/` 文本 | Agent SSD（唯一副本，不上送） |
| **扫描报表** | Agent `start_log_scan.py` 扫 HDD 事件目录 | 控制面 `scan_task`（PlanRun 终态触发） | `Result_*_org.xls`、merge 汇总 xls | HDD → CIFS `dedup/` → CIFS `jira/` |

**关键区分**：「设备日志」≠「运行日志」。前者是手机崩溃产生的（AEE/mobilelog/bugreport），体积大、数量多、需要长期保留；后者是脚本执行产生的（stdout/stderr），体积小、实时消费、用完即删。两者存储策略完全不同。

### 0.3 三级存储漏斗

```text
Android 设备 (MTK/高通/展锐)
  │  adb pull / inotifyd / Reconciler
  ▼
[L1] Agent HDD  ─────────── 第一落点，保留原始副本
  │  /mnt/hdd/aee_events/{folder}/{serial}/
  │  容量 ~200GB-1TB，每台 host 独立
  │
  ├── [L1b] 无 HDD? ──→ Agent SSD 降级
  │    STP_AEE_LOCAL_ROOT 探测失败/不可写 → STP_AEE_SSD_FALLBACK_ROOT
  │    （默认 {LOG_DIR}/aee_events），目录布局不变。
  │    
  │    落地设计：
  │    - get_aee_local_root() 在现有 fallback 链最后一环前插入 SSD probe：
  │      STP_AEE_LOCAL_ROOT → (挂载点可写?) → STP_AEE_SSD_FALLBACK_ROOT
  │      → /mnt/hdd/aee_events（不回落 STP_AEE_NFS_ROOT）
  │    - 探测方法：os.access(root, os.W_OK) + 非 tmpfs（/proc/mounts 检查
  │      fs_type 不含 tmpfs）；失败则记 aee_local_root_ssd_fallback
  │    - HddSpill 在 SSD 模式下自动禁用（spill 阈值仅对 HDD 有意义；
  │      SSD 满了靠 LogArchiver 的 grace pruning 控制）
  │
  ├── 磁盘 ≥95%? ──→ [L2] 中心日志服务器 CIFS（溢出路径）
  │    HddSpillMonitor copytree → prune 本地
  │
  └── PlanRun 终态触发 ──→ UploadManager 上送
       │
       ▼
[L2] 中心日志服务器 CIFS ── 集中存储 + 合并汇总
  //172.21.15.253/jxtinno/sonic_tinno/
  ├── devices/{plan_run_id}/     ← 设备事件目录（仅 upload 路径；spill 路径见 P0-1）
  ├── dedup/{plan_run_id}/       ← 各 host 扫描 xls
  ├── jira/{plan_run_id}/        ← extract 汇总产物（JIRA 提单引用此路径）
  └── jobs/{job_id}/             ← JobArtifact 文件
```

**⚠️ 语义对齐**：当前实现仍是「PlanRun 终态批量上送 + HDD≥95% spill」。[`ADR-0028`](../adr/ADR-0028-device-log-event-and-continuous-upload.md) **D2 已接受**目标态：事件进入 `LOCAL` 后立即由 `EventUploader` 上送，不等待 PlanRun 终态。

**为什么不是 L1 直接到 L2**：
- 设备日志产生是**持续**的（Watcher 实时采集），上送是**批量**的（PlanRun 终态触发一次）。中间必须有 HDD 缓冲。
- HDD 满了不能丢数据 → HddSpillMonitor 做溢出保护，把最旧的事件目录推到 CIFS。
- 即使 CIFS 断网，HDD 上的日志不丢；CIFS 恢复后下一次 PlanRun 触发上送补全。

**中心日志服务器地址可切换**：
- 当前生产（过渡，与控制面同机）：`//172.21.15.253/jxtinno/sonic_tinno`（挂载点 `STP_AEE_NFS_ROOT`；spill 同主键）
- 目标迁移：`//172.21.15.4/...` 或 `//172.21.9.4/...`（控制面 IP 为 15.253；`STP_FILE_SERVER_ADDRESS` **不改**）
- 切换方式：修改所有 Agent 与控制面的 `STP_AEE_NFS_ROOT`，通过 `batch_hot_update` 推送，**不需要改代码**（须 remount）。
- 切换注意事项：新旧服务器并行期间，upload 和 spill 可能写到不同目标；extract 阶段从旧服务器读不到新上送的数据。建议切换窗口内暂停 PlanRun 触发。

**中心日志服务器的三个特征**（与普通网络挂载点的区别）：
1. **空间大**：单盘 ≥400GB，能承载数百台设备 × 多轮 PlanRun 的事件累积
2. **集中存储**：所有 Agent host 的上送目标是同一个共享，控制面 merge/extract 也在同一个共享上操作——不需要跨 host 读数据
3. **JIRA 可引用**：`jira/{plan_run_id}/` 下的汇总产物路径直接写进 JIRA 提单，运维点开即看

> **关于 `/tmp`**：控制平面 `/tmp`（tmpfs 12GB）**不存储任何日志产物**。
> - `STP_RUN_CONSOLE_LOG_ROOT` 默认指向 `logs/console`（生产为 `/home/debian13/stability-test-platform/logs/console`，NVMe 445GB）
> - merge 临时文件列表（`merge_list_*.txt`，几 KB）用完即删（`dedup_scan.py:259-263`）
> - Agent 侧运行日志在 `logs/runs/{job_id}/`（SSD），不在 `/tmp`
> - 当前 `/tmp` 总量 ~358MB，STP 相关 <1MB
>
> 避免基于「日志在 /tmp 下会撑爆」的错误假设做存储决策。

### 0.4 平台抽象层（当前 MTK only，高通/展锐待建）

```text
                    ┌─────────────────────────────┐
                    │   platform probe            │
                    │   ro.soc.manufacturer        │
                    │   → MTK / UNISOC / QCOM     │
                    └──────────┬──────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
    ┌──────────┐        ┌──────────┐        ┌──────────┐
    │   MTK    │        │  UNISOC  │        │   QCOM   │
    │ (已实现) │        │ (Issue)  │        │ (Issue)  │
    ├──────────┤        ├──────────┤        ├──────────┤
    │ 触发:    │        │ 触发: ?  │        │ 触发: ?  │
    │ db_history│       │          │        │          │
    │ 监测目录: │        │ 监测: ?  │        │ 监测: ?  │
    │ /data/   │        │          │        │          │
    │ aee_exp  │        │          │        │          │
    │ 解析:    │        │ 解析: ?  │        │ 解析: ?  │
    │ ZZ_INTER │        │          │        │          │
    │ NAL      │        │          │        │          │
    └──────────┘        └──────────┘        └──────────┘
```

**当前门禁逻辑**（`aee/CLAUDE.md` + `job_session.py`）：
- `adb shell getprop ro.soc.manufacturer`（回退 `ro.board.platform` 前缀）→ 归一化为 `MTK` / `UNISOC` / `QCOM` / `UNKNOWN`
- `STP_WATCHER_AEE_RECONCILE_PLATFORMS` 默认 `MTK`——只有命中才启动 Reconciler；未命中记 `aee_reconciler_skipped_platform`
- `UNKNOWN` **有意放行**（adb 抖动不应让 MTK 机型漏采）

**扩展新平台的接口契约**（建议）：
1. 每个平台实现一个 `PlatformCollector` 协议：`detect(adb, serial) → bool` + `collect(adb, serial, output_dir) → list[Path]` + `parse_metadata(output_dir) → SignalExtra`
2. Reconciler 根据 platform probe 结果选择对应 Collector
3. 目录布局和上送路径**不感知平台差异**——平台差异只体现在「怎么从设备拿出来」，拿出来之后全部走统一存储漏斗

### 0.5 MTK 平台完整触发链（当前唯一实现）

```text
设备侧                                Agent 侧
───────                              ────────
AEE 崩溃发生
  │
  ▼
/data/aee_exp/db_history 新增行       Reconciler (每 60s 基线)
  │                                     │
  │    ◄── adb shell cat db_history ────┘
  │         sha256 对比上次快照
  │         变化? → 触发采集
  │                                     │
  ├── adb pull 整目录 ──────────────────► HDD: {local_root}/{folder}/{serial}/
  │    (含 ZZ_INTERNAL, __exp_main.txt,    ├── ZZ_INTERNAL 解析 → event_type/subtype/package
  │     db.fatal.*.dbg, ...)              ├── SignalEmitter → job_log_signal 表
  │                                       │
  ├── adb pull mobilelog ───────────────► HDD: .../mobilelog/
  │    (AP 侧日志, 上下文补充)            │
  │                                       │
  └── adb pull bugreport ──────────────► HDD: .../bugreport/
       (系统快照, 上下文补充)

         ┌── Reconciler 启动失败? ──→ inotifyd 兜底 ─┐
         │    (不读 ZZ_INTERNAL, extra=NULL, 仅计数)   │
         └────────────────────────────────────────────┘
```

**为什么 dblog（db_history）是触发点**：
- MTK AEE 机制在每次崩溃时向 `db_history` 追加一行（包含 exp_class、进程名、时间戳）
- `db_history` 是一个累加日志文件，不是目录监听——所以 Reconciler 用 sha256 对比而非 inotify
- 获取 `db_history` 新增行后，才知道哪个事件目录是新的，然后 `adb pull` 对应目录

**辅助日志的上下文意义**：
- `mobilelog`：AP 侧 modem/logging 缓冲区，帮助还原崩溃前的无线/协议栈状态
- `bugreport`：Android 系统级快照（dumpsys、logcat、proc），帮助还原崩溃时的系统状态
- 两者都是**时间敏感**的——必须在检测到崩溃后尽快导出，否则环形缓冲区会覆盖

### 0.6 PlanRun 文件夹：中心日志服务器上的最终形态

```text
{中心日志服务器根}/
└── jira/{plan_run_id}/               ← ★ JIRA 提单引用的路径
    ├── merge_result_*.xls             ← 所有 host 扫描报表合并（控制面 merge_task 产出）
    ├── devices/                       ← 设备事件目录（extract_task 从 devices/ 汇聚）
    │   ├── {folder_name_1}/{serial_1}/  ← 来自 Agent-1 的设备
    │   │   ├── __exp_main.txt
    │   │   ├── mobilelog/
    │   │   └── bugreport/
    │   ├── {folder_name_2}/{serial_2}/  ← 来自 Agent-2 的设备
    │   │   └── ...
    │   └── ...
    └── extract_log.txt                ← extract 过程日志

{中心日志服务器根}/
├── dedup/{plan_run_id}/               ← 各 host 原始扫描 xls（中间产物，extract 后可能 prune）
│   ├── {host_id_1}_Result_*_org.xls
│   └── {host_id_2}_Result_*_org.xls
│
├── devices/{plan_run_id}/             ← 各 host 上送的事件目录（中间产物）
│   ├── {dirname_1}/
│   └── {dirname_2}/
│
└── devices/{folder_name}/{serial}/    ← HddSpill 溢出路径（不按 plan_run_id 组织）
```

**关键设计点**：
- `jira/{plan_run_id}/` 是**最终可交付产物**——这个路径写进 JIRA 提单，运维/开发点开就能看到该轮测试的完整崩溃报告
- `dedup/` 和 `devices/` 是**中间产物**——为 merge 和 extract 服务，extract 完成后可以按保留策略清理
- **一个 PlanRun 可能跨多台 Agent host**——控制面 merge_task 负责把所有 host 的 `Result_*_org.xls` 合并成一份，extract_task 负责把所有 host 的设备事件目录汇聚到 `jira/{plan_run_id}/devices/`
- HddSpill 溢出路径**不按 PlanRun 组织**（因为溢出时还不知道属于哪个 PlanRun）——它按 `{folder_name}/{serial}` 组织，与 PlanRun 的关联靠文件名中的时间戳事后匹配

### 0.7 磁盘空间保护层级

```text
HDD 使用率
  │
  ├── < 70%  正常：所有日志写 HDD，等待 PlanRun 终态触发上送
  │
  ├── 70%-95%  警戒：无动作，但运维应关注
  │
  └── ≥ 95%  触发 HddSpillMonitor
       │
       ├── 按 mtime 找最旧事件目录 → copytree 到 CIFS
       ├── 成功后 prune 本地
       ├── 循环直至回落到 70% 或无更多可上送目录
       └── 无更多目录仍超阈 → 仅告警，不丢数据
```

**配置项**（`HddSpillMonitor` 默认值）：
| 参数 | 默认值 | 含义 |
|------|--------|------|
| `threshold_pct` | 95.0 | 触发溢出的使用率阈值 |
| `target_pct` | 70.0 | 溢出后目标回落到此值 |
| `interval` | 300s | 检查间隔 |
| `_MAX_SPILL_PER_CYCLE` | 20 | 每轮最多溢出目录数（防风暴） |

**已知缺陷（已修）**：`_current_usage_pct` 曾把缺失 `usage_percent` 当成 `0.0`「磁盘健康」跳过 spill；`get_disk_usage` 异常也曾返回 `0.0`。现失败/缺失/非法值一律 `None`，spill 跳过且 metrics 不写假 0%；capacity 同步 `disk_unknown` → UNSCHEDULABLE。

---

## 一、当前真实流转（事实源）

### 链路 1：Job 运行日志

```text
pipeline_engine 写磁盘
  └→ Agent SSD: logs/runs/{job_id}/
       ├→ [实时] SocketIO step_log → 控制面 log_writer → UI LiveConsole
       │                                                → GET /logs/query
       └→ [事后] POST /agent/logs（SSH 读 Agent 磁盘，需在 STP_SSH_LOG_ROOTS 白名单）
            ↓
       LogArchiver 按 grace prune 本地目录（不上送中心存储）
```

**事实源**：
- Agent SSD：唯一副本（方案 C）
- 控制面 `log_writer.py`：持久化到本地文件（非 Loki）
- 中心存储（CIFS）：**不含运行日志**（`run_log_bundle` JobArtifact 已取消）

---

### 链路 2：AEE / mobilelog / bugreport

```text
Watcher 路径 B（Reconciler / inotifyd）
  └→ Agent HDD: {STP_AEE_LOCAL_ROOT}/{folder_name}/{serial}/
       ├→ SignalEmitter → JobLogSignal → 控制面 DB
       │
       ├→ [按需] upload_events SocketIO 命令
       │    └→ UploadManager.upload_event_dirs
       │         → 中心存储（CIFS）: {STP_AEE_NFS_ROOT}/devices/{plan_run_id}/{dirname}/
       │
       └→ [溢出] HddSpillMonitor（HDD 满 ≥ threshold_pct）
            └→ 中心存储（CIFS）: {STP_AEE_NFS_ROOT}/devices/{folder_name}/{serial}/
```

**事实源**：
- Agent HDD：第一落点（`STP_AEE_LOCAL_ROOT`，默认 `/mnt/hdd/aee_events`）
- 中心存储（CIFS）：`devices/` 子目录（两条路径：`upload_manager` 写 `{plan_run_id}/{dirname}`；HddSpill 写 `{folder_name}/{serial}`）
- 控制面 DB：`job_log_signal.extra->>'nfs_path'`（Reconciler emit）

---

### 链路 3：扫描报表（dedup scan → upload → merge）

```text
控制面 enqueue_dedup_terminal_sync → SAQ scan_task
  ├→ emit scan_now → 各 ONLINE Agent
  │    └→ ScanRunner.run_local_scan（-m 0 -d {hdd_root}）
  │         → Agent HDD: Result_*_org.xls
  │              └→ UploadManager.upload_scan_report
  │                   → 中心存储（CIFS）: {STP_AEE_NFS_ROOT}/dedup/{plan_run_id}/{host_id}_Result_*_org.xls
  │
  ├→ [控制面] poll 中心存储 dedup/{plan_run_id}/ (10s × 30 = 300s max)
  │    └→ run_scan_sync 注册 PlanRunArtifact(scan_result_xls)
  │
  ├→ enqueue upload_task（并行 merge_task）
  │    └→ emit upload_events → Agent upload_event_dirs
  │         → 中心存储（CIFS）: devices/{plan_run_id}/{dirname}/
  │
  └→ enqueue merge_task
       ├→ run_merge_sync（控制面本地子进程 -merge_files / -merge_files_list）
       │    → 控制面本地: merge_result/ 目录
       │         └→ PlanRunArtifact(merge_result_xls) 注册 DB
       │
       ├→ poll upload:{plan_run_id} SAQ job 终态（5s × 132 ≈ 660s max）
       ├→ poll 中心存储 devices/{plan_run_id}/ 时间戳目录（10s × 30 = 300s max）
       └→ enqueue extract_task
            └→ copy devices/ + merge xls → 中心存储（CIFS）: jira/{plan_run_id}/
```

**事实源**：
- Agent HDD：`Result_*_org.xls` + 事件目录
- 中心存储（CIFS）：`dedup/{plan_run_id}/`（scan 产物）、`devices/{plan_run_id}/`（事件目录）、`jira/{plan_run_id}/`（extract 汇总）
- 控制面 DB：`plan_run_artifact` 表
- 完备性判定：`count_hosts_with_scan_artifacts`（三维度收窄：host 去重 + triggered 集合 + since 水位线）

---

## 二、Findings（缺陷清单）

### P0 数据丢失 / 错归档

#### **P0-1：HddSpill 溢出路径 extract/jira 不可达**

**证据**：`resolve_spill_devices_dest`（[`paths.py:176`](../../backend/agent/aee/paths.py)）写入 `devices/{folder_name}/{serial}/`，但 `run_extract_sync`（[`dedup_extract.py:156`](../../backend/services/dedup_extract.py)）只读 `devices/{plan_run_id}/`。HddSpill 溢出的目录在 depth≥3 的嵌套路径下，`_count_devices_event_dirs_sync` 只统计 depth-1 的 `devices/{plan_run_id}/` 子目录——溢出的数据**在控制面完全不可见**。

**为什么会在生产 20 台 host 规模下出事**：
- HDD ≥95% 触发 spill → 最旧事件目录被 copy 到 CIFS `devices/{folder}/{serial}/` → 本地 prune
- 后续 PlanRun 终态触发 extract → 只从 `devices/{plan_run_id}/` 收集 → 这些溢出的目录**永远不会出现在 jira bundle 中**
- 运维看到 `hosts_with_artifacts` 低于预期，但不知道为什么——数据已经在 CIFS 上，只是路径不对

**建议修法**：
- 短期：extract 按 merge xls Path 列同时查 `devices/{folder}/{serial}/`（双根遍历）；或 spill 写入 `devices/{plan_run_id}/`（需要 spill 时知道 plan_run_id——当前架构做不到，需 DeviceLogEvent）
- 中期（重构后）：spill 写入 DeviceLogEvent.remote_path，extract 按 event ID 查 DB 而非扫目录

#### **P0-2：HddSpill 无 job/scan 感知，可能删除正在使用的目录**

**证据**：`local_disk_monitor.py` 的 docstring 写「永不删除活跃 job 关联的事件目录」，但代码中 **job_id/active 出现次数为 0**（grep 确认）。`_spill_oldest_event_dir` 只按 mtime 排序取最旧，copytree 后即 `shutil.rmtree`——不检查是否有运行中的 Job 正在引用该目录，不检查是否有 queued 的 scan_now 即将扫描它。

**为什么会在生产 20 台 host 规模下出事**：
- Reconciler 刚 pull 了一个旧时间戳事件 → HddSpill 在几秒后把它判定为「最旧」→ copytree + rm
- 后续 scan_now 扫描时目录已消失 → xls 缺该事件 → merge 缺数据
- 或者更糟：scan_now 正在读该目录时被 rm → xls 写入失败（或部分写入）

**建议修法**：
- 短期：spill 前查目录 mtime（N 分钟内跳过）+ 检查是否存在活跃 job 或未 UPLOAD 事件标记
- 中期（重构后）：由 `DeviceLogEvent.state` 替代——只溢出 `state=LOCAL` 且非 `UPLOADING` 的事件

#### **P0-3：增量 merge 混入历史轮次 _org.xls**

**证据**：`_load_org_files_for_merge`（[`dedup_scan.py`](../../backend/services/dedup_scan.py)）加载**全部** `scan_result_xls` artifact（`WHERE plan_run_id = ...`），无 `since` 时间边界。`run_scan_sync` 也注册 `dedup/{plan_run_id}/` 下**所有** `_org.xls`（不区分轮次）。同一 `plan_run_id` 上的增量 scan 会把上一轮的 org 文件混入本轮 merge。

**当前薄弱防护**：`count_hosts_with_scan_artifacts` 用 `created_at >= since` 做完备性统计，但不限制 merge 的输入。如果上轮的 org 文件仍然在 `dedup/` 下，merge 会把它和本轮的文件一起合并——产生重复数据。

**建议修法**：
- 短期：merge 仅取 `PlanRunArtifact WHERE created_at >= round_started_at`（需 `scan_task` 传入 `round_started_at` 或新增 `scan_round_id` 字段）
- 中长期：新增 `scan_round_id` 列，artifact 带轮次，merge 按轮次过滤

---

### P1 完备性误判

#### **P1-1：HddSpillMonitor 读盘失败返回 0.0 误判磁盘健康** — **已修**

**原证据**：`get_disk_usage` except 返回 `usage_percent: 0.0`；`_current_usage_pct` 用 `.get(..., 0.0)`；`check_once` 把 `None` 记成 `_last_usage_pct=0.0`。HDD 已满但 stat 失败时 spill 被当成「健康」跳过。

**落地**（修复不止 HddSpill 消费侧——最上游 `system_monitor.get_disk_usage` 本身的行为就是契约）：
- `system_monitor.get_disk_usage`（`system_monitor.py:71-97`）：**读盘失败必须返回 `usage_percent: None`，绝不填 `0.0`**——`0.0` 会被任何消费方当成「磁盘健康」而跳过溢出。docstring 已把「None 不填 0.0」写成契约（`:74-75`），异常分支返回全 None 字典（`total_gb`/`used_gb`/`free_gb`/`usage_percent` 均 `None`，`:90-97`），并有测试断言
- `HddSpillMonitor._current_usage_pct`（`local_disk_monitor.py:233-256`）：缺失 / `None` / 非法值 → `None`；`check_once` 遇 `None` 记 WARNING 并**跳过本轮 spill**（`:141-143`）；`_last_usage_pct` 保持 `None`，metrics 暴露 `None` 不写假 0%
- `capacity_reporter`：`disk_unknown` → UNSCHEDULABLE（同失败面：读不到盘就不要继续接活）

---

#### **P1-2：ScanRunner 忽略 `STP_DEDUP_SCAN_TAG`，固定 `-side shanghai`**

**证据**：`scan_runner.py:237-238`（`configure`）固定 `self._side = "shanghai"`，不读 `STP_DEDUP_SCAN_TAG`。而控制面 `run_merge_sync`（`dedup_scan.py:226-227`）读 tag 决定 `-side factory` 或 `-side shanghai`。Agent scan 与 merge 的 side 参数可能不一致。

**建议修法**：`ScanRunner.configure` 与 `dedup_scan.run_merge_sync` 同逻辑读 `STP_DEDUP_SCAN_TAG`。

---

#### **P1-3：部分 host 未齐仍 merge，UI 不展示缺口**

**证据**：`saq_tasks.scan_task` 在 `hosts_done < n_triggered` 后仍 enqueue upload + merge（有意设计——部分报表优于零报表）。但前端 `DedupReportCard` 不展示 `hosts_with_artifacts` / `hosts_triggered` 缺口。

**建议修法**：API 暴露 `run_context.archive`；前端 DedupReportCard 展示 N/M host 完成度。

---

#### **P1-4：`find_event_dir_under_root` 同名目录只取字典序第一个**

**证据**：`event_dirs.py:80` 用 `sorted(matches)[0]`——两台不同 serial 的设备产生同名事件目录时（相同 `{timestamp}_{db_path}`），第二台被静默跳过。

**建议修法**：按 serial 路径消歧，或引入事件 ID 做唯一目录名。

---

#### **P1-5：FAILED/abort PlanRun 设备日志不上送（仅靠 spill）**

**证据**：`should_trigger_dedup` 仅对 SUCCESS/PARTIAL_SUCCESS 返回 True；abort 路径的调用是死代码（abort 永远解析为 FAILED）。FAILED 运行的事件永远留在 HDD，直到被 HddSpill 溢出（且溢出路径 extract 不可达——P0-1）。

**建议修法**：短期——abort 仍触发 `scan_now(is_final=True)` 但不 extract；长期——连续上送（A-1）解耦。

---

### P2 路径/env 角色错配

#### **P2-1：控制面与 Agent 共用 `STP_DEDUP_SCAN_*` 的角色混淆**

**证据**：
- `backend/services/dedup_scan.py:36-40`（控制面读 `STP_DEDUP_SCAN_PYTHON` / `_SCRIPT`）
- `backend/agent/scan_runner.py:237-238`（Agent 读同名 env）
- `AGENTS.md` §scan/upload/merge 跨进程契约：控制面须经 `STP_AGENT_DEDUP_SCAN_*` 映射

**为什么会在生产 20 台 host 规模下出事**：
- 部署时若控制面与 Agent 共用一个 `.env`，控制面 merge 会错误调用 Agent 路径的扫描工具
- 反之，若 Agent 继承控制面的 env，Agent scan 会错误调用控制面路径（可能无权限或不存在）
- 生产表现：此 bug 在首次 `merge_task` 就会暴露（控制面找不到 `-merge_files_list` 或路径不对），但 Agent scan 可能静默失败（只有本地 WARNING）

**建议修法（最小改动）**：
1. Agent **继续读** `STP_DEDUP_SCAN_PYTHON` / `_SCRIPT`（不改前缀——hot-update 已按 `STP_AGENT_` 前缀映射下发，Agent 侧 `STP_DEDUP_SCAN_*` = 控制面 `STP_AGENT_DEDUP_SCAN_*`，循环改会破坏现有映射）
2. 控制面 `dedup_scan.py` 改为读 `STP_BACKEND_DEDUP_SCAN_PYTHON` / `_SCRIPT`（或新增专门的本地路径 env），与 Agent 侧值不同
3. 另增：`ScanRunner.configure` 读 `STP_DEDUP_SCAN_TAG`（当前固定 `side=shanghai`——P1-2）

---

#### **P2-2a：启动窗口 `set_control_handler` 晚于 `connect`，SocketIO 命令丢弃**

**证据**：`main.py:558` 调用 `sio_client.connect()`，但 `main.py:943` 才 `set_control_handler`（注册 `scan_now`/`upload_events`/`reload_config` 等处理函数）。连接成功到 handler 注册之间到达的命令被**静默丢弃**（SocketIO 事件无订阅者）。

**为什么会在生产 20 台 host 规模下出事**：
- Agent 重启后控制面可能立即下发 `scan_now`（SAQ retry 或 cron sweep）
- 连接成功但 handler 未注册 → 命令丢失 → 该轮 scan 零产物（无 retry——`emit_agent_control` 是 fire-and-forget，见 P2-4）

**建议修法**：先 `set_control_handler` 再 `connect`（或先注册空 handler 入队，configure 后消费）。

---

#### **P2-2b：ScanRunner / UploadManager configure 晚于 SocketIO 命令到达**

**证据**：`main.py` 启动顺序：先 `sio_client.connect()`（558），后 `ScanRunner.instance().configure()`（626）、`UploadManager.instance().configure()`（627）。handler 已在 943 注册，但 `is_configured() == False` 时命令被静默跳过（`control_scan_now_skip_runner_not_configured`）。

**为什么会在生产 20 台 host 规模下出事**：同 P2-2a——Agent 重启后首个 PlanRun 的 scan 请求被静默跳过。

**额外触发条件**：`watcher` 关闭时（`STP_WATCHER_ENABLED=0`），ScanRunner/UploadManager/LogArchiver/HddSpill 的 `configure` 都在 watcher 门控块内不执行——无 watcher 的 host 也失去了 scan/upload 能力（见 P2-3）。

**建议修法**：命令入队至 configure 完成再消费；或将 ScanRunner/UploadManager 移出 watcher 门控（P2-3）。

---

#### **P2-3：watcher 关闭时 scan/upload/LogArchiver 不启**

**证据**：`main.py` 中 `ScanRunner.configure()`、`UploadManager.configure()`、`LogArchiver`、`HddSpillMonitor` 的 `configure` 都在 `if watcher_subsystem_enabled()` 块内。`STP_WATCHER_ENABLED=0` 时全部跳过。watcher 负责 AEE 采集，scan/upload 负责报表——两者无强依赖关系。

**建议修法**：将 ScanRunner/UploadManager/LogArchiver/HddSpill 的 configure 移出 watcher 门控块。

---

#### **P2-4：`emit_agent_control` 无 ack，Agent 离线时命令静默丢弃**

**证据**：`socketio_server.py:607-618`（`emit_agent_control`）用 `sio.emit`（room emit，无 ack）。Agent 离线时 room emit 静默成功（消息无接收者）。`call_agent_rpc`（同文件 365-434）有 ack 能力但**未用于 control 命令**。

**建议修法**：SAQ 任务层重试（`retries=2` 已在 scan_task）；或 Agent 连接后拉取待执行命令队列（替代 push-only）。

---

#### **P2-5：Agent 仅配 `STP_AEE_CIFS_ROOT` 时上送落本地 HDD** — **已修**

**原证据**：UploadManager / `get_aee_nfs_root()` 只认 `STP_AEE_NFS_ROOT`，未设时回落到 HDD。只配 `STP_AEE_CIFS_ROOT` 会上送到本地盘。

**落地**：`resolve_shared_storage_root()`（`backend/core/storage_root.py` + Agent `aee/paths.py` 副本）主键 `STP_AEE_NFS_ROOT`，CIFS/WATCHER 仅弃用别名；HDD 根 `get_aee_local_root()` 不再回落中心存储键。

---

#### **P2-6：UploadManager 与 HddSpill 的中心存储根不一致**

**证据**：
- `upload_manager.py:32-35` 与 `main.py:625` 都经 `resolve_shared_storage_root()` 解析：主键 `STP_AEE_NFS_ROOT`，`STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR` 只是**未设主键时的弃用别名**
- 而方案 C 文档（`docs/design/2026-plan-c-storage-and-access.md` §2.3）写的是「`STP_AEE_CIFS_ROOT` 仅 spill 可选，空则回落 NFS_ROOT」——语义是 spill 可配**独立目标**

**为什么会在生产 20 台 host 规模下出事**：
- 运维按文档同时配置 `STP_AEE_NFS_ROOT` + `STP_AEE_CIFS_ROOT` 期望 spill 分流——实际 spill 与 upload 仍写同一根，分盘意图落空；反之若只配 `STP_AEE_CIFS_ROOT`，upload 也会静默改用它（与 P2-5 同族）
- 切换窗口（§0.3）内新旧服务器并行时，spill/upload 目标不可独立控制，extract 从旧服务器读不到新上送的数据

**建议修法（最小改动）**：P2-5 已修时代码已选方向（`resolve_shared_storage_root()` 主键仅 `STP_AEE_NFS_ROOT`，CIFS/WATCHER 为弃用别名）。阶段 1 只需同步方案 C 文档 §2.3：删除「spill 可选独立目标」语义，标注 `STP_AEE_CIFS_ROOT` 为弃用别名

---

### P3 可观测性缺口

#### **P3-1：Agent scan 失败只有本地 WARNING，控制面 PlanRun 仍报 SUCCESS**

**证据**：
- `backend/agent/scan_runner.py:369-375`（scan 失败记 WARNING）
- `backend/tasks/saq_tasks.py:214-240`（scan_task 记录零产物/部分产物到 `run_context.archive`）
- `backend/services/dedup_scan.py:159-208`（`record_scan_archive_state`）

**当前状态**：已有 `saq_scan_no_artifacts`（ERROR）与 `saq_scan_partial_artifacts`（WARNING），并写 `run_context.archive`。

**剩余缺口**：
- Agent scan 子进程 stderr 不上报（`scan_runner.py:373` 截断 500 字符仅本地 WARNING）
- 控制面 PlanRun 终态仍可能是 SUCCESS（scan 失败不影响 Job 聚合结果）

**为什么会在生产 20 台 host 规模下出事**：
- 若扫描工具路径错配、依赖缺失、Python 版本不兼容 → 全部 host scan 失败 → `hosts_with_artifacts=0` → ERROR 日志但 PlanRun 不转 FAILED
- 运维只看 PlanRun 终态 SUCCESS，不知道"没有报表"

**建议修法（最小改动）**：
- `aggregator.py` 在 `should_trigger_dedup` 返回 True 且 `hosts_with_artifacts=0` 时，将 PlanRun status 改为 PARTIAL_SUCCESS 或在 `result_summary` 标记 `scan_failed: true`
- 前端 PlanRun 详情页显著展示 `run_context.archive`（当前可能被折叠在 JSON 里）

---

#### **P3-2：upload_events 完成度无反馈**

**证据**：
- `backend/agent/upload_manager.py:127-195`（`upload_event_dirs` 返回 count）
- `backend/agent/main.py` 对 `upload_events` 命令的处理：调用但不回传结果
- `backend/tasks/saq_tasks.py:311-352`（`upload_task` 仅下发命令，不等结果）

**为什么会在生产 20 台 host 规模下出事**：
- 控制面 `upload_task` 下发后立即返回，不知道 Agent 实际上送了几个目录
- `merge_task` poll `devices/{plan_run_id}/` 时若超时 → `saq_merge_devices_wait_timeout` → 仍 enqueue extract（best-effort）
- 若 Agent 上送失败（CIFS 断挂、权限错误），只有 Agent 本地 `upload_event_dirs_copy_failed`，控制面无感知

**生产影响**：
- 20 台 host 部分 CIFS 挂载失败 → 该 host 零上送 → extract 缺部分数据 → jira bundle 不完整 → 运维无法从汇总报告溯源

**建议修法（最小改动）**：
- Agent `upload_events` 处理完成后经 SocketIO 回传 `{plan_run_id, uploaded_count, failed_count}` → 控制面记入 `run_context.upload_summary`
- 前端显示上送完成度（类似 scan 的 `hosts_with_artifacts`）

---

#### **P3-3：`archived_jobs` 实为 JobLogSignal 计数，命名误导**

**证据**：`WatcherArchiveOut.archived_jobs` 实际统计 `job_log_signal` 行数（信号数），不是 job 数。字段名暗示「多少 job 有归档」，实际含义是「多少信号已采集」。重构后应改为 `signaled_jobs` 或改计算维度。

---

#### **P3-4：extract/jira bundle 无完备性校验**

**证据**：`run_extract_sync`（`dedup_extract.py:156`）不做事后校验——targets（merge xls 引用的目录）vs copied vs missing 无记录。

**建议修法**：extract 后写 `run_context.extract`：`{targets: N, copied: M, missing: [...]}`。

---

### P4 权限/磁盘满/CIFS 断挂时的静默失败

#### **P4-1：UploadManager / HddSpill copytree 失败后无重试**

**证据**：
- `backend/agent/upload_manager.py:182-189`（`upload_event_dirs` 单次 exception → 跳过该目录）
- `backend/agent/local_disk_monitor.py:203-206`（HddSpill copy 失败 → 返回 0，本地不 prune）

**为什么会在生产 20 台 host 规模下出事**：
- 瞬时 CIFS 网络抖动、NFS stale file handle → copytree 失败 → 该目录永久漏上送
- Agent 日志有 `upload_event_dirs_copy_failed`，但控制面不感知 → extract 缺数据

**生产影响**：
- 20 台 host × 每台几十个事件目录：网络抖动导致 1% 目录上送失败 → 累积遗漏可观

**建议修法（最小改动）**：
- UploadManager 维护失败清单，下次 `upload_events` 重试（或单独定时重传线程）
- HddSpill 失败目录标记（写本地 `.stp-spill-failed` 哨兵文件），周期重试

---

#### **P4-2：LogArchiver grace=0 时误删活跃 Job 日志** — **已修**

**原证据**：`archive_now` → `scan_once(grace_seconds=0)`；只靠 `_db.get_active_jobs()` 跳过活跃 Job。SQLite 漏记刚启动 Job 时会立刻 `rmtree` SSD 日志。

**落地**：`MIN_GRACE_SECONDS = 300`。`configure` / `scan_once`（含 archive_now 的 0）一律抬到下限；mtime 未满 300s 的目录不 prune。

---

#### **P4-3：`JobLogSignal` CASCADE 删 job 丢信号**

**证据**：`models/job.py:159-191`（`JobLogSignal`）`job_id` 设 `ondelete="CASCADE"`。job 被保留策略删除时，所有已采集的信号记录级联删除——丢失该 job 对应设备的全部崩溃记录。

**建议修法**：改 `ondelete` 为 `SET NULL`；重构后软关联 `device_log_event_id`。

---

#### **P4-4：log_signal outbox 租约过期后死信永久丢弃**

**证据**：`watcher/emitter.py:154-371`（`OutboxDrainer`）依赖 `DeviceLease` 行存在。租约过期后 `POST /agent/log-signals` 返回 409 `UPLOAD_FENCING_MISMATCH`，重试 10 次 → 死信永久丢弃。

**建议修法**：死信可查询（DB 表或持久化文件），提供人工/自动重放能力。

---

## 三、查过但没问题的环节

### ✅ 链路 1：运行日志不上送 15.4

- `backend/api/routes/plan_runs.py`：`/plan-runs/{id}/artifacts/run_log_bundle` 返回 409
- `backend/services/dedup_scan.py`：无 `run_log_bundle` 相关逻辑
- `backend/agent/log_archiver.py`：已移除上送代码

### ✅ 链路 2：AEE 事件目录路径无串台

- `backend/agent/aee/paths.py:137-164`（#172 统一入口）：
  - `resolve_artifact_promote_dir`：`jobs/{job_id}/`
  - `resolve_upload_devices_dir`：`devices/{plan_run_id}/`
  - `resolve_spill_devices_dest`：`devices/{folder_name}/{serial}/`
- 三者写不同子路径，无覆盖风险
- **⚠️ 注**：无串台 ≠ 可达——HddSpill 溢出的 `devices/{folder}/{serial}/` 路径与 extract 读取的 `devices/{plan_run_id}/` 不兼容，溢出数据在 jira bundle 中不可达（见 §2 P0-1、§五根因分析）

### ✅ 链路 3：完备性三维度收窄

- `backend/services/dedup_scan.py:117-156`（`count_hosts_with_scan_artifacts`）：
  - 按 host 去重（`func.count(distinct(PlanRunArtifact.host_id))`）
  - 限定 triggered 集合（`.in_(list(host_ids))`）
  - 限定 since 水位线（`PlanRunArtifact.created_at >= since`）

### ✅ scan_task 等齐逻辑正确

- `backend/tasks/saq_tasks.py:186-212`：poll 最多 300s，等不齐也 enqueue 后继（有意设计）
- 零产物 / 部分产物写 `run_context.archive`

### ❌ HddSpill 不删活跃 Job 关联目录（已被 P0-2 证伪）

- `backend/agent/local_disk_monitor.py:169-190`：按 mtime 排序，只删最旧的——**没有任何 `job_id` / `active` 检查**，docstring 声称的「永不删除活跃 job 关联的事件目录」未实现（见 §2 P0-2）

---

## 四、当前框架与理想形态的差距

以下不是代码缺陷，而是**架构层面尚未覆盖的能力**——需要在后续迭代中补齐。

### 4.1 平台扩展（#73 展锐 + 高通待建）

| 差距 | 当前状态 | 目标 |
|------|----------|------|
| 展锐 (UNISOC) 崩溃采集 | 平台探测已通（`ums9230`），但 `STP_WATCHER_AEE_RECONCILE_PLATFORMS=MTK` 不启动 Reconciler | 研究展锐崩溃日志路径（可能不同于 `/data/aee_exp`），实现 `UnisocCollector` |
| 高通 (QCOM) 崩溃采集 | 未开始 | 研究高通平台日志机制（Tombstone? ramdump?），实现 `QcomCollector` |
| 平台 Collector 协议 | 不存在——逻辑直接写在 Reconciler/JobSession 里 | 定义 `PlatformCollector` 抽象协议（detect / collect / parse_metadata），各平台实现 |

**设计原则**：平台差异只体现在「怎么从设备拿出来」，拿出来之后全部走统一存储漏斗。上送路径、PlanRun 文件夹、JIRA 引用方式不变。

### 4.2 存储切换（15.253 → 15.4/9.4）

| 差距 | 当前状态 | 目标 |
|------|----------|------|
| 地址配置 | 通过 `STP_AEE_NFS_ROOT` 环境变量，值散落在 20 台 host 的 `.env` 中（`STP_FILE_SERVER_ADDRESS` 是健康页控制面 IP，切盘不改） | 支持 `batch_hot_update` 批量推送新挂载点 |
| 切换流程 | 无文档化流程 | 文档化切换步骤：暂停 PlanRun → 推送新 env → 验证挂载 → 恢复 |
| 新旧并存（双源读） | 不支持——extract 只读单一 `STP_AEE_NFS_ROOT` | 方案 A：`STP_AEE_NFS_ROOT_LEGACY`（extract 双根遍历，切换窗口后移除）；方案 B：硬切换 + 人工 rsync 迁数据。建议方案 A（SOP 写入 O-1） |
| 切换后验证 | 无 | 冒烟用例：触发一次 PlanRun → 验证 `jira/{plan_run_id}/` 产物完整 |

### 4.3 PlanRun 文件夹与中间产物完整性

| 差距 | 当前状态 | 目标 |
|------|----------|------|
| `jira/` bundle 完整性校验 | extract_task best-effort，部分 host 上送失败仍继续 | extract 完成后校验：`hosts_triggered` vs `hosts_in_bundle`，缺口写入 `run_context` |
| `jira/` 产物留存策略 | 无自动清理 | 按 PlanRun 终态时间 + 保留期（如 30 天）自动 prune |
| 跨 host 设备目录去重 | 无——同一设备可能被多个 PlanRun 引用 | 按 `{serial}_{timestamp}` 组织，避免同名覆盖 |
| **`merge_result/` 控制面本地中间产物** | 14 个子目录，5 月至今累积 ~8MB，无清理。路径：`{STP_DEDUP_SCAN_SCRIPT 父目录}/merge_result/`。merge 子进程在控制面本地执行，产物在此目录下，注册 `merge_result_xls` artifact（`storage_uri` 指向本地路径）后即遗留。extract 阶段从此目录读取 merge xls 汇入 `jira/` bundle | ① merge 仍跑在控制面本地（依赖 scan tool 脚本，无法迁 CIFS），但 `merge_result/` 子目录在 extract 完成后按保留期清理（如 30 天）；② artifact 注册后，`jira/` bundle 里的 merge xls 是最终交付物，本地 `merge_result/` 降级为可丢弃缓存 |
| **`logs/console/` RunConsole 日志** | `STP_RUN_CONSOLE_LOG_ROOT` 默认 `logs/console`（相对路径，生产指向 `/home/debian13/stability-test-platform/logs/console`），当前 ~242MB、~20 个文件（含 ansible 安装输出）。**无自动清理，无限增长** | RunConsole 日志按 30 天自动清理（`con-{uuid}.log` 文件，按 mtime 判定） |

### 4.4 可观测性

| 差距 | 当前状态 | 目标 |
|------|----------|------|
| HDD 使用率趋势 | 无聚合——每台 Agent 独立监控 | 控制面聚合 20 台 host 的 HDD 使用率 → Grafana panel |
| CIFS 空间监控 | 无 | 中心日志服务器磁盘使用率告警 |
| 端到端延迟 | 无 | 设备崩溃 → Watcher 采集 → HDD 落盘 → 上送 → merge → extract 全链路延迟分布 |

### 4.5 运维 / 配置 / 流程

| # | 补项 | 内容 |
|----|------|------|
| O-1 | 中心存储切换 SOP | 暂停新 PlanRun → batch_hot_update 推送 `STP_AEE_NFS_ROOT`（+ CIFS）→ 各 host `reload_config` → 挂载冒烟 → 恢复；记录切换时间点；双源 extract 策略（§4.2 方案 A） |
| O-2 | 中心根一致性门禁 | hot-update 后校验：Agent `STP_AEE_NFS_ROOT` 存在且可写；与控制面同值（或文档允许的映射关系） |
| O-3 | 阈值统一 | 生产默认 `STP_LOCAL_DISK_SPILL_THRESHOLD=95`（勿用 80 覆盖） |
| O-4 | CIFS 空间告警 | 中心盘 ≥85% 告警；与 Agent HDD 告警分开 |
| O-5 | `jira/` 留存策略 | 终态 + 30 天 prune（cron/脚本 + 文档） |
| O-6 | `merge_result/` 本地清理 | 控制面 `{script_parent}/merge_result/` 按 mtime 30 天清理 |
| O-7 | RunConsole 日志清理 | `logs/console/` 按 30 天清理 |

---

## 五、根因分析：为什么这些缺陷会扎堆出现

上面列出的 P1-P4 缺陷和 §4 的差距，**不是彼此独立的 bug**。它们共享三个根因。不解决根因，修了这批还会有下一批。

### 根因 1：没有「设备日志事件」实体 —— 系统不知道自己在管什么

**证据链**（代码审查确认）：

| 阶段 | 事件的表示形式 | 状态追踪 |
|------|---------------|----------|
| Reconciler 发现新崩溃 | `processed_entries`（`state_store` 里的 JSON 列表，按 db_history **行**去重，非按目录） | 仅到「pull 完成」，不跨阶段 |
| Reconciler emit 信号 | `job_log_signal` 表一行，`extra.nfs_path`（字符串，非结构化外键） | 无 |
| UploadManager 上送 | `event_dir_names` 字符串列表（来自 `collect_upload_event_dir_names`——union 了 JobLogSignal 路径 basename + scan xls Path 列解析） | 靠 `dst_dir.exists()` 做幂等（文件系统去重，不可靠） |
| ScanRunner 扫描 | scan xls 的 `Path` 列（文本，由外部 `start_log_scan.py` 写入） | 无 |
| Merge 合并 | `PlanRunArtifact` 行（按 `plan_run_id` 聚合，无时间边界——累积所有轮次） | 仅靠 `created_at >= since` 做完备性统计，不影响合并内容 |
| Extract 提取 | merge xls 的 `Path` 列 → 文件名匹配 `devices/{plan_run_id}/{dirname}` | 靠 `dst.exists()` 跳过 |

**没有一个地方能回答**：「事件 X 现在在哪里？上送了吗？进哪个 PlanRun 了？HDD 上的副本可以删了吗？」

**具体后果**：
- `collect_upload_event_dir_names`（[`dedup_extract.py:117`](../../backend/services/dedup_extract.py)）用 **union(JobLogSignal 路径名 ∪ 解析 scan xls Path 列)** 拼凑事件列表——两个源各自有缺口：JobLogSignal 只有 Reconciler 发射的（inotifyd 兜底的 `extra=NULL`，永远无法通过文件名上送）；scan xls 只有扫描时 HDD 上还存在的目录（被 HddSpill 移走的扫不到）
- `find_event_dir_under_root`（[`event_dirs.py:52`](../../backend/agent/event_dirs.py)）遍历目录树取**字典序第一个匹配**——两台设备产生同名事件目录时，第二台被静默跳过
- same-named dirs 在 `devices/{plan_run_id}/{dirname}` 扁平布局下**必然碰撞**——因为没有按 serial 组织

### 根因 2：设备日志生命周期绑定 PlanRun 生命周期 —— 日志的命运不取决于自己

**当前链路**：

```text
设备崩溃 → Watcher 拉到 HDD
  → [等待 PlanRun 跑完，可能要几小时]
  → aggregator: should_trigger_dedup(run.status)
  → 只有 SUCCESS / PARTIAL_SUCCESS 才触发
  → enqueue scan_task → poll 300s → upload_task → poll 660s → merge → extract
```

**这意味着**：
- 设备上午 10:00 崩溃 → 日志可能下午 3:00 才到中心服务器
- PlanRun 终态是 FAILED → **永久不上送**（`should_trigger_dedup` 只对 SUCCESS/PARTIAL_SUCCESS 返回 True）
- PlanRun 被 abort → **永久不上送**
- 没有 PlanRun 在跑 → 日志堆在 HDD 上，永远不触发上送，直到被 HddSpill 溢出
- `scan_now` 命令在 Agent configure 前到达 → 静默跳过（P2-2），该轮 PlanRun 零产物
- scan 失败 → 只有本地 WARNING（P3-1），PlanRun 仍报 SUCCESS，运维不知道没有报表

**设备日志的安全到达，不应该依赖 PlanRun 的成败。**

### 根因 3：文件系统被当作数据库 —— 状态判定靠「文件在不在」

| 想知道什么 | 当前做法 | 为什么不靠谱 |
|-----------|---------|------------|
| 事件是否已上送 | `dst_dir.exists()`（[`upload_manager.py:175`](../../backend/agent/upload_manager.py)） | 并发 copytree 时不可靠；不能区分「正在写」和「写完」 |
| 事件属于哪个 PlanRun | 解析 scan xls 的 Path 列文本 | xls 格式由外部工具控制，变化时静默解析失败 |
| PlanRun 上送完备性 | `count_hosts_with_scan_artifacts` 数 NFS 上的文件 | 需要三维度收窄（host 去重 + triggered 集合 + since 水位线）才能避免误判——这是一个**补丁摞补丁**的典型 |
| 哪些事件目录在 HDD | `find_event_dir_under_root` 遍历 `**/YYYY-MM-DD_HH-MM-SS_*` | O(n) 目录树遍历，1000 个事件时每次调用扫全盘 |
| merge 合并范围 | glob `dedup/{plan_run_id}/*_org.xls` | 累积所有增量轮次，无时间边界——**上轮旧产物会混入本轮合并** |
| HddSpill 选择溢出目标 | 按 `st_mtime` 排序，取最旧 | **完全不知道目录是否被正在运行的 Job/Scan 引用**——会删掉正在被扫描或即将被上送的目录 |

**最严重的案例**：HddSpillMonitor 的 docstring 写「永不删除活跃 job 关联的事件目录」，但代码中**没有任何 job_id 检查**（grep 确认：`local_disk_monitor.py` 中 `job_id`/`active` 出现次数为 0）。它只按 mtime 排序，取最旧的，copytree 到 CIFS 后 `shutil.rmtree` 本地。如果一个正在运行的 Job 的 Reconciler 刚 pull 了一个旧时间戳的事件目录，HddSpill 可能在几秒后把它删掉——然后后续的 `upload_events` 收获 `source_missing`。

**更隐蔽的案例**：HddSpill 写 CIFS 的路径是 `devices/{folder}/{serial}/{dirname}`（嵌套，保留 HDD 相对路径），而 UploadManager 写的是 `devices/{plan_run_id}/{dirname}`（扁平）。两个路径结构互不兼容。`saq_tasks.py:_count_devices_event_dirs_sync` 只统计 depth-1 的 `devices/{plan_run_id}/` 下的目录——HddSpill 溢出的目录在 `devices/{folder}/{serial}/` 下，depth≥3，**永远不被计数、不被提取、在控制面不可见**。

### 三个根因的关系

```text
根因 1 (无事件实体)
  │
  ├──→ 没有统一 ID → 只能靠文件名/路径字符串关联 → 根因 3 (文件系统当数据库)
  │
  └──→ 没有生命周期状态机 → 上送时机无法独立表达 → 根因 2 (绑定 PlanRun 生命周期)
```

**根因 1 是源头**。只要引入 `DeviceLogEvent` 实体 + 状态机，根因 2 和根因 3 就有了解决的基础——事件有了自己的 ID 和生命周期，上送就可以独立于 PlanRun，状态查询就可以走 DB 而非文件系统。

---

## 六、重构方向与范围

### 6.1 核心思路

**引入 `DeviceLogEvent` 实体 + 解耦上送 + 文件系统降级为纯存储**。

不改核心链路（Plan→PlanRun→Job→脚本执行→聚合），只重构日志采集和上送这一条支线。

### 6.2 目标架构（对比当前）

```text
当前：
  Watcher → HDD 目录 ──[等 PlanRun 终态]──→ UploadManager → CIFS 目录
            └── HddSpill → CIFS 另一套目录（路径不兼容）
  事件归属靠解析 xls + union 文件名

目标：
  Watcher → DeviceLogEvent(LOCAL) ──[立即入队]──→ 连续上传 → DeviceLogEvent(REMOTE)
            └── HddSpill → 同一上传通道（查 state=LOCAL，只溢最旧的）
  事件归属靠 DB 查询（时间范围 + serial）
  PlanRun 终态时事件已经全部在 CIFS 上了
```

### 6.3 新增实体：`device_log_event`

```python
class DeviceLogEvent:
    """一个设备日志事件 = 一次崩溃/ANR/KE + 关联日志文件集合"""
    id: UUID                          # 全局唯一标识
    serial: str                       # 设备序列号
    platform: str                     # MTK / UNISOC / QCOM
    event_type: str                   # KE / NE / JE / ANR / HWT / SWT
    event_subtype: str | None         # ZZ_INTERNAL 解析结果
    detected_at: datetime             # Reconciler 发现时间（控制面时钟）
    device_timestamp: datetime | None # 设备侧时间戳
    
    # 存储状态（状态机，非文件系统探测）
    state: EventState                 # DETECTED → LOCAL → UPLOADING → REMOTE → ARCHIVED → PRUNED
    local_path: str                   # HDD 路径
    remote_path: str | None           # CIFS 路径（上送完成后设置）
    size_bytes: int
    checksum: str | None              # sha256，上送时校验完整性
    
    # 松散关联
    plan_run_id: int | None           # 关联到的 PlanRun（可空，可事后关联）
    host_id: int                      # 采集此事件的 Agent host
    job_id: int | None                # 采集此事件的 Job（可空）
```

**状态机**：

```text
DETECTED ──(adb pull 完成)──→ LOCAL
  │                             │
  └── pull 失败 → PULL_FAILED   ├──(开始上传)──→ UPLOADING ──(copytree + checksum OK)──→ REMOTE
                                │                    │
                                └──(磁盘压力大)       └── 失败 → UPLOAD_FAILED（可重试）
                                    HddSpill 直接
                                    走上传通道
                                    
REMOTE ──(被 PlanRun extract 引用)──→ ARCHIVED
LOCAL / REMOTE / ARCHIVED ──(本地 prune)──→ PRUNED
```

### 6.4 改动范围

| 模块 | 改动性质 | 影响 |
|------|---------|------|
| 新建 `device_log_event` 表 + ORM model + migration | 新建 | DB |
| 新建 `PlatformCollector` 协议 + MTK 实现（从 Reconciler 抽离） | 重构 | `agent/aee/` |
| 新建 `EventUploader` 后台线程（连续上送，替代 PlanRun 触发） | 新增 | `agent/` 新模块 |
| 改造 `UploadManager`：从「上送一批」改为「上送单个事件 + 状态更新」 | 重构 | `agent/upload_manager.py` |
| 改造 `HddSpillMonitor`：从「扫目录 copytree」改为「查 DB state=LOCAL → 走上传通道」 | 大幅简化 | `agent/local_disk_monitor.py` |
| 改造 SAQ 链：scan_task 只产 xls（不上传事件），upload_task 改为「确认事件已到 CIFS」 | 简化 | `tasks/saq_tasks.py` |
| 改造 `dedup_extract`：从「解析 xls 找事件名」改为「查 DB」 | 重构 | `services/dedup_extract.py` |
| `job_log_signal` 表添加 `device_log_event_id` 外键 | migration | DB |
| `PlanRunArtifact` 添加 `scan_round_id`（merge 轮次边界） | migration | DB |
| inotifyd 兜底事件可上送：`extra=NULL` 的 signal 也创建 DeviceLogEvent | 新增 | `agent/aee/processor.py` |
| 前端：PlanRun 详情页显示事件上送状态 | 新增 | `frontend/` 若干组件 |

**不动的模块**：Plan→PlanRun→Job→脚本执行→聚合（`aggregator.py`, `job_terminalization.py`）、Reconciler 核心逻辑（只抽离平台差异）、SocketIO 通信层、前端主页面结构。

**merge 产物的归属**（不在 DeviceLogEvent 范围内）：
- merge 仍跑在**控制面本地**——`start_log_scan.py -merge_files` 子进程需要本地脚本路径，无法迁到 CIFS
- 当前 merge 输出目录 `{script_parent}/merge_result/` 作为**中间缓存**，extract 完成后按保留期清理（§4.3）
- merge 的最终交付物（merge xls）随 extract 汇入 CIFS `jira/{plan_run_id}/`——JIRA 引用的是 CIFS 路径，非控制面本地路径
- DeviceLogEvent 解决的是**设备日志**的上送问题（`devices/` 下的原始事件目录）；merge xls 是**报表维度**的产物，走原有的 `PlanRunArtifact` 注册机制不变

**merge 轮次边界**（D-8 / P0-3）：
- 新增 `PlanRunArtifact.scan_round_id` 字段（`scan_task` 写入 `round_started_at` ISO 或自增 id）
- `_load_org_files_for_merge` 仅取 `created_at >= round_started_at` 的 artifact（或按 `scan_round_id` 过滤）
- `run_merge_sync` 不再累积全部历史轮次的 org 文件

### 6.5 架构补项索引（A-1～A-11）

与 §一「三、架构补项」一一对应：

| # | 补项 | 本文落点 |
|---|------|----------|
| A-1 | 连续上送与 PlanRun 解耦 | §6.2 目标架构、§6.4 表（EventUploader） |
| A-2 | 统一中心路径模型 | §6.4 P2-6 fix：`resolve_shared_storage_root()` |
| A-3 | DeviceLogEvent 与 PlanRun 关联 | §6.3 数据模型（`plan_run_id` 可空） |
| A-4 | HddSpill 改为溢最旧 LOCAL 事件 | §6.4 表（改造 HddSpillMonitor） |
| A-5 | scan 职责收窄 | §6.4 表（改造 SAQ 链） |
| A-6 | merge 轮次 ID | §6.4「merge 轮次边界」 |
| A-7 | extract 双源（切换窗口） | §4.2 D-9（`NFS_ROOT_LEGACY`） |
| A-8 | PlatformCollector 契约 | §0.4 + §6.4 表（阶段 3） |
| A-9 | L1 无 HDD → SSD | §0.3 L1b 落地设计 |
| A-10 | inotifyd 兜底事件可上送 | §6.4 表（`extra=NULL` → DeviceLogEvent） |
| A-11 | 与 Job 运行日志边界 | §0.2「关键区分」+ §6.4「不动的模块」 |

### 6.6 分阶段落地建议

| 阶段 | 内容 | 预计工作量 | 风险 |
|------|------|-----------|------|
| **1** | `DeviceLogEvent` 数据模型 + migration + Reconciler 写入（不改上送链路，仅新增写入） | 3-4 天 | 低——纯增量，不影响现有链路 |
| **2** | 连续上送后台线程 + 改造 `UploadManager`（新旧两条上送路径并存，feature flag 控制） | 4-5 天 | 中——涉及 Agent 侧新线程 + CIFS 并发写入 |
| **3** | `PlatformCollector` 协议 + MTK 实现 + 展锐存根 | 2-3 天 | 低——接口定义 + 现有逻辑搬家 |
| **4** | 改造 SAQ 链 + extract（切到 DB 查询，废弃文件系统解析） | 3-4 天 | 中——涉及 scan/upload/merge/extract 全链 |
| **5** | HddSpill 简化 + 前端补齐 + 旧路径清理 | 2-3 天 | 低——此时新路径已验证 |

**总计**：~3 周（14-19 天），含测试和灰度。

### 6.7 与当前 P1-P4 修复的关系

**不建议逐个修大多数 P2–P4 缺陷**——它们都是三个根因的表面症状。但有少数可以独立止血：

**可在阶段 1 独立修**（与重构无依赖）：
- P2-6（中心根不一致）：统一 `resolve_shared_storage_root()`——这是纯配置收敛，不依赖 DeviceLogEvent
- P2-2a（handler 窗口）：先 `set_control_handler` 再 `connect`——纯启动顺序调整
- P0-3（merge 轮次）：加 `created_at >= round_started_at` 过滤——不改数据模型

**必须在重构中解决**（依赖 DeviceLogEvent 状态机）：
- P2-1（`STP_DEDUP_SCAN_*` 角色混淆）：重构后 scan 不再负责触发 upload，env 角色边界重新划清
- P2-2b（configure 时序）：重构后连续上送有自己的就绪门禁
- P3-1（scan 失败 PlanRun 仍报 SUCCESS）：重构后 scan 只管产 xls，日志已通过连续上送到达 CIFS——scan 失败影响面缩小为「缺报表」
- P4-1（copytree 失败无重试）：`DeviceLogEvent` 有 `UPLOAD_FAILED` 状态 → 重试逻辑有处可挂

**推荐策略**：P1-1 / P4-2 已修；阶段 1 止血 P0-1(短期) + P0-3 + P1-2 + P2-6 + P2-2a；其余 P2–P4 暂缓至阶段 3。

---

## 七、缺陷修复优先级（重构前的临时止血）

对应 §六「建议落地顺序」的阶段 0 和阶段 1。

### 阶段 0：文档 ✅ 已完成（2026-08-09）

D-1～D-10 全部落地于本文 v3.0。§五+§六 决策已提取为 [`ADR-0028`](../adr/ADR-0028-device-log-event-and-continuous-upload.md)。

### 阶段 1：代码止血（3–5 天）

| 编号 | 标题 | 策略 |
|------|------|------|
| P1-1 | 磁盘使用率误判 | **已修**（`get_disk_usage` + spill + capacity `disk_unknown`） |
| P4-2 | LogArchiver grace=0 | **已修**（`MIN_GRACE_SECONDS=300`） |
| P0-1(短期) | HddSpill 溢出 extract 不可见 | extract 双根遍历 `devices/{plan_run_id}/` + `devices/{folder}/{serial}/` |
| P0-3 | 增量 merge 混入历史轮次 | merge 仅取 `created_at >= round_started_at` |
| P1-2 | ScanRunner 忽略 tag | 读 `STP_DEDUP_SCAN_TAG` |
| P2-6 | UploadManager/HddSpill 中心根不一致 | 文档侧同步：方案 C 文档 `STP_AEE_CIFS_ROOT` 改为「仅弃用别名」（P2-5 已修时代码已选此方向，spill 不独立分盘） |
| P2-2a | 启动无 handler 丢命令 | 先 `set_control_handler` 再 `connect` |

### 阶段 2：可观测（1–2 天）

| 编号 | 标题 |
|------|------|
| P1-3 | API 暴露 `run_context.archive` + 前端展示 N/M host |
| P3-1 | 零产物时 PlanRun 终态降级为 PARTIAL_SUCCESS |
| P3-2 | Agent 回传 upload_summary |
| P3-3 | `archived_jobs` 改名 `signaled_jobs` |
| P3-4 | extract 后写 `run_context.extract` |

以下**不建议单独修**，在阶段 3 重构中一并解决：

| 编号 | 原因 |
|------|------|
| P0-2 | DeviceLogEvent.state 替代 spill 的 job 感知 |
| P1-4 | 事件 ID 唯一目录名替代字典序 |
| P2-2b | 连续上送有自己的就绪门禁 |
| P2-3 | 重构后 scan/upload 独立于 watcher |
| P2-4 | 连续上送替代 fire-and-forget（或 Agent 拉取队列） |
| P2-5 | **已修**（`resolve_shared_storage_root` + HDD 不再回落中心键） |
| P4-1 | `UPLOAD_FAILED` 状态为重试提供基础 |
| P4-3 | `device_log_event_id` 软关联替代 CASCADE |
| P4-4 | DeviceLogEvent 表替代 outbox 依赖租约 |

---

## 八、验收标准（DoD —「全部修好」的可执行定义）

| 链路 | 验收用例 |
|------|----------|
| MTK 采集 | 注入 db_history 新行 → HDD 出现事件目录 + mobilelog/bugreport → `job_log_signal` 有 `nfs_path` |
| L1 降级 | 无 HDD 的 Agent（`os.access(W_OK)` 失败或 fs 为 tmpfs）→ 事件落 `STP_AEE_SSD_FALLBACK_ROOT`（默认 `{LOG_DIR}/aee_events`），HddSpill 自动禁用 |
| L2 溢出 | HDD 人工填满 ≥95% → spill → 中心可见副本 → extract/jira 能引用（P0-1 闭合） |
| 连续上送 | 事件产生后 5min 内 state=REMOTE（不等待 PlanRun 终态） |
| PlanRun 汇总 | 2 台 Agent 各 1 设备 → 终态后 `dedup/{id}/` 有 2 host xls → merge xls → `jira/{id}/` 含两设备目录 |
| 部分 host 失败 | 1 台 scan 失败 → UI 显示 1/2 host；`run_context.archive` 一致 |
| 增量 scan | 同一 `plan_run_id` 两轮 scan → merge 仅含第二轮 org 文件（P0-3 闭合） |
| factory side | `STP_DEDUP_SCAN_TAG=factory` → Agent scan 与 merge 同为 `-side factory`（P1-2 闭合） |
| 存储切换 | 改 env + reload → 新 PlanRun 产物在新根；双源 extract（O-1/D-9）验证 |
| FAILED PlanRun | 终态 FAILED → 设备日志仍在中心（连续上送后；P1-5 闭合） |
| 运行日志 | 不上中心；SSH 可读；grace 内（≥300s）不 prune 活跃 job |

---

## 九、落地顺序总表

| 阶段 | 范围 | 时间 | 内容 |
|------|------|------|------|
| **0** | 文档 | ✅ 已完成（2026-08-09） | D-1～D-10 + §4.5 + §八 + §九（本文 v3.0）；决策见 [`ADR-0028`](../adr/ADR-0028-device-log-event-and-continuous-upload.md) |
| **1** | 止血 | 3–5 天 | P1-1(已修) + P4-2(已修) + P0-1(短期) + P0-3 + P1-2 + P2-6 + P2-2a |
| **2** | 可观测 | 1–2 天 | P1-3 + P3-1～P3-4 |
| **3** | 重构 | ~2 周 | **✅ 方案 A 生产生效（2026-08-13）**——D1（DLE 表）+ D2（FAILED 触发 + upload_task 恢复）+ D3（UPLOAD_PENDING 状态追踪）；灰机 PlanRun #209 全链路验证通过（10 条 DLE：LOCAL→UPLOAD_PENDING→REMOTE→ARCHIVED/PRUNED）。剩余：P0-2（HddSpill 状态机替代）、P0-1（extract 双根）、旧代码清理 |
| **4** | 平台+运维 | 按排期 | A-8(UNISOC/QCOM) + O-1～O-7 + PRUNE_LOCAL fleet 决策（#217 灰机已验） |

---

**审查人**：Claude Code (Opus 4.8)  
**审查方式**：端到端三条链路代码审查 + 方案 C 权威约定核验 + 总体框架设计 + 根因分析  
**审查范围**：设备日志完整流转（Job 运行日志 / AEE 设备日志 / 扫描报表）+ 平台扩展路线  
**关键证据来源**：见 §二～§五各 Finding 的证据入口
