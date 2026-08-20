# DeviceLogEvent 重构实现规格（阶段 3）

> **最后更新**：2026-08-20  
> **决策依据**：[`ADR-0028`](../adr/ADR-0028-device-log-event-and-continuous-upload.md)（D1–D8）  
> **背景分析**：[`DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](../reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md)（v3.0）  
> **方案 A 修订**：以 ADR-0028 修订版为准（upload_task 筛选 + EventUploader 执行）；初版 D2「连续全量上送」已废弃。  
> **范围**：实现细节与边界条件；不重复 ADR 决策理由。

---

## 实现顺序

| 步骤 | 专题 | 交付物 | 依赖 |
|------|------|--------|------|
| 1 | 专题 1 | `device_log_event` 表 + migration + ORM + `EventState` | — |
| 2 | 专题 1 续 | `job_log_signal.device_log_event_id` + `job_id SET NULL` + `scan_round_id` | 步骤 1 |
| 3 | 专题 1 续 | Agent `POST /api/v1/agent/device-log-events`（upsert + state 转换） | 步骤 1 |
| 4 | 专题 5（MTK） | `PlatformCollector` 协议 + Reconciler 写入 `DeviceLogEvent` | 步骤 3 |
| 5 | 专题 2 | `EventUploader` 执行者 + 模式开关（`CONTINUOUS`） | 步骤 4 |
| 6 | 专题 3 | `HddSpillMonitor` 改查 DB | 步骤 5 |
| 7 | 专题 4 | SAQ scan/upload/merge/extract 链 | 步骤 5 |
| 8 | 专题 6 | 灰度验证 + 旧路径删除 | 步骤 7 |

---

## 专题 1：`device_log_event` 表完整 schema

**结论**：PostgreSQL 权威表 + 控制面 ORM；Agent 通过 REST 写入，不在 Agent SQLite 复制全表。

### 1.1 表 `device_log_event`

| 列 | PostgreSQL 类型 | Nullable | Default | 说明 |
|----|-----------------|----------|---------|------|
| `id` | `UUID` | NOT NULL | `gen_random_uuid()` | 主键 |
| `serial` | `VARCHAR(128)` | NOT NULL | — | 设备序列号 |
| `platform` | `VARCHAR(16)` | NOT NULL | — | `MTK` / `UNISOC` / `QCOM` / `UNKNOWN` |
| `event_type` | `VARCHAR(32)` | NOT NULL | — | KE / NE / JE / ANR / HWT / SWT 等 |
| `event_subtype` | `VARCHAR(128)` | NULL | — | ZZ_INTERNAL 解析 |
| `detected_at` | `TIMESTAMPTZ` | NOT NULL | — | Reconciler 发现时间（控制面时钟） |
| `device_timestamp` | `TIMESTAMPTZ` | NULL | — | 设备侧时间戳 |
| `state` | `VARCHAR(32)` | NOT NULL | `'DETECTED'` | 见 `EventState` |
| `local_path` | `VARCHAR(1024)` | NOT NULL | — | HDD/SSD 绝对路径 |
| `remote_path` | `VARCHAR(1024)` | NULL | — | 上送完成后 CIFS 路径 |
| `size_bytes` | `BIGINT` | NULL | — | 目录总大小 |
| `checksum` | `VARCHAR(64)` | NULL | — | 上送后 sha256 |
| `plan_run_id` | `INTEGER` | NULL | — | 松散关联 |
| `host_id` | `VARCHAR(64)` | NOT NULL | — | 采集 Agent host |
| `job_id` | `INTEGER` | NULL | — | 采集 Job（可空） |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | 行创建 |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | 最后状态变更 |

### 1.2 索引

| 索引名 | 列 | 查询场景 |
|--------|-----|----------|
| `idx_device_log_event_plan_state` | `(plan_run_id, state)` | extract：`WHERE plan_run_id=? AND state IN ('REMOTE','ARCHIVED')` |
| `idx_device_log_event_host_state_detected` | `(host_id, state, detected_at)` | EventUploader 恢复、HddSpill 取最旧 `LOCAL` |
| `idx_device_log_event_serial_detected` | `(serial, detected_at DESC)` | 按设备查事件、事后关联 PlanRun |
| `idx_device_log_event_state_updated` | `(state, updated_at)` | `UPLOAD_FAILED` 重试扫描 |

### 1.3 外键 ON DELETE

| FK | 引用 | ON DELETE |
|----|------|-----------|
| `host_id` | `host.id` | `CASCADE`（删 host 删其事件） |
| `plan_run_id` | `plan_run.id` | `SET NULL`（PlanRun 归档不删事件） |
| `job_id` | `job_instance.id` | `SET NULL`（删 job 保留事件） |

### 1.4 `job_log_signal` 变更

- 新增 `device_log_event_id UUID NULL` → `device_log_event.id`，`ON DELETE SET NULL`
- `job_id`：`NOT NULL` → `NULL` 允许；FK `ondelete` 从 `CASCADE` → `SET NULL`

**Migration 处理已有数据**：现有行 `job_id` 均非空，改 nullable 无数据损失；先 `DROP CONSTRAINT` 再 `ALTER COLUMN DROP NOT NULL` 再 `ADD CONSTRAINT ... ON DELETE SET NULL`。

### 1.5 `plan_run_artifact.scan_round_id`

| 列 | 类型 | Nullable | 说明 |
|----|------|----------|------|
| `scan_round_id` | `VARCHAR(64)` | NULL | `scan_task` 写入 `round_started_at.isoformat()` |

索引：`idx_plan_run_artifact_run_round (plan_run_id, scan_round_id)` — merge 按轮次过滤。

### 1.6 ORM

- 文件：`backend/models/device_log_event.py`
- `__tablename__ = "device_log_event"`
- `EventState` 枚举：`backend/models/enums.py`

```python
class EventState(str, Enum):
    DETECTED = "DETECTED"
    PULL_FAILED = "PULL_FAILED"
    LOCAL = "LOCAL"
    UPLOADING = "UPLOADING"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    REMOTE = "REMOTE"
    ARCHIVED = "ARCHIVED"
    PRUNED = "PRUNED"
```

### 1.7 Migration

- Revision：`p3q4r5s6t7u8`
- Revises：`o2p3q4r5s6t7`

---

## 专题 2：EventUploader 执行者与模式开关

**结论**：EventUploader 是 Agent 侧唯一 copytree 执行者（单队列 + 2 slot + 重试/checksum/PRUNE）。默认 `CONTINUOUS=0`：只拉取 `upload_task` 标记的 `UPLOAD_PENDING`（过滤模型）；`CONTINUOUS=1` 是逃生阀：`LOCAL` 入队后立即全量上送，不等待 PlanRun 终态。

### 2.1 线程模型

- 1 个 `queue.Queue`（进程级单例）
- 1 个 dispatcher 线程：从 queue 取任务，受 `Semaphore(2)` 限制并发
- 2 个逻辑 slot = 同时最多 2 个 `shutil.copytree`（CIFS 写入保护）

### 2.2 上传流程

```
enqueue(event_id)
  → UPDATE state=UPLOADING (via control-plane API)
  → acquire slot
  → copytree(local_path → remote_path)
  → sha256 校验
  → UPDATE state=REMOTE, remote_path=..., checksum=...
  → release slot
```

`remote_path` 布局：`{nfs_root}/devices/{plan_run_id}/{basename(local_path)}/`（与现有 UploadManager 一致）；`plan_run_id` 为空时用 `devices/unassigned/{event_id}/`。

### 2.3 失败与重试

- 最多 5 次重试；退避 `min(300, 2^attempt)` 秒
- 耗尽 → `UPLOAD_FAILED`；每 10 分钟扫描 `UPLOAD_FAILED` 且 `updated_at < now()-600s` 重新入队

### 2.4 Agent 重启恢复

启动即周期轮询由 `_recover_pending` 执行（`_RECOVER_POLL_INTERVAL` 30s）：

- `CONTINUOUS=0`（默认）：只恢复 `UPLOAD_PENDING`（upload_task 已筛选的子集），外加 `UPLOADING` / `UPLOAD_FAILED` 的中断残留；
- `CONTINUOUS=1`：恢复 `LOCAL`（立即全量上送）以及 `UPLOADING` / `UPLOAD_FAILED`。

`UPLOAD_FAILED` 按 2.3 的退避与 10 分钟重扫规则处理。

### 2.5 Feature flag

| 变量 | 默认 | 说明 |
|------|------|------|
| `STP_EVENT_UPLOADER_ENABLED` | `1` | EventUploader 运行（Agent 侧执行者） |
| `STP_EVENT_UPLOADER_CONTINUOUS` | `0` | `0` 过滤模型（仅拉 `UPLOAD_PENDING`）；`1` 逃生阀（全量上送） |

### 2.6 模式与回滚

| 问题 | 策略 |
|------|------|
| 默认模式 | `CONTINUOUS=0`：EventUploader 只拉 `upload_task` 标记的 `UPLOAD_PENDING` |
| 逃生阀 | `CONTINUOUS=1`：`LOCAL` 全量入队（无 PlanRun 纯采集等场景） |
| 回滚 | 改 env + `reload_config`，无需重启 |

---

## 专题 3：HddSpill 改造

**结论**：不再 `iterdir()` 按 mtime；改查 DB `state=LOCAL ORDER BY detected_at ASC`，走 EventUploader 同一 queue。

### 3.1 行为对照

| | 当前 | 目标 |
|---|------|------|
| 候选发现 | HDD 目录 mtime 排序 | DB `state=LOCAL` + `detected_at` ASC |
| 上送 | 本地 `copytree` 到 spill 路径 | `EventUploader.enqueue(event_id)` |
| 路径 | `devices/{folder}/{serial}/` | 统一 `devices/{plan_run_id}/`（由 remote_path 决定） |
| SSD 模式 | 仍可能 spill | `get_aee_local_root()` 判定 SSD → 禁用 spill |

### 3.2 优先级

正常上送（新事件 `LOCAL`）与 spill 共用 queue；queue FIFO，无抢占。spill 仅在 `usage ≥ threshold` 时批量 enqueue 最旧 N 条。

### 3.3 `_MAX_SPILL_PER_CYCLE`

保持 20：每轮 spill 周期最多 enqueue 20 个 `LOCAL` 事件，防止一次打满 CIFS。

### 3.4 SSD 禁用条件

`paths.is_ssd_fallback_root(local_root)` 为真，或 env `STP_AEE_SSD_FALLBACK_ROOT` 与实际 root 相同 → `HddSpillMonitor.start()` 跳过。

---

## 专题 4：scan/upload/merge/extract SAQ 链

**结论**：scan 只产 xls；`upload_task` 按 scan xls 标记 `UPLOAD_PENDING`（EventUploader 拉取执行）；merge 按 `scan_round_id` 过滤；extract 查 DB。

### 4.1 各 task 对照

| Task | 改前 | 改后 |
|------|------|------|
| `scan_task` | 发 scan + poll artifact + enqueue upload | 只发 scan + poll；写 `scan_round_id`；enqueue upload_task 后 enqueue merge |
| `upload_task` | emit `upload_events` 等 Agent | 标记 scan 引用事件 `LOCAL → UPLOAD_PENDING`；EventUploader 30s 拉取执行 copytree |
| `merge_task` | `_load_org_files_for_merge` 全量 | 仅 `scan_round_id = 本轮` 或 `created_at >= round_started_at` |
| `extract_task` | `collect_upload_event_dir_names` | `SELECT remote_path FROM device_log_event WHERE plan_run_id=? AND state IN (...)` |

### 4.2 `_count_devices_event_dirs_sync`

改为：

```sql
SELECT COUNT(DISTINCT host_id)
FROM device_log_event
WHERE plan_run_id = :run_id AND state IN ('REMOTE', 'ARCHIVED')
  AND host_id = ANY(:triggered) AND updated_at >= :since
```

### 4.3 P0-1 双根遍历（临时）

`dedup_extract.run_extract_sync` 在 `STP_AEE_NFS_ROOT_LEGACY` 存在时双根 — **阶段 3 完成后删除**，由 `remote_path` 唯一确定。

---

## 专题 5：`PlatformCollector` 接口

**结论**：协议在 `backend/agent/aee/collector.py`；MTK 从 `reconciler.py` + `processor.py` 抽离；UNISOC/QCOM 仅 `detect`。

### 5.1 类型与签名

```python
@dataclass(frozen=True)
class TriggerInfo:
    aee_type: str          # "aee_exp" | "vendor_aee_exp"
    entry_line: str        # db_history 原始行
    device_path: str       # 设备侧路径

@dataclass
class EventMetadata:
    event_type: str
    event_subtype: str | None
    package_name: str | None
    device_timestamp: datetime | None

class PlatformCollector(Protocol):
    platform: str

    def detect(self, adb_run: Callable[..., str], serial: str) -> bool: ...
    def poll_new_events(
        self, adb_run, serial: str, *, processed: set[str]
    ) -> list[TriggerInfo]: ...
    def collect(
        self, adb_run, serial: str, trigger: TriggerInfo, output_dir: Path
    ) -> Path: ...  # 返回 local_path；失败 raise CollectorError
    def parse_metadata(self, event_dir: Path) -> EventMetadata: ...
```

### 5.2 Reconciler 错误约定

- `CollectorError`：记日志 + `tick_errors++`，不 crash 线程
- `detect=False`：跳过该设备本轮
- `collect` 成功 → `POST device-log-events` `state=LOCAL`

### 5.3 平台注册

`STP_WATCHER_AEE_RECONCILE_PLATFORMS`（默认 `MTK`）决定启动哪个 Collector；`UNKNOWN` 仍放行 MTK Collector。

### 5.4 存根（#220）

`UnisocCollector` / `QcomCollector`：`detect` 返回 False；`parse_metadata` 抛
`CollectorError`。生产白名单默认仅 `MTK`——**勿**把 `STP_WATCHER_AEE_RECONCILE_PLATFORMS`
扩成含 UNISOC/QCOM 冒充扫描。真采集仍见 #73（延期，不阻塞主线）。

---

## 专题 6：数据迁移与兼容性

**结论**：新表空启动；历史 HDD/CIFS 数据不回填；旧 CIFS 目录保持只读遗留。

| 项 | 决策 |
|----|------|
| 历史事件不入库 | 无可靠元数据重建 `detected_at` / `event_type` |
| `job_log_signal` CASCADE→SET NULL | migration 见 1.4；下游查询 `job_id IS NOT NULL` 保持兼容 |
| CIFS `devices/{plan_run_id}/` | 不变；新事件同布局 |
| CIFS spill `devices/{folder}/{serial}/` | 遗留；手动清理或保留策略淘汰 |
| 不回溯填充 | 降低 migration 风险；新事件从新链路开始 |

---

## DoD 映射（审查 §八）

| 用例 | 实现路径 |
|------|----------|
| MTK 采集 | 专题 5 MTK Collector + 专题 1 API |
| L1 降级 | ADR D5 `get_aee_local_root` SSD（已部分落地）+ 专题 3 禁用 spill |
| L2 溢出 | 专题 3 + 专题 2 上送 |
| 过滤上送 | 专题 2；`UPLOAD_PENDING` 后 5min 内 `REMOTE` |
| PlanRun 汇总 | 专题 4 scan/merge/extract |
| 部分 host 失败 | 专题 4 `run_context.archive`（阶段 2 可观测） |
| 增量 scan | 专题 1 `scan_round_id` + 专题 4 merge 过滤 |
| factory side | 不变（`STP_DEDUP_SCAN_TAG`） |
| 存储切换 | ADR D8 `NFS_ROOT_LEGACY` |
| FAILED PlanRun | 专题 2 解耦上送 |
| 运行日志 | 不变（方案 C） |

---

## 模块路径索引

| 模块 | 路径 |
|------|------|
| ORM | `backend/models/device_log_event.py` |
| Migration | `backend/alembic/versions/p3q4r5s6t7u8_add_device_log_event.py` |
| Agent API | `backend/api/routes/agent_device_log_events.py` |
| EventUploader | `backend/agent/event_uploader.py` |
| PlatformCollector | `backend/agent/aee/collector.py` |
| MTK 实现 | `backend/agent/aee/collectors/mtk.py` |
| HddSpill | `backend/agent/local_disk_monitor.py` |
| SAQ | `backend/tasks/saq_tasks.py` |
| Extract | `backend/services/dedup_extract.py` |
| Merge 过滤 | `backend/services/dedup_scan.py` |
