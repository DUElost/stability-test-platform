# ADR-0028：设备日志事件实体 + 连续上送 + 存储路径收敛

- 状态：Accepted
- 优先级：P1（阶段 1 止血已部分落地；阶段 3 重构全面实施）
- 目标里程碑：阶段 3 重构完成
- 日期：2026-08-09
- 决策者：平台研发组
- 标签：设备日志, DeviceLogEvent, 连续上送, PlanRun 解耦, PlatformCollector, 存储收敛
- 背景分析：[`DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](../reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md)（v3.0，§五根因 + §六重构方向）

## 背景

ADR-0025（方案 C 存储）建立了三层存储漏斗（Agent HDD → CIFS → JIRA bundle），但设备日志在系统内**没有统一实体**——一个设备崩溃经过五个阶段（Reconciler pull → HDD 目录 → job_log_signal 行 → scan xls Path 列 → event_dir_names 字符串列表），每个阶段用不同方式表示同一事件。由此衍生三个根因级别问题：

1. **无事件实体**：无法回答「事件 X 在哪里、上送了吗、进哪个 PlanRun 了」
2. **上送绑定 PlanRun 终态**：设备上午崩溃，日志等到 PlanRun 跑完（可能下午）才上送；PlanRun FAILED/ABORTED → 永久不上送
3. **文件系统当数据库**：状态判定靠 `dst_dir.exists()`、目录遍历 `find_event_dir_under_root`、解析 xls Path 列文本

三个根因在 20 host 规模下勉强运维，60 host × 1000 device × 三平台下会系统性失效。详细证据见审查报告 §五（23 个 Findings，P0-1~P4-4）。

## 决策

### D1：引入 `DeviceLogEvent` 实体 + 生命周期状态机

新建 `device_log_event` 表作为设备日志事件的**唯一权威记录**：

| 字段 | 说明 |
|------|------|
| `id`（UUID） | 全局唯一标识 |
| `serial` | 设备序列号 |
| `platform` | MTK / UNISOC / QCOM |
| `event_type` / `event_subtype` | KE / NE / JE / ANR / HWT / SWT（从 ZZ_INTERNAL 或平台等价格式解析） |
| `detected_at` | Reconciler 发现时间（控制面时钟，非设备时钟——ADR-0025 已知坑） |
| `device_timestamp` | 设备侧时间戳（可空） |
| `state` | `DETECTED → LOCAL → UPLOADING → REMOTE → ARCHIVED → PRUNED`（状态机，非文件系统探测） |
| `local_path` | HDD/SSD 路径 |
| `remote_path` | CIFS 路径（上送完成后设置，可空） |
| `size_bytes` / `checksum` | 上送校验 |
| `plan_run_id` | 关联 PlanRun（可空，可事后按 `detected_at` + `serial` 关联） |
| `host_id` / `job_id` | 采集此事件的 Agent host / Job（可空） |

状态转换：

```
DETECTED ──(adb pull 完成)──→ LOCAL
  │                             │
  └── pull 失败 → PULL_FAILED   ├──(开始上传)──→ UPLOADING ──(copytree + checksum OK)──→ REMOTE
                                │                    │
                                └──(磁盘压力大)       └── 失败 → UPLOAD_FAILED（可重试）
                                    HddSpill 走
                                    同一上传通道

REMOTE ──(被 PlanRun extract 引用)──→ ARCHIVED
LOCAL / REMOTE / ARCHIVED ──(本地 prune)──→ PRUNED
```

与现有表的关联：
- `job_log_signal` 表新增 `device_log_event_id` 外键（可空，`SET NULL` on delete——替代当前 `CASCADE`）
- `JobLogSignal.job_id` 的 `ondelete` 从 `CASCADE` 改为 `SET NULL`（删 job 不丢事件记录）

### D2：设备日志上送与 PlanRun 生命周期解耦（连续上送）

**当前**：上送仅在 PlanRun 终态（SUCCESS/PARTIAL_SUCCESS）触发，`should_trigger_dedup` 门控 → `scan_task → upload_task`。

**改为**：事件在 `LOCAL` 状态后**立即入队上送**（Agent 侧后台线程 `EventUploader`），不等待 PlanRun 终态。

- PlanRun 终态时的 scan/merge/extract 链路不变，但此时事件已全部在 CIFS 上（`state=REMOTE`）
- PlanRun FAILED/ABORTED → 不影响事件上送（事件已安全到达 CIFS）
- scan_task 职责收窄为**只产 xls**，不再负责触发事件上送
- `collect_upload_event_dir_names`（`dedup_extract.py` 的 union 文件名逻辑）废弃，改为查 DB `SELECT ... WHERE plan_run_id = ... AND state IN (REMOTE, ARCHIVED)`

### D3：文件系统降级为纯存储后端

所有状态查询走 DB，文件系统只做读写：

| 原来 | 改为 |
|------|------|
| `dst_dir.exists()` 判断是否已上送 | `SELECT state FROM device_log_event WHERE id = ...` |
| `find_event_dir_under_root` 遍历目录树 | `SELECT local_path FROM device_log_event WHERE state = LOCAL AND host_id = ...` |
| 解析 scan xls Path 列文本获取事件名 | 按 `plan_run_id` + `detected_at` 时间范围查 DB |
| `count_hosts_with_scan_artifacts` 数 NFS 文件 | 按 `device_log_event` 行数统计（host 去重 + triggered 集合 + since 水位线逻辑迁移到 SQL） |

### D4：`PlatformCollector` 协议

平台差异隔离在 Collector 实现内，Reconciler 只做调度：

```python
class PlatformCollector(Protocol):
    platform: str  # "MTK" | "UNISOC" | "QCOM"

    async def detect(self, adb, serial: str) -> bool: ...
    async def poll_new_events(self, adb, serial: str, last_seen: str) -> list[TriggerInfo]: ...
    async def collect(self, adb, serial: str, trigger: TriggerInfo, output_dir: Path) -> DeviceLogEvent: ...
    def parse_metadata(self, event_dir: Path) -> EventMetadata: ...
```

- MTK 实现从现有 Reconciler 逻辑抽离（`db_history` sha256 对比 + `ZZ_INTERNAL` 解析）
- UNISOC / QCOM 各一个 issue + 验收用例
- 目录布局和上送路径**不感知平台差异**

### D5：L1 存储降级（无 HDD → SSD）

`get_aee_local_root()` 在现有 fallback 链最后一环前插入 SSD probe：

```
STP_AEE_LOCAL_ROOT → (os.access(W_OK) + /proc/mounts 非 tmpfs?)
  → STP_AEE_SSD_FALLBACK_ROOT（默认 {LOG_DIR}/aee_events）
  → STP_AEE_NFS_ROOT → … → /mnt/hdd/aee_events
```

- HddSpill 在 SSD 模式下自动禁用（spill 阈值仅对 HDD 有意义；SSD 满靠 LogArchiver grace pruning）
- 探测失败记 `aee_local_root_ssd_fallback`

### D6：统一中心存储根

`resolve_shared_storage_root()`（P2-5 已落地）：主键仅 `STP_AEE_NFS_ROOT`；`STP_AEE_CIFS_ROOT` 与 `STP_WATCHER_NFS_BASE_DIR` 降级为**弃用别名**（未设主键时的 fallback，不提供 spill 独立分盘语义）。

方案 C 文档（`2026-plan-c-storage-and-access.md` §2.3）需同步：删除「`STP_AEE_CIFS_ROOT` 仅 spill 可选」语义。

### D7：merge 轮次边界

`PlanRunArtifact` 新增 `scan_round_id`（`scan_task` 写入 `round_started_at` ISO）。

- `_load_org_files_for_merge` 仅取 `created_at >= round_started_at` 的 artifact
- 增量 scan 复用同一 `plan_run_id` 时不再混入历史轮次 org 文件

### D8：extract 存储切换双源读

存储切换窗口（8.202 → 15.4/9.4）内，extract 支持双根遍历：

- 新增 `STP_AEE_NFS_ROOT_LEGACY` 环境变量（可空）
- `run_extract_sync` 在 `LEGACY` 存在时同时从两个根收集事件目录
- 切换完成后移除 `LEGACY`

## 后果

### 正面

- **事件可追踪**：从采集到 JIRA bundle 全生命周期有唯一 ID 和状态
- **上送不再受 PlanRun 成败影响**：设备崩溃后数分钟内日志到达中心服务器
- **查询替代文件系统遍历**：O(1) DB 查询替代 O(n) 目录扫描
- **平台扩展有明确接口**：新增平台只需实现 `PlatformCollector`
- **存储切换有技术方案**：双源读 + SOP，不依赖「暂停 PlanRun」

### 负面

- 新增 `device_log_event` 表 + 状态机 → DB migration + Agent 侧 SQLite schema 变更
- 连续上送后台线程 → Agent 进程增加一个常驻线程 + CIFS 并发写入需控制
- 废弃路径需过渡期：`collect_upload_event_dir_names` 的文件名 union 逻辑不能立即删除（新旧上送路径并存期间）
- `JobLogSignal.job_id` 的 `CASCADE → SET NULL` 需 migration + 下游查询适配

### 不改变的部分

- Plan→PlanRun→Job→脚本执行→聚合核心链路不动
- SocketIO 通信层不动
- SAQ 任务链框架不动（scan/upload/merge/extract 仍走 SAQ，只是各 task 内部逻辑简化）
- 运行日志（`logs/runs/{job_id}/`）策略不变：SSD 唯一副本，不上送 CIFS

### 分阶段落地

| 阶段 | 内容 | 时间 |
|------|------|------|
| 1（止血） | P0-1 短期（extract 双根遍历 spill 路径）+ P0-3（merge `since` 过滤）+ P2-6（文档同步）+ P2-2a（handler 顺序） | 3–5 天 |
| 2（可观测） | API 暴露 `run_context.archive` + 前端 N/M host + PlanRun 零产物降级 | 1–2 天 |
| **3（重构）** | **D1–D8 全面实施**：DeviceLogEvent 表 + 连续上送 + PlatformCollector + 文件系统查询迁移 | ~3 周 |
| 4（平台+运维） | UNISOC/QCOM Collector + 存储切换 SOP + 清理保留策略 | 按排期 |

## 相关

- 审查报告：[`DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](../reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md)（v3.0）
- ADR-0025（方案 C 存储与访问）
- ADR-0026（Plan 执行扩容）
- Issue #73（展锐异常采集）
- `docs/design/2026-plan-c-storage-and-access.md`（需按 D6 同步）
