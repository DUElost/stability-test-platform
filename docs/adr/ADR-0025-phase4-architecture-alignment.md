# ADR-0025: Phase 4 架构对齐——单控制平面下 Agent 侧日志闭环与水平扩展推迟

- 状态：Accepted（已实现：Sprint 1–4 落地；2026-06-20 修订：D2/D4 重写——运行日志不上送 15.4 + AEE 设备日志先落 Agent 本地 HDD + scan 下沉 Agent + 上送触发扩展为五场景）
- 优先级：P2
- 目标里程碑：M4 核心闭环（Sprint 1-3，共 12-19 天）；D6 运维收口为触发条件驱动，不计入 M4 排期
- 日期：2026-06-13（2026-06-14 按「首次落地」视角重排优先级；2026-06-18 按长跑稳定性测试需求修订 D2/D4；2026-06-20 修订归档架构——运行日志不上送 15.4 + AEE 先落本地 HDD + scan 下沉 Agent + 五触发）
- 决策者：平台研发组
- 标签：架构, Watcher, 无人值守闭环, 日志归档, 去重, 水平扩展, 部署策略
- 关联：ADR-0018 (Watcher), ADR-0011 (可观测)

## 背景

五维评估报告（`docs/archive/assessments/architecture-five-dimensional-assessment-2026-06.md`）Phase 4 列出 7 项长期改动：SocketIO Redis adapter、APScheduler 外置、分布式限流、Loki 集中日志、Watcher CATCHUP、SAQ 多进程适配、Prometheus 多进程指标。评估假设"多后端实例 + 集中日志服务器"为终态架构。

经与项目实际部署需求对齐，发现原假设与项目背景存在根本偏差：

1. **部署形态**：同一局域网下一台控制平面（Windows 开发 / Linux 生产）+ 多 Agent 节点，只面向单 React Dashboard
2. **多实例需求**：平台需先在实际项目中跑顺，成熟后再考虑多控制平面或多 worker
3. **日志管理**：Loki 集中日志不契合——Agent 本地 SSD 256GB 存运行日志 + HDD 1TB 存设备日志，15.4 中心日志服务器 14TB（CIFS 共享 `//172.21.15.4/jxtinno/sonic_tinno`）仅存汇总报告与按需上送的事件；设备日志先落 Agent 本地 HDD，scan 在 Agent 本地执行，按需上送报告中 db 对应事件到 15.4；本地 HDD 达阈值后溢出上送
4. **Watcher 定位**：应在 Agent 侧完成完整闭环（检测→拉取→分析→归档），控制平面只做定时拉取和聚合展示

### 长跑稳定性测试需求（2026-06-18 补充）

平台核心场景是**数小时到数天的长跑稳定性测试**，价值在于「边跑边看」——过程中持续归档 + 增量去重 + 终态最终汇总。初版 D4 把归档定义为「Job 终态后一次性搬运」，长跑 Job 成为盲区。2026-06-18 修订明确：

1. **归档重定义为三阶段**：Agent 本地 scan + 按需上送事件/报告 + 分类提取
2. **过程中持续归档**：自动归档间隔 + 手动归档按钮（场景 4/5）
3. **增量去重 + 终态合并**：Agent 本地 scan 产 `_org.xls` → 上送 15.4 → 终态控制平面 `-merge_files` 合并
4. **15.4 中心日志服务器**：仅存汇总报告 + 报告中 db 对应事件 + 溢出事件；运行日志不上送
5. **日志存储结构**：mobilelog/bugreport 按 AEE 事件目录聚合，非统一 `correlated_*` 混放

### 排序依据（2026-06-14 补充）：平台尚未在实际项目中运用

本 ADR 初版（2026-06-13）以「成熟度加固」为标准，曾把运维收口（D6）提前为 Sprint 0。复盘发现一个更上位的事实：**平台尚未在任何真实项目中跑过**。在此前提下，评判「最明显增益」的标准不是成熟度，而是**能否让一个真实稳定性专项稳定地无人值守跑出价值、值得被采用**。

据此回归 `docs/project-vision.md` 的迭代落地原则：

1. **先确保「专项执行闭环」可稳定无人值守运行** ← 当前唯一应聚焦项
2. 再叠加「结果到报告/JIRA」的后处理自动化能力
3. 所有新专项复用统一编排框架

把愿景 9 步专项流程映射到现状，唯一拖累「无人值守」的承重项是 **Watcher 重启续航（D5）**：真实专项是数小时到数天的长跑，期间 Agent 必然重启（热更新本身是平台一等公民功能 + 崩溃 + 运维）。重启后崩溃检测能否恢复，是平台「无人值守抓崩溃」存在理由的承重项。

> 注：2026-06-14 逐行核实恢复链路后发现，watcher 实际**已能随 recovery_sync 的 RESUME 动作经 JobSession 自动重挂**（详见 D5），并非「完全不恢复」。但该链路无防回归测试、存在 RESUME 无 job_payload 的僵尸洞、且 enable.py 默认值与 main.py 不一致。D5 据此从「新建独立重挂」改为「验证并加固既有 RESUME 路径」。

因此重排优先级：**D5（Watcher 续航加固）为首次落地最高增益项，先做**；D4（LogArchiver）服务后处理链，列第二（对应原则 2）；D6（告警/备份运维收口）保护的是**尚不存在的生产负载，属过早投入，后移至首个真实专项稳定跑通后再启**。

## 决策

### D1: 水平扩展类改动 —— 推迟

涉及项：SocketIO Redis adapter / APScheduler 外置 / 分布式限流 / SAQ 多进程适配 / Prometheus 多进程指标

**理由**：
- 当前单控制平面单实例，无多实例需求
- 多 worker 涉及 7 处全局状态冲突（APScheduler 重复调度 / SocketIO room 分裂 / 内存限流 N 倍放大 / `_host_to_sid` 跨进程失效 / SAQ `enqueue_sync` 绑定单 loop / Reconciler Lock 不跨进程 / Prometheus 指标各进程独立），改动量 10-17 天，当前 ROI 低
- 推迟不损失功能：单实例下现行代码完全可用

**重启条件**（立项依据）：
- 设备池 >80 台，单后端 CPU/连接数近极限
- 需要零停机滚动重启
- 需要多用户同时操作（多控制平面）

### D2: Loki 集中日志 —— 不引入；15.4 中心日志归档服务器 —— 引入

**Loki 不引入**（理由不变）：
- Agent 日志归档是 Agent 侧职责，不是控制平面侧的集中式日志问题
- 引入 Promtail + Loki + Grafana 集成增加运维负担，且与"Agent 本地存储 + NFS 归档"模式冲突
- 当前 `log_writer.py`（后端写本地文件）+ Agent 写本地文件的模式对单控制平面已足够
- 日志下载端点已通过 NFS 路径读取 Agent 落盘文件（`runs.py` / `plan_runs.py` 的 `FileResponse`）

**15.4 中心日志归档服务器**（2026-06-20 修订，从「全量归档」改为「按需上送」）：

- **定位**：提单后开发访问的集中日志存储，非 Loki 式实时检索
- **形态**：现有 CIFS 共享 `//172.21.15.4/jxtinno/sonic_tinno`（上一代工具已用），Agent 通过本地 CIFS 挂载点写入，开发通过同一共享只读访问
- **存储内容**（仅三类，不含运行日志）：
  1. 各节点汇总报告（`Result_*_org.xls` / `Result_*_final.xls` / `Result_MergeFiles.xls`）
  2. 汇总报告中 db 对应的事件目录（AEE 原始文件 + mobilelog/ + bugreport/）
  3. 溢出事件目录（Agent HDD 超阈值时上送）
- **不是新基础设施**：是现有 NFS/CIFS 模式的延伸，不引入额外服务

**Agent 本地存储**（第一落点，15.4 非第一落点）：

| 存储 | 用途 | 容量 | 路径 |
|------|------|------|------|
| SSD | 运行日志（init/patrol/teardown），唯一物理存储 | 256GB | `/home/android/sonic_agent/logs/runs/{job_id}/` |
| HDD | AEE 设备日志（AEE + mobilelog + bugreport），第一落点 | 1TB | `/mnt/hdd/aee_events/{folder_name}/{serial}/` |

**运行日志访问方式**（2026-06-17 修订）：执行中经 SocketIO 推送到控制面（`GET /api/v1/logs/query`、LiveConsole）；事后经 `POST /api/v1/agent/logs`（SSH 读 Agent 磁盘）。不上送 15.4。~~原 Agent HTTP `:8900`（`run_log_server`）已废弃移除。~~

**替代方案**：Agent 侧实现日志归档调度器（LogArchiver，见 D4）

**重启条件**：
- 跨节点集中日志检索成为刚需且 NFS 模式不满足
- 需要与 Grafana 深度集成的日志探索体验

### D3: Watcher 定位对齐 —— Agent 侧完整闭环

**当前状态**：Watcher 做检测 + HTTP 上送信号元数据到后端 DB；crash 文件拉取到 NFS 但不做汇总/归档

**目标状态**：Agent 侧完成完整生命周期

| 阶段 | 当前 | 目标 | 缺口 |
|------|------|------|------|
| 检测 | ✅ inotifyd 实时监测 | 不变 | — |
| 拉取 | ⚠️ 默认路径A只拉单文件 | 路径B默认开：AEE整目录+dblog+bugreport+前后各2 mobilelog | reconciler 默认开启 + 存储结构改造 |
| 上送 | ✅ HTTP POST 元数据 → 后端 DB | 不变 | — |
| 分析 | ❌ | 归档-2：Agent 本地 start_log_scan 汇总去重 + 上送报告/事件 | 归档-2（Agent 本地 scan） |
| 归档 | ❌ | 归档-1 Agent 本地 scan+按需上送 → 归档-3 控制平面分类提取 → 15.4 | LogArchiver 改造 + scan 下沉 + 分类提取 |
| CATCHUP | ✅ RESUME 重挂已落地（Sprint 1） | 不变 | — |
| 控制面拉取 | ⚠️ 仅 Agent 主动推送 | 后端可按需拉取归档状态 + 去重结果 | 新端点（Sprint 3） |

**控制平面角色**：聚合展示 + merge 合并 + 归档-3 分类提取 + Jira 提单，不做日志实时存储。scan 在 Agent 侧执行，控制平面只合并+展示。

**Watcher 日志类型契约**（2026-06-18 增补，对齐上一代工具 `MonkeyAEEinfo_260523.py`）：
- **dblog**：`/data/aee_exp` + `/data/vendor/aee_exp` 的 AEE 整目录 + `db_history` 转储
- **bugreport**：crash 发生时导出，300s 冷却（避免短时多次 crash 刷爆）
- **mobilelog**：crash 时间前后各 2 个文件（main_log + kernel_log，默认开；sys_log 默认关）
- **存储结构**：mobilelog/bugreport 按事件目录聚合，非统一 `correlated_*` 混放（见 D4 路径约定）
- **实现**：路径 B（Reconciler `STP_WATCHER_AEE_RECONCILE_ENABLED`）已具备全部能力，改为默认开启

### D4: 日志归档——三阶段（搬运 + 汇总去重 + 分类提取）

> 2026-06-18 重写：原 D4 把归档定义为「LogArchiver 搬运 tar 到 NFS」，不含汇总/去重/分类。按长跑稳定性测试需求，归档重定义为三阶段，覆盖过程中持续归档 + 增量去重 + 终态最终汇总 + 分类提取到 15.4 中心日志服务器。

#### 归档-1：Agent 本地 scan + 按需上送（2026-06-20 修订，替代原「搬运运行日志到 15.4」）

> 2026-06-20 重大修订：原归档-1 定义为「LogArchiver 搬运运行日志到 15.4 CIFS 挂载点」。经架构澄清，运行日志不需上送 15.4（见 D2 修订）；AEE 设备日志的第一落点从 15.4 CIFS 挂载点改为 Agent 本地 HDD；scan 从控制平面下沉到 Agent 本地执行。归档-1 重新定义为「Agent 本地完成 scan + 按需上送事件/报告到 15.4」，LogArchiver 的「搬运+快照」职责取消。

**核心变更**：
- Watcher 拉取 AEE/mobilelog/bugreport → Agent 本地 HDD（非 15.4 CIFS 挂载点）
- scan 工具在 Agent 本地 Linux 运行（`backend/agent/resources/start_log_scan/`），扫描本地 HDD
- 15.4 只接收三件事：汇总报告 + 报告中 db 对应的事件目录 + 溢出事件目录
- 运行日志留在 Agent SSD；实时看控制面，事后 SSH 取证（见 D2 访问方式修订）

**Agent 本地存储布局**：

```
SSD 256GB: /home/android/sonic_agent/logs/runs/{job_id}/
  init_<step>.log
  patrol_<step>.log
  teardown_<step>.log
  （运行日志唯一物理存储，SSD 超阈值时 prune 最旧终态 Job）

HDD 1TB: /mnt/hdd/aee_events/{folder_name}/{serial}/
  aee_exp/{ts}_{db_path}/
    __exp_main.txt / main.dbg
    mobilelog/                        ← 该事件关联的 mobilelog
    bugreport/                        ← 该事件的 bugreport
  vendor_aee_exp/（同上）
  （AEE 设备日志第一落点，HDD 超阈值时上送最旧事件到 15.4 后 prune）
```

**15.4 路径约定**（{cifs_root} = CIFS 挂载点，Agent 写入 = 开发只读）：

```
{cifs_root}/
  devices/{folder_name}/{serial}/      ← 从 Agent 上送的 AEE 事件目录
    aee_exp/{ts}_{db_path}/
      __exp_main.txt / main.dbg
      mobilelog/
      bugreport/
    vendor_aee_exp/（同上）

  dedup/{plan_run_id}/{host_id}/       ← 各节点 scan 产物
    Result_*_20260618_1400.xls         ← 增量（保留历史）
    Result_*_final.xls                 ← 终态
  dedup/{plan_run_id}/merge/           ← 合并产物
    Result_MergeFiles_org.xls
    Result_MergeFiles.xls

  jira/{plan_run_id}/{issue_key}/      ← 提单目录（归档-3）
    {serial}/{ts}_{db_path}/
      __exp_main.txt / mobilelog/ / bugreport/
```

**上送流程**：

1. Agent 本地 scan → 产出 `Result_*_org.xls`
2. 从 scan 结果识别 db 事件 → 对应事件目录从本地 HDD 上送 15.4 `devices/`
3. scan 报告上送 15.4 `dedup/{plan_run_id}/{host_id}/`
4. 控制平面 merge（读各 agent 报告）→ 产出 `Result_MergeFiles.xls` → 15.4 `dedup/{plan_run_id}/merge/`

**HDD 溢出机制**：
- Agent 本地 HDD 超阈值时，最旧事件目录上送 15.4 `devices/` 后 prune 本地
- 类似原 LogArchiver 的 `LocalDiskMonitor` + `spill_oldest` 逻辑，但对象从「运行日志目录」改为「AEE 事件目录」，目的地不变（15.4）

**关键改动**（vs 原归档-1）：
- Watcher 路径 B 写入目标：15.4 CIFS 挂载点 → Agent 本地 HDD
- LogArchiver「搬运运行日志+快照」：取消（运行日志不上送 15.4）
- LocalDiskMonitor：监控 SSD 运行日志 prune + 监控 HDD 事件目录溢出上送
- scan 执行位置：控制平面 → Agent 本地 Linux
- 15.4 存储内容：运行日志目录树 + 事件目录 → 仅事件目录 + 报告

#### 归档-2：Agent 本地 scan + 报告上送（2026-06-20 修订，从控制平面下沉到 Agent）

**职责**：Agent 本地运行 `start_log_scan` 扫描本地 HDD 上的 AEE 事件目录，产出 `Result_*.xls`；上送报告和事件到 15.4。

**两模式**：

| 模式 | 触发 | CLI | 产物 | 保留 |
|------|------|-----|------|------|
| 增量 | 自动归档间隔 / 手动归档 | `start_log_scan.py -m 0 -d <本地HDD> -side <profile> [-pipeline <plan_run_id>]`（不带 `-end`；mode 0=AEE_TNE，无外部 DB 依赖） | `Result_*_org.xls`（累计去重） | 保留每次历史（可回溯去重演变） |
| 终态最终 | PlanRun 终态 | 同上 + `-end`（合并所有增量） | `Result_*_org.xls` + `Result_*.xls`（最终去重） | 供人工审核 |

**scan 执行位置**：Agent 本地 Linux（`backend/agent/resources/start_log_scan/`）。工具核心模块（`modules/analyse/aee/`、`modules/mode/ScanAeePlatform.py`、`modules/common/Excel.py`）无 Windows 硬依赖（xlwt/xlrd 纯 Python），Linux 可运行。

**多 agent 合并**（控制平面，`-merge_files`）：
```
# 各 agent 单独 scan 产 _org.xls
# 控制面集中合并
start_log_scan.py -merge_files agentA_org.xls agentB_org.xls -side shanghai -merge_priority
  → Result_MergeFiles_org.xls（含设备 SN 详情）
  → Result_MergeFiles.xls（跨设备去重终态）
```
- 合并输入必须用 `_org.xls`（含 `DeviceId` 列），不能用 final `.xls`（`DeviceCount` 是整数语义错）
- 合并输出落在 `<工具目录>/merge_result/<timestamp>/`，需后处理扫描取最新
- `-side shanghai`（默认）/ `-side factory` 需按部署侧显式指定

**RunConsole 复用**：scan 与 merge 均走 RunConsole（`run_key=scan:{plan_run_id}` / `merge:{plan_run_id}`），前端 LiveConsole 看实时日志。

**产物存储**：
```
{cifs_root}/dedup/{plan_run_id}/{host_id}/
  Result_*_20260618_1400.xls    ← 每小时增量（保留历史）
  Result_*_20260618_1500.xls
  ...
  Result_*_final.xls            ← 终态最终
{cifs_root}/dedup/{plan_run_id}/merge/
  Result_MergeFiles_org.xls     ← 集中合并
  Result_MergeFiles.xls
```

#### 归档-3：分类提取（控制平面，终态后/提单时）

**职责**：从去重 `Result_MergeFiles.xls` 的 db 路径，定位到 15.4 上已上送的事件目录，复制到提单目录。

**逻辑**：
1. 读 `Result_MergeFiles.xls` 的 Path 列（`__exp_main.txt` 路径）
2. 按 Path 定位到 15.4 上已上送的事件目录 `{cifs_root}/devices/{folder_name}/{serial}/aee_exp/{ts}_{db_path}/`
3. 复制该事件目录（含 AEE 原始文件 + mobilelog/ + bugreport/）到提单目录
4. 开发通过 CIFS 只读访问提单目录

**提单目录**：
```
{cifs_root}/jira/{plan_run_id}/{issue_key or ts}/
  {serial}/{ts}_{db_path}/
    __exp_main.txt
    main.dbg
    mobilelog/
    bugreport/
```

**前提**：归档-1 的存储结构必须按事件目录聚合（mobilelog/bugreport 下沉到事件目录内），否则分类提取无法按 db 路径定位关联日志。

#### 上送触发五场景（2026-06-20 修订，从三场景扩展为五场景）

| # | 场景 | 触发方式 | 上送内容 |
|---|------|---------|---------|
| 1 | 测试结束时间到达 | PlanRun SUCCESS/PARTIAL_SUCCESS，自动 | scan 终态报告 + 报告中 db 对应事件目录 |
| 2 | 手动停止测试 | 用户 abort → FAILED，前端确认后 | 同上 |
| 3 | 测试中断/失败 | PlanRun FAILED/DEGRADED，前端确认后 | 同上 |
| 4 | 测试过程中手动归档 | 测试详情页「手动归档」按钮 | scan 增量报告 + 报告中 db 对应事件目录 |
| 5 | 自动归档间隔 | 计划开始时设置的间隔时间，周期触发 | scan 增量报告 + 报告中 db 对应事件目录 |

**溢出上送**（独立于上述 5 个触发）：Agent HDD 超阈值时，最旧事件目录上送 15.4 后 prune 本地。

场景 1 自动；场景 2/3 需用户确认（中断/失败时日志可能不完整，归档价值需用户判断）；场景 4/5 为过程中归档，支持「边跑边看」。

### D5: Watcher 无人值守续航 —— 加固既有 RESUME 重挂路径（非独立重挂）——【首次落地最高增益项，优先执行】

**为什么是最明显增益**：Watcher 续航是「专项执行闭环可稳定无人值守运行」（愿景原则 1）的承重项。真实专项数小时到数天长跑，期间 Agent 必然重启（热更新一等公民 + 崩溃 + 运维），重启后若崩溃检测不恢复，该设备剩余时间静默不再抓 AEE/ANR，直接击穿平台「放着跑、崩溃替你盯住」的核心承诺。

**关键勘察修正（2026-06-14）**：初版 D5 设想「`manager.catchup_on_startup(active_jobs)` 独立重挂 DeviceLogWatcher」。逐行核实恢复链路后**否定该设计**——watcher 实际上已经能随 Job 恢复自动重挂：

```
Agent 重启
 → reconcile_on_startup()         清理上次残留 watcher_state→stopped（manager.py:439）
 → run_recovery_sync_if_needed()  上报 active_jobs（main.py:754）
 → 后端 recovery_sync 返回 RESUME + job_payload（agent_api.py:1668-1678 / 1709-1721）
 → execute_recovery_actions_impl → resume_job(payload)（main.py:267）
 → executor.submit(run_task_wrapper, payload)（main.py:739，与正常 claim 同一入口）
 → job_runner.run_task → JobSession.__enter__()（job_runner.py:188-197）
 → manager.start() → 重新挂载 DeviceLogWatcher  ← 崩溃检测已恢复
```

独立重挂是**冗余且有害**的：watcher 不变量要求「绑定 JobSession + stop(drain) 在释放设备锁前由 JobSession 调用」（manager.py:11-12），独立重挂会造出无 pipeline 执行、无锁释放协调的**孤儿 watcher**；且 `manager.start` 的 `already_running`（按 serial）守卫会让后续 RESUME 驱动的 JobSession.start 抛 `WatcherStartError`，**反而打断恢复**。

**因此 D5 改为「验证并加固既有 RESUME 路径」**，工作项：
- **enable.py 默认值统一**：`enable.py:11` `STP_WATCHER_ENABLED` 默认 `false` → `true`，与 `main.py:69` 一致（消除表述歧义；当前因 `plan_default` 默认 true 仍启用，属混乱非损坏）
- **端到端续航测试**（核心交付）：构造「活跃 patrol Job → Agent 重启 → recovery RESUME → watcher 重挂 → 信号续流」用例，把当前隐式可用的链路固化为防回归保证
- **堵 RESUME 无 job_payload 僵尸洞**：`agent_api.py:1710` job 行缺失时 RESUME 不带 payload → `resume_job` 不触发（main.py:256 守卫）→ job 登记 active 但 pipeline/watcher 永不恢复。改为缺 payload 时降级为 `CLEANUP`/`ABORT_LOCAL` 或显式告警
- **catchup 可观测**：watcher 重挂时区分「resume 重挂 vs 全新 claim」打点/日志，让运维能看到续航确实发生
- **数据来源契约（已澄清）**：catchup 不需要 recovery_sync 额外回吐 active_jobs——它搭载在已携带 `job_payload` 的 RESUME action 上。初版 D5 的「(a) 反推 / (b) 新增字段」二选一作废
- **续航前提依赖**：信号幂等由后端 `(job_id, seq_no)` UNIQUE + `ON CONFLICT DO NOTHING` 兜底（已落地：`models/job.py:158` + 迁移 `k9f0a1b2c3d4` + `agent_api.py:1293`），RESUME 重复上送天然安全

**超出 D5 范围、需单独立项**：RESUME 从 `run_task_wrapper` 顶部重跑 → patrol 中途的 Job 会重做 init（check_device/ensure_root/monkey_setup/资源推送/launch）。这是 Job 恢复语义问题（ADR-0019/0022 范畴），比 watcher 续航更大，本 ADR 仅标注不解决。

### D6: 单节点运维成熟度收口 ——【后移，触发条件：首个真实专项稳定无人值守跑通后】

**背景**：2026-06-13 代码核实发现，五维评估报告标记「Phase 1-3 已完成」的若干运维加固项，实际仅到「配置/脚本就位」，运维链路未闭合。这些项与被推迟的水平扩展（D1）正交，是单节点部署下的成熟度。

**排期决策（2026-06-14 修正）**：初版曾把本项提前为 Sprint 0。但平台尚未真实落地，告警与备份保护的是**尚不存在的生产负载**——在没有真实专项运行时，没有故障可告警、没有业务数据可丢失。故本项**后移**，不计入 M4 核心排期，触发条件为「首个真实专项已能稳定无人值守跑通（D5/D4 落地并验证）」。届时这三项合计 < 2 天即可收口。

| 项 | 当前状态（代码佐证） | 收口动作 |
|----|---------------------|---------|
| AlertManager 触达 | `deploy/prometheus/alertmanager.yml` 有 route + receiver 骨架，但 webhook 为占位 `127.0.0.1:5001/alerts`，钉钉/邮件配置全注释 | 接入真实值班通道（钉钉/飞书/邮件）+ 触发一次端到端验证（造一条 critical 告警确认触达） |
| PG 备份调度 | `scripts/pg_backup.sh`（含 `-mtime` 轮转）+ `scripts/pg_restore_test.sh` + `docs/preprod-drill-runbook.md` 齐备；脚本头第 10 行 cron 仅注释示例 | 安装 systemd timer 或 cron 真正调度每日备份；按 runbook 跑一次恢复演练并记录 RTO |
| Grafana 导入 | `docs/grafana/stability-platform-dashboard.json` 仅模板，无 provisioning | 配置 provisioning 自动导入 + 验证生产 scrape 到本平台指标 |
| RBAC 分级（可选） | `AdminRoute`（`router/index.tsx:99`）单档 admin/非 admin，覆盖 users/notifications/settings/audit | 评估是否需 read-only/operator 第三档；若否，记录「单档已满足当前用量」并关闭该项 |

**理由（后移后仍成立的价值，但非首次落地前置）**：
- 告警未触达 = R3「故障靠人工」实质未解——但首个专项跑通前无真实故障流，价值滞后
- 备份脚本就位但无调度 = R5「无自动备份」实质未解——但首个专项跑通前无真实业务数据，风险滞后
- 三项改动量 < 2 天、不碰业务代码，跑通后顺手收口即可，无需阻塞核心闭环

**不纳入本 ADR 的成熟度项**（记录但不在 M4 排期，避免范围蔓延）：
- SettingsPage 静态占位（`SettingsPage.tsx` 全硬编码，无后端配置端点）——需求待确认，单独立项
- 批量 Job 报告导出 zip/PDF（当前仅单 PlanRun JSON/MD）——测试经理需求驱动，单独立项
- 前端 API 客户端双风格统一（~5 模块走 unwrap / 7 模块裸响应，`management.ts` 24 调用全裸）——技术债，渐进收敛
- Plan 链失败中止策略（`plan_chain_trigger.py` 仅回滚标记不中止上游）——能力增强，单独立项

## 影响

### 推迟项的影响（可接受）

| 推迟项 | 当前约束 | 接受理由 |
|--------|---------|---------|
| SocketIO Redis adapter | 单后端崩溃 = 全平台不可用 | 设备池 <80，非工作时间崩溃概率低 |
| APScheduler 外置 | 重启后定时任务重新计时 | 重启频率低（月度升级），interval 任务重新计时影响有限 |
| 分布式限流 | 多 worker 限流 N 倍放大 | 当前单 worker，不影响 |
| Loki | 跨节点日志需 SSH | NFS 归档模式替代，LogArchiver 补齐 |

### 新增项的影响

| 新增项 | 影响范围 |
|--------|---------|
| 归档-1 Agent 本地 scan + 按需上送 | LogArchiver「搬运+快照」取消；Watcher 路径 B 从 15.4 CIFS 改为本地 HDD；scan 下沉到 Agent 本地；15.4 只存报告+事件 |
| 归档-2 Agent 本地 scan | Agent 本地运行 start_log_scan（Linux），产出 Result_*.xls 上送 15.4；控制平面只做 -merge_files 合并 |
| 归档-3 分类提取 | 控制平面：按 Result.xls 的 db 路径从 15.4 `devices/` 取事件目录复制到提单目录 |
| 15.4 中心日志服务器 | 现有 CIFS 共享，非新基础设施；仅存汇总报告 + 按需上送事件 + 溢出事件 |
| 运行日志访问 | 控制面实时（SocketIO + `/logs/query`）+ 事后 SSH（`/agent/logs`），不上送 15.4 |
| Watcher 路径 B 默认开 | `STP_WATCHER_AEE_RECONCILE_ENABLED` 默认 true；mobilelog/bugreport 存储结构改造 |
| 上送触发五场景 | PlanRun 终态链路 + 前端交互（自动 + 提示确认 + 手动归档 + 自动间隔） |
| 详情页数据展示 | 新增归档状态区 + 去重报告区 + crash 详情下钻 |
| Watcher 续航加固（已落地） | enable.py 1 行 + agent_api RESUME 降级 + main.py 打点 + 测试 |
| 归档状态端点（已落地） | 后端只读端点，不影响现有 API |

## 实施计划

> 执行顺序：Sprint 1（已落地）→ Sprint 2（已落地，需改造）→ Sprint 3（已落地，需扩展）→ Sprint 4（归档-2/3 新增）。
> Sprint 1-3 为 M4 核心闭环首次落地路径；运维收口阶段（D6）触发条件驱动，跑通后再启。

### Sprint 1（3-5 天）：Watcher 无人值守续航——加固 RESUME 重挂路径 ——【已落地】

> 详细实现计划见 [`archive/sprints/adr-0025-watcher-catchup-implementation-plan-2026-06-14.md`](../archive/sprints/adr-0025-watcher-catchup-implementation-plan-2026-06-14.md)（已落地，已归档）。

| 步骤 | 文件 | 改动 | 状态 |
|------|------|------|------|
| 1 | `backend/agent/watcher/enable.py:11` | `STP_WATCHER_ENABLED` 默认值 `false` → `true` | ✅ 已落地 |
| 2 | `backend/api/routes/agent_api.py:1709-1721` | RESUME 缺 `job_payload` 时降级为 CLEANUP/ABORT_LOCAL | ✅ 已落地 |
| 3 | `backend/agent/main.py:243-275` | RESUME 重挂打点 | ✅ 已落地 |
| 4 | `backend/agent/tests/` | 端到端续航测试 | ✅ 已落地 |
| 5 | 回归 | 既有 recovery + watcher 用例全过 | ✅ 已落地 |

### Sprint 2（5-8 天）：Watcher 路径 B 改本地 HDD + 存储结构改造 + LogArchiver 改造

> 2026-06-20 修订：原 Sprint 2「LogArchiver 搬运运行日志到 15.4」已取消。改为：Watcher 路径 B 写入目标从 15.4 CIFS 挂载点改为 Agent 本地 HDD；LogArchiver「搬运+快照」取消，只保留 SSD prune + HDD 溢出上送；上送逻辑新增。

| 步骤 | 文件 | 改动 | 优先级 |
|------|------|------|--------|
| 1 | `backend/agent/aee/reconciler.py:151` | `STP_WATCHER_AEE_RECONCILE_ENABLED` 默认 `false` → `true` | 高 |
| 2 | `backend/agent/aee/processor.py:211,219` | mobilelog/bugreport 的 `output_dir` 改为 `local_target_dir`（事件目录） | 高 |
| 3 | `backend/agent/aee/paths.py` | `get_aee_nfs_root()` 默认值从 15.4 CIFS 挂载点改为本地 HDD 路径（`/mnt/hdd/aee_events` 或 env `STP_AEE_LOCAL_ROOT`） | 高 |
| 4 | `backend/agent/aee/paths.py` | `resolve_device_output_dir` 输出路径指向本地 HDD | 高 |
| 5 | `backend/agent/log_archiver.py` | 删除 `_do_archive`（搬运运行日志到 15.4）和 `snapshot_active_job`（快照）；保留 `_iter_job_dirs` + SSD prune 逻辑 | 高 |
| 6 | `backend/agent/log_archiver.py` | 新增 HDD 溢出上送：监控 HDD 使用率，超阈值时最旧事件目录上送 15.4 `devices/` 后 prune | 高 |
| 7 | `backend/agent/main.py` | 删除 cycle_snapshot_callback 注入；LogArchiver 配置移除 nfs_base_dir | 高 |
| 8 | `backend/agent/main.py` | ~~新增 Agent HTTP 运行日志下载端点（方案 A）~~ → **已废弃**（2026-06-17 移除 `run_log_server`） | 高 |
| 9 | 测试 | Watcher 路径 B 写本地 HDD + SSD prune + HDD 溢出上送 + 运行日志 HTTP 下载 | 高 |

### Sprint 3（2-3 天）：控制平面展示扩展

> 原 Sprint 3 已落地 watcher-summary + WatcherSummaryCard。2026-06-18 扩展：详情页接线归档状态 + crash 详情下钻。

| 步骤 | 文件 | 改动 | 优先级 |
|------|------|------|--------|
| 1 | `frontend/src/pages/execution/PlanRunDetailPage.tsx` | 接线归档状态区（复用 watcher-summary `archive` 字段 + 立即归档按钮） | 高 |
| 2 | `frontend/src/components/plan-run/AnomalyDashboard.tsx` | 包名榜行内增补「查看 N 条详情」下钻，调用 `listJobArtifacts` | 中 |
| 3 | `frontend/src/components/plan-run/DeviceDetailDrawer.tsx` | 增补「Crash 产物」区块 | 中 |
| 4 | `backend/api/routes/plan_runs.py` | watcher-summary 补充按事件目录组织的 crash 详情端点 | 中 |
| 5 | 测试 | 前端归档状态展示 + crash 详情下钻 | 中 |

### Sprint 4（8-12 天）：Agent 本地 scan + 按需上送 + 归档-3 分类提取 + 五触发

> 2026-06-20 修订：scan 从控制平面下沉到 Agent 本地；上送为新增核心逻辑；终态触发扩展为五场景。

| 步骤 | 文件 | 改动 | 优先级 |
|------|------|------|--------|
| 1 | `backend/agent/resources/start_log_scan/` | 放置 scan 工具（从 `F:\automation-toolkit\python-tools\stability_Start-Log-Scan`）；确认 Linux 兼容 + 依赖 xlwt/xlrd | 高 |
| 2 | `backend/agent/scan_runner.py`（新建） | Agent 本地 scan 执行器：调用 start_log_scan.py 扫描本地 HDD，产出 Result_*.xls | 高 |
| 3 | `backend/agent/upload_manager.py`（新建） | 按需上送：scan 报告 → 15.4 `dedup/`；报告中 db 对应事件目录 → 15.4 `devices/` | 高 |
| 4 | `backend/models/plan_run.py` | `plan_run_artifact` 表（FK plan_run，artifact_type=scan_result_xls / merge_result_xls） | 高 |
| 5 | `backend/api/routes/dedup.py` | 保留 scan/status/merge/extract 端点（scan 改为触发 Agent 本地 scan + 等待上送结果） | 高 |
| 6 | `backend/services/run_console.py` | 保留 `on_complete` 回调钩子 | 高 |
| 7 | `backend/tasks/saq_tasks.py` | `scan_task`（Agent 本地执行）+ `upload_task`（上送报告/事件到 15.4）+ `merge_task`（控制平面合并） | 高 |
| 8 | `backend/services/aggregator.py` | PlanRun 终态触发：scan_task + upload_task + merge_task | 高 |
| 9 | `backend/api/routes/plan_runs.py` | 上送触发五场景：终态自动 + abort/失败确认 + 手动归档按钮 + 自动归档间隔 | 高 |
| 10 | `frontend/src/pages/execution/PlanRunDetailPage.tsx` | 去重报告区 + 手动归档按钮 + 终态确认交互 | 高 |
| 11 | `backend/models/plan.py` | Plan 表新增 `auto_archive_interval_seconds` 列（计划开始时设置自动归档间隔） | 中 |
| 12 | 归档-3 | `POST /plan-runs/{run_id}/dedup/extract`：从 15.4 `devices/` 取事件目录复制到提单目录 | 中 |
| 13 | 测试 | Agent 本地 scan + 上送报告/事件 + merge + extract + 五触发 | 高 |

### 运维收口阶段（1-2 天，D6）——触发条件：首个真实专项稳定无人值守跑通后

> 不计入 M4 核心排期。Sprint 1-4 落地、且有真实专项稳定跑通后再启动。

| 步骤 | 文件 | 改动 |
|------|------|------|
| 1 | `deploy/prometheus/alertmanager.yml` | 接入真实值班 webhook（钉钉/飞书/邮件），替换占位 URL |
| 2 | 端到端 | 造一条 critical 告警验证触达值班通道 |
| 3 | `deploy/control-plane/systemd/`（新建 timer）或 crontab | 调度 `scripts/pg_backup.sh` 每日运行 |
| 4 | 运维 | 按 `docs/preprod-drill-runbook.md` 跑一次 `pg_restore_test.sh` 恢复演练，记录 RTO |
| 5 | Grafana provisioning | 自动导入 `docs/grafana/stability-platform-dashboard.json` + 验证 scrape |
| 6 | 决策 | RBAC 第三档评估：需要则立项，不需要则在 ADR 标注关闭 |

## 2026-06-27 运维加固（scan 风暴收口）

PlanRun #54 失败复盘后，对五触发场景⑤与 Agent scan 并发做如下约束（实现：`cron_scheduler.auto_archive_sweep`、`agent/scan_runner.py`、`agent/main.py`）：

| 规则 | 行为 |
|------|------|
| Plan 选型 | 每 Plan 每轮最多 1 条 run：优先 **RUNNING**，否则 **最新终态**（`max(id)`） |
| 终态 scan | 仅首次（`is_final=True`，无 `scan_result_xls`）；**已有 artifact 则永久跳过** |
| RUNNING scan | patrol 期间按 `auto_archive_interval_seconds` 增量 scan（`is_final=False`） |
| Agent 并发 | 单 worker + FIFO 队列；同 `plan_run_id` 在队列中 coalesce 为最新一条 |
| 控制面缺口 | Agent 队列与 SAQ poll 独立；poll 超时仍可能 best-effort 进入 upload/merge |
| Host 心跳 | 控制面 `HOST_HEARTBEAT_TIMEOUT_SECONDS` 建议 **300**（长跑 patrol + scan 时 HTTP 心跳可能 >120s 间隔） |

## 验证

1. **Sprint 1**（已落地）：Agent 重启后 Watcher 自动恢复 + 信号不丢失 + enable.py 默认值一致
2. **Sprint 2**：Watcher 路径 B 写 Agent 本地 HDD + SSD prune 可释放空间 + HDD 溢出上送 15.4 + mobilelog/bugreport 在事件目录内 + 运行日志经控制面实时/SSH 可访问
3. **Sprint 3**：前端显示归档状态 + crash 详情可下钻 + 下载端点可访问 15.4 归档文件
4. **Sprint 4**：Agent 本地 scan 产 `_org.xls` + 上送报告/事件到 15.4 + `-merge_files` 合并含设备 SN + 增量历史保留 + 上送触发五场景 + 归档-3 按 db 路径从 15.4 `devices/` 提取事件目录到提单目录
5. **全局回归**：`python -m pytest backend/tests/` + `npx vitest run` 全过
6. **运维收口阶段（触发后）**：critical 告警可触达值班通道 + pg_backup 每日产物落地 + 恢复演练 RTO 记录 + Grafana 可见本平台指标

## 索引

- 评估报告：`docs/archive/assessments/architecture-five-dimensional-assessment-2026-06.md` Phase 4
- Watcher 主线：ADR-0018（需补充日志类型契约 + 路径 B 默认开 + 存储结构改造）
- 可观测路线：ADR-0011
- 派发门禁：ADR-0021
- 设备租赁：ADR-0019
- 归档工具：`backend/agent/resources/start_log_scan/`（从 `F:\automation-toolkit\python-tools\stability_Start-Log-Scan` 放入，Agent 本地 Linux 运行）
- 上一代工具：`MonkeyAEEinfo_260523.py`（日志类型 + CIFS 挂载 + mobilelog 时间窗参考）
- Sprint 2 实施计划（已归档）：`docs/archive/sprints/plans/2026-06-20-sprint2-watcher-hdd-logarchiver.md` → 见 `design/2026-plan-c-storage-and-access.md`
- dedup 设计（已归档）：`docs/archive/migrations/adr-0025-dedup-integration-design-2026-06-16.md` → 见 `design/01-execution-pipeline.md` §8
