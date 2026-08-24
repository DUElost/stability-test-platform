# ADR-0028：设备日志事件实体 + PlanRun FAILED 触发上送 + 存储路径收敛

- 状态：Accepted（方案 A 修订——2026-08-12）
- 优先级：P1
- 目标里程碑：阶段 3 重构完成
- 日期：2026-08-09（初版）；2026-08-12（方案 A 修订）
- 决策者：平台研发组
- 标签：设备日志, DeviceLogEvent, PlanRun FAILED 上送, HddSpill 修复, PlatformCollector, 存储收敛
- 背景分析：[`DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](../reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md)（v3.0）

## 背景

ADR-0025（方案 C 存储）建立了三层模型：**Agent HDD 做预处理工作区 → 按需去重/筛选 → CIFS 只收有效子集**。但实现中有三个异常路径缺口：

1. **PlanRun FAILED/ABORTED → 永久不上送**：`should_trigger_dedup` 仅对 SUCCESS/PARTIAL_SUCCESS 返回 True。事件困在 HDD 上，只能靠 HddSpill ≥95% 被动溢出——且溢出路径 extract 不可达（P0-1）
2. **无事件实体**：一个设备崩溃经过五个阶段，每个阶段用不同方式表示，无法回答「事件 X 在哪里」
3. **文件系统当数据库**：状态判定靠 `dst_dir.exists()`、目录遍历、解析 xls 文本

三个根因在 20 host 规模下勉强运维，60 host × 1000 device × 三平台下会系统性失效。详细证据见审查报告 §五。

ADR-0028 初版（2026-08-09）选择了**翻转存储模型**——所有事件无筛选推 CIFS（连续上送全量）。经讨论（2026-08-12），确认此举偏离了 ADR-0025 的设计意图（「HDD 预处理 → CIFS 精选」）。本修订回退到**保留过滤模型、仅修异常路径**的方案 A。

## 设计基调（方案 A）

```
手机 → Agent HDD（1TB，预处理工作区）← 主副本
         │
         ├── scan（去重、挑有效事件 → Result_*_org.xls）
         │
         ├── PlanRun 终态（含 FAILED）→ upload  ← 只上送 scan 引用的有效事件
         │    └→ CIFS devices/{run_id}/
         │         └→ merge → extract → CIFS jira/{run_id}/
         │
         └── HDD ≥95% → HddSpill → CIFS devices/{folder}/{serial}/
              （溢出路径，extract 双根可读——修 P0-1）
```

**不翻转**：CIFS 仍只收「汇总报告 + scan 引用的有效事件 + HDD 溢出」。HDD 保留为主副本和预处理工作区。

## 决策

### D1：引入 `DeviceLogEvent` 实体 + 生命周期状态机

新建 `device_log_event` 表作为设备日志事件的**唯一权威记录**：

| 字段 | 说明 |
|------|------|
| `id`（UUID） | 全局唯一标识 |
| `serial` | 设备序列号 |
| `platform` | MTK / UNISOC / QCOM |
| `event_type` / `event_subtype` | KE / NE / JE / ANR / HWT / SWT（从 ZZ_INTERNAL 或平台等价格式解析） |
| `detected_at` | Reconciler 发现时间（控制面时钟） |
| `device_timestamp` | 设备侧时间戳（可空） |
| `state` | `DETECTED → LOCAL → UPLOAD_PENDING → REMOTE → ARCHIVED → PRUNED`（+ `PULL_FAILED` / `UPLOAD_FAILED`） |
| `local_path` | HDD/SSD 路径 |
| `remote_path` | CIFS 路径（`upload_task` 或 HddSpill 完成后设置，可空） |
| `size_bytes` / `checksum` | 上送校验 |
| `plan_run_id` | 关联 PlanRun（可空） |
| `host_id` / `job_id` | 采集此事件的 Agent host / Job（可空） |

状态转换（方案 A）：

```text
DETECTED ──(adb pull 完成)──→ LOCAL
  │                             │
  └── pull 失败 → PULL_FAILED   ├──(PlanRun scan 引用)─→ upload_task 标记 ─→ UPLOAD_PENDING
                                │                          │
                                │                          └─→ EventUploader（30s 轮询）copytree + checksum ─→ REMOTE
                                │                                                       │
                                │                                                       └── PRUNE_LOCAL=1 → rmtree 本地 → PRUNED
                                │
                                └──(未被 scan 引用)──→ 保持 LOCAL
                                      │
                                      └── HDD ≥95% → HddSpill → EventUploader(force=True) copytree → REMOTE
```

`REMOTE` 仅由 **EventUploader** 写入（copytree + checksum 完成后）；其来源是 `upload_task` 标记的 `UPLOAD_PENDING`（PlanRun 触发，scan 筛选）或 HddSpill（磁盘压力溢出，`force=True`）。`upload_task` 本身不写 `REMOTE`，只做 `LOCAL → UPLOAD_PENDING` 标记。**不存在「连续上送全量」路径**——`STP_EVENT_UPLOADER_CONTINUOUS` 逃生阀已删除（#287），过滤模型是唯一路径。

失败态：
- `PULL_FAILED`：无 `local_path`。Collector 可按同一 trigger 重试 → `DETECTED`。
- `UPLOAD_FAILED`：本地副本仍在。`upload_task` 或 HddSpill 失败后重试。

与现有表的关联：
- `job_log_signal` 表新增 `device_log_event_id` 外键（可空，`SET NULL` on delete）
- `JobLogSignal.job_id` 的 `ondelete` 从 `CASCADE` 改为 `SET NULL`

### D2：保留过滤模型，修 PlanRun FAILED 上送缺口

**ADR-0025 原意**：CIFS 只收有效子集。**保留此模型，不翻转。**

**当前缺口**：`should_trigger_dedup` 仅对 `SUCCESS` / `PARTIAL_SUCCESS` 返回 True。PlanRun FAILED → 永久不上送。事件困在 HDD，只能靠 HddSpill ≥95% 溢出。

**改为**：

| PlanRun 终态 | scan | upload | merge | extract → jira/ |
|-------------|------|--------|-------|-----------------|
| SUCCESS | ✅ | ✅ | ✅ | ✅ |
| PARTIAL_SUCCESS | ✅ | ✅ | ✅ | ✅ |
| **FAILED** | ✅ | ✅ | ❌ | ❌ |

- `_DEDUP_AUTO_STATUSES`（`backend/services/dedup_scan.py` 的 `should_trigger_dedup`）含 `"FAILED"`
- FAILED 走 scan→upload（事件到达 CIFS），但不走 merge/extract（运行失败，产 jira/ 无意义）
- 门禁为显式实现（并非依赖 scan xls 不足自然跳过）：
  - 路由层（`backend/api/routes/dedup.py` `trigger_merge` / `trigger_extract`）：FAILED PlanRun 直接 `409`（`PlanRun FAILED：按 ADR-0028 D2 不执行 merge/extract`），且先于 merge precheck
  - SAQ 层：`run_merge_sync` 对 FAILED 显式跳过（log `merge_skip_failed_plan_run`，返回空串）
  - extract 在 SAQ 链无独立门禁：merge 无产物 → extract 以 `-1`（no merge artifact）短路；显式拒绝仅在路由层 409

**upload_task 恢复**（方案 A 已恢复其 enqueue 链）：

```
scan_task → scan_now → poll → register artifacts → enqueue upload_task
          → enqueue merge_task
```

`upload_task` 从 scan xls 的 Path 列提取事件目录名——正是「只上送有效子集」的过滤逻辑。`collect_upload_event_dir_names` 保留。

**EventUploader = Agent 侧唯一执行者**：30s 轮询拉取 `UPLOAD_PENDING`，执行 copytree 到 CIFS 并回写 `REMOTE`（含重试/checksum/PRUNE）。`upload_task`（控制面）只做 DB 标记，不执行文件拷贝。

**auto_archive_sweep**（`cron_scheduler.py`）：仅 Plan 配置了 `auto_archive_interval_seconds` 时触发。`_AUTO_FINAL_STATUSES` 加 `FAILED`（可选——FAILED 在终态时已由路径①处理，定时扫可做兜底）。

### D3：DeviceLogEvent 追踪事件状态，文件系统仍是主存储

`device_log_event` 表用于**追踪事件生命周期状态**——不是替代文件系统。文件系统仍是事件的主存储（HDD 为第一落点），DB 只回答「事件在哪里、什么状态」。

| 原来（无 DLE） | 改为（有 DLE） |
|---------------|---------------|
| `dst_dir.exists()` 判断是否已上送 | `SELECT state FROM device_log_event WHERE id = ...` |
| 无事件级状态追踪 | `state` 机：`LOCAL → REMOTE → PRUNED` |
| HddSpill 按 mtime 盲删 | 查 `state=LOCAL` + `detected_at` 排序，跳过活跃事件 |
| merge 跨轮次混入 | `scan_round_id` 过滤 |

不上送的事件（未被 scan 引用）长期保持 `LOCAL`——这是**有意设计**，靠 HddSpill 或后续 PlanRun 的 scan 覆盖。

### D4：`PlatformCollector` 协议

同初版，不变。

### D5：L1 存储降级（无 HDD → SSD）

同初版，不变。

### D6：统一中心存储根

同初版，不变。

### D7：merge 轮次边界

同初版，不变。

### D8：extract 存储切换双源读

同初版，不变。

### D9：HddSpill 溢出路径 extract 可读（P0-1 修复）

extract 双根遍历：
- `devices/{plan_run_id}/` —— upload_task 上送的
- `devices/{folder}/{serial}/` —— HddSpill 溢出的（legacy 遍历，匹配 merge xls 引用的目录名）

等旧 HddSpill 溢出数据自然淘汰后，可移除第二根。

## 后果

### 正面

- **符合 ADR-0025 原意**：HDD 做主存储和预处理工作区，CIFS 只收有效子集
- **PlanRun FAILED 不再丢事件**：scan→upload 照跑，事件到达 CIFS
- **事件可追踪**：全生命周期有唯一 ID 和状态
- **平台扩展有明确接口**

### 负面

- PlanRun FAILED 时 scan 可能产不出足够的 xls（取决于失败发生在哪个阶段）——merge/extract 由 D2 显式门禁拒绝，但事件可能因无 xls 引用而不被上传
- EventUploader 的 copytree 逻辑保留（执行者定位，不回退）；`CONTINUOUS=1` 仅作为逃生阀模式
- `JobLogSignal.job_id` 已由 `CASCADE` 改为 `SET NULL`（migration g5b6c7d8e9f0）

### 与 ADR-0028 初版（全量上送）的对比

| | 初版（全量） | 方案 A（过滤） |
|---|---|---|
| CIFS 存储量 | 全部事件 | 仅有效子集 + 溢出 |
| 带宽消耗 | 全量 copytree | 仅有效子集 copytree |
| HDD 角色 | 缓存 | 主副本 + 预处理工作区 |
| 上送触发 | EventUploader 持续 | PlanRun 终态（含 FAILED） |
| 未选中事件 | 也上送了 | 留在 HDD，靠 HddSpill 或后续 PlanRun |
| ADR-0025 一致性 | 偏离 | 一致 |

### 不改变的部分

- Plan→PlanRun→Job→脚本执行→聚合核心链路
- SocketIO 通信层
- SAQ 任务链框架
- 运行日志（`logs/runs/{job_id}/`）策略
- D1/D4/D5/D6/D7/D8 设计

### 分阶段落地

| 阶段 | 内容 | 时间 | 状态 |
|------|------|------|------|
| 1（止血） | P0-1（extract 双根）+ P0-3（merge since）+ P2-6（文档同步）+ P2-2a（handler 顺序） | 3–5 天 | P2-6 已落地（`resolve_shared_storage_root()`）；其余按排期 |
| 2（可观测） | API 暴露 `run_context.archive` + 前端 N/M host | 1–2 天 | 待排期 |
| **3（重构）** | **方案 A 实施**：D1（DLE 表）+ D2（FAILED 触发 + 恢复 upload_task）+ D3（状态追踪）+ D4–D9 | ~2 周 | **✅ 生产生效（2026-08-13）** |
| 4（平台入口+运维） | UNISOC/QCOM stub + 存储切换 SOP + PRUNE_LOCAL fleet 决策 | 按观察窗 | UNISOC/QCOM stub 已锁定（#220）；PRUNE_LOCAL 灰机验证通过（#217），fleet 待决策 |

### 方案 A 生产实施记录（2026-08-13）

**代码**（main 提交序列）：
- `bce5177`：`_AUTO_STATUSES` 加 `FAILED`；恢复 `collect_upload_event_dir_names`（仅 scan xls，不再 union signal）；新增 `upload_task`（标记 `UPLOAD_PENDING`）；`count_pending_upload_events` 按 `STP_EVENT_UPLOADER_CONTINUOUS` 区分
- `6da21d0`：`EventState.UPLOAD_PENDING` 入枚举；HddSpill `force=True` 绕过过滤模型
- `a085656`：`upload_task` 注册进 `SAQ_FUNCTIONS`（漏注册导致 worker 不认识函数）
- `1fb8e2a`：Agent `_recover_pending` 一次性 → 30s 周期轮询；recover/retry 入队 `force=True`（修复 Plan A 下 self-gate）

**fleet 配置**（20 台 Agent，代码版本 `1fb8e2a`；#287 后两键合并为单一开关且代码默认开）：
- `STP_DEVICE_LOG_EVENT_ENABLED=1`（DLE 注册 + EventUploader 运行）
- `STP_EVENT_UPLOADER_PRUNE_LOCAL=1`（仅灰机 `192-0-2-143`，fleet 未开）

**灰机验证**（PlanRun #209，Plan 7 / device 19 / host 8.143）：
- 10 条 DLE：`LOCAL → UPLOAD_PENDING（upload_task 01:03:43）→ REMOTE → ARCHIVED(1)/PRUNED(8)/REMOTE(1)`
- CIFS：`devices/209/` 9 目录、`jira/209/` 9 项、`dedup/209/` 2 文件
- 灰机 HDD 从 55GB 降至 49GB（净回收）

## 相关

- 审查报告：[`DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](../reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md)（v3.0）
- 方案 A 讨论记录：本 ADR 2026-08-12 修订；生产实施 2026-08-13
- ADR-0025（方案 C 存储与访问）
- ADR-0026（Plan 执行扩容）
- Issue #73（展锐异常采集）
- Issue #213（旧上送双轨删除——upload_task 已恢复）
- Issue #217（PRUNE_LOCAL 灰度）
