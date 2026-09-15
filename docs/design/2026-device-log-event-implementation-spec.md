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
    UPLOAD_PENDING = "UPLOAD_PENDING"
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

**结论**：EventUploader 是 Agent 侧唯一 copytree 执行者（单队列 + 2 slot + 重试/checksum/PRUNE）。
**过滤模型是唯一路径**：只拉取 `upload_task` 标记的 `UPLOAD_PENDING`（初版 `CONTINUOUS=1`
逃生阀已随 #287 整体删除，全仓无 `STP_EVENT_UPLOADER_CONTINUOUS`；非 force 入队一律拒绝，
无 PlanRun 的纯采集场景由 `devices/unassigned/` 兜底承接）。

### 2.1 线程模型

- 1 个 `queue.Queue`（进程级单例）
- 1 个 dispatcher 线程：从 queue 取任务，受 `Semaphore(2)` 限制并发
- 2 个逻辑 slot = 同时最多 2 个 `shutil.copytree`（CIFS 写入保护）

### 2.2 上传流程

```
enqueue(event_id, force?)
  → 预检：本地目录缺失且远端无副本 → PULL_FAILED；远端已有同 event_id 副本且
    checksum 一致 → 直接补 REMOTE
  → acquire slot（#389：先拿 slot 再起 worker 线程，线程数被 2 封顶）
  → UPDATE state=UPLOADING (via control-plane API)
  → UploadManager._copytree_safe(local_path → remote_path)
  → 自读回 sha256 比对；不一致删坏副本并按重试计（#1083）
  → UPDATE state=REMOTE, remote_path=..., checksum=...
  → release slot；REMOTE ack 成功后才允许 PRUNE（#1083）
```

`remote_path` 布局：`{nfs_root}/devices/{plan_run_id}/{event_id}/{src.name}/`
（#1073 加 event_id 层，防同名事件互相覆盖）；`plan_run_id` 为空时用
`devices/unassigned/{event_id}/`。

### 2.3 失败与重试

- 最多 5 次重试；退避 `min(300, 2^attempt)` 秒（daemon `threading.Timer` 重入队）
- attempt 持久化到 agent_state `event_upload_attempts:{event_id}`（#785），重启不归零
- 耗尽 → `UPLOAD_FAILED`；由 600s 慢循环重扫 `UPLOAD_FAILED`/`UPLOADING`
  并按持久化 attempt 跳过已耗尽者（不按 `updated_at` 过滤——Agent 侧 GET 无该参数）

### 2.4 Agent 重启恢复

启动即周期轮询由 `_recover_pending` 执行（`_RECOVER_POLL_INTERVAL` 30s）：

- 只恢复 `UPLOAD_PENDING`（upload_task 已筛选的子集，#380）；
- `UPLOADING` / `UPLOAD_FAILED` 的中断残留交 600s 慢循环（`_retry_failed_loop`）——
  快速轮询若也拉这两个状态，会把在途/已达重试上限的事件反复以 attempt=0 重入队
  （重试上限失效、CIFS 上 rmtree-vs-copy 抖动）。

### 2.5 Feature flag

| 变量 | 默认 | 说明 |
|------|------|------|
| `STP_DEVICE_LOG_EVENT_ENABLED` | `1` | DLE 注册 + EventUploader 单一开关（#287 合并双 flag，默认开）；过滤模型是唯一路径（`CONTINUOUS` 已删除） |

### 2.6 模式与回滚

| 问题 | 策略 |
|------|------|
| 默认模式 | EventUploader 只拉 `upload_task` 标记的 `UPLOAD_PENDING`（过滤模型唯一路径，#287） |
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

常态保持 20：每轮 spill 周期最多 enqueue 20 个 `LOCAL` 事件，防止一次打满 CIFS。

**临界水位分支（#741，单轮上限不再恒为 20）**：`usage_pct ≥ STP_HDD_SPILL_CRITICAL_PCT`
（默认 98.0）时，`_spill_budget()` 返回 `max(_MAX_SPILL_PER_CYCLE, STP_HDD_SPILL_CRITICAL_BATCH)`
= **100**（默认）——磁盘濒满时优先腾退，代价是单轮 CIFS 压力放大。预算每次 spill 时按
**当时的** usage 重算，回落到临界水位以下即回到 20。高水位未回落时另有
`STP_HDD_SPILL_CATCHUP_INTERVAL`（默认 30s）控制追打间隔（#1522）。

### 3.4 SSD 禁用条件

`paths.is_ssd_fallback_root(local_root)` 为真，或 env `STP_AEE_SSD_FALLBACK_ROOT` 与实际 root
相同 → 线程照常启动，但每次 `check_once()` 开头早退（`local_disk_monitor.py` `_ssd_spill_disabled`
分支），行为等效于禁用 spill。

### 3.5 阈值默认的双口径（注记）

类体默认（interval 300s / threshold 95% / target 70%）仅在未 configure 时生效；生产 wiring 由
`main.py` 以 env 缺省注入 `STP_LOCAL_DISK_SPILL_THRESHOLD=80` / `STP_LOCAL_DISK_SPILL_TARGET=70` /
`STP_LOCAL_DISK_MONITOR_INTERVAL_SECONDS=300`——**有效默认以 main.py 注入为准**，两处数值勿混用。

---

## 专题 4：scan/upload/merge/extract SAQ 链

**结论**：scan 只产 xls；`upload_task` 按 scan xls 标记 `UPLOAD_PENDING`（EventUploader 拉取执行）；merge 按 `scan_round_id` 过滤；extract 查 DB。

### 4.1 各 task 对照

| Task | 职责（现状） |
|------|--------------|
| `scan_task` | 发 scan + poll 产物；写 `scan_round_id`；enqueue upload_task 后 enqueue merge |
| `upload_task` | 按 scan xls 引用标记 `LOCAL → UPLOAD_PENDING`；EventUploader 30s 轮询执行 copytree（Agent 侧唯一执行者） |
| `merge_task` | 仅合并 `scan_round_id = 本轮` 或 `created_at >= round_started_at` 的产物 |
| `extract_task` | `SELECT remote_path FROM device_log_event WHERE plan_run_id=? AND state IN (...)`；拷贝到 `jira/{run_id}/` |

### 4.2 `_count_devices_event_dirs_sync`

改为：

```sql
SELECT COUNT(DISTINCT host_id)
FROM device_log_event
WHERE plan_run_id = :run_id AND state IN ('REMOTE', 'ARCHIVED')
  AND host_id = ANY(:triggered) AND updated_at >= :since
```

### 4.3 P0-1 双根遍历（临时）

`dedup_extract.run_extract_sync` 单根定位（`STP_AEE_NFS_ROOT_LEGACY` 双源与 `resolve_legacy_shared_storage_root` 已删除，#289），由 `remote_path` 唯一确定。

---

## 专题 5：`PlatformCollector` 接口

**结论**：协议在 `backend/agent/aee/collector.py`（ADR-0028 D4）；MTK 与 UNISOC 均有真实
Collector（`collectors/mtk.py`、`collectors/unisoc.py`，ADR-0032 D6/B5），QCOM 仍为
stub-only；Collector 路由由 `get_collector_for_platform` 按 `device.platform` 完成
（ADR-0032），不再由 env 白名单决定。

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

@runtime_checkable
class PlatformCollector(Protocol):
    platform: str

    def detect(
        self, shell_fn: Callable[[str, int], Optional[str]], serial: str,
    ) -> bool: ...
    def parse_metadata(self, event_dir: Path) -> EventMetadata: ...
```

`TriggerInfo` 定义后**全仓无使用点**（实际拉取判定走 processor 的 pending/processed
dict，见 `backend/agent/aee/processor.py`），属预留接口；`parse_metadata` 失败
raise `CollectorError`。设备侧事件拉取由 reconciler 直接驱动（平台归属在
JobSession 组装时经 `get_collector_for_platform` 一次性确定），成功后
`POST device-log-events` `state=LOCAL`。

### 5.2 Reconciler 错误约定

- `CollectorError`：记日志 + `tick_errors++`，不 crash 线程
- 协议中的 `detect()` **当前零调用点**（平台判定实际走 `detect_device_platform` +
  `get_collector_for_platform`，`job_session.py`）；保留接口但勿据本文推演运行时行为

### 5.3 平台路由（ADR-0032）

`get_collector_for_platform(platform)` 按 `device.platform`（大写归一）路由：

| platform | Collector |
|---|---|
| `MTK` | `MtkPlatformCollector`（真实） |
| `UNISOC` | `UnisocPlatformCollector`（真实，ADR-0032 B5） |
| `QCOM` | `QcomPlatformCollector`（stub-only） |
| `UNKNOWN` / 未识别 | `MtkPlatformCollector`（兜底） |

历史单白名单 env 键 `STP_WATCHER_AEE_RECONCILE_PLATFORMS` **已随路由删除**
（全代码零读取点），不再作为 Collector 启动依据。

### 5.4 平台可用性三层口径（#220 → ADR-0032 supersede）

| 平台 | 模块存在 | 端到端采集可用 | 真机验收完成 |
|---|---|---|---|
| MTK | ✓ | ✓ | ✓（主线） |
| UNISOC | ✓（collector + reconciler，ADR-0032） | 见 R09 台账 #1055 相关项 | 待补 |
| QCOM | stub（`detect` False、`parse_metadata` raise） | —（#73 延期，不阻塞主线） | — |

勿以「模块存在」代替「端到端可用」表述——三者按上表分别陈述。

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

---

## 可信边界与状态迁移（#1052 / R09-R02）

### 信任模型

DLE 摄入端点（`POST /agent/device-log-events`）的授权只有**共享 Agent 密钥**
（`_verify_agent`，fleet 级）+ `host/job/plan_run/serial` 的一致性校验；
**没有** log_signal 路径的 `_require_job_bound_upload_lease` 级租约绑定——
Agent 是「半可信」的（持有共享密钥的进程可按任意 host 身份提交，校验只能发现
组合不一致，不能证明占有设备）。

演进方向（R02/R03 未定项）：租约/设备占有证明、未绑定事件的独立授权通道；
在此之前：

- **身份字段不可变**：`serial` / `job_id` / `plan_run_id` 一经入库不得更改
  （不一致 → 400/403）——防跨设备/跨任务混淆与错误 Agent 改写归属；
- **三元组一致**：插入与更新都要求 `host_id == job.host_id`、
  `plan_run_id == job.plan_run_id`（二者都非空时）；
- **状态迁移显式**：`_ALLOWED_TRANSITIONS` 矩阵（agent_api.py）——同态幂等；
  表外迁移 409 `DLE_INVALID_TRANSITION`；extractable 三态（REMOTE/ARCHIVED/
  PRUNED）的降级补丁按 #1174 幂等成功忽略（保 Agent outbox ACK）；
- **合法迟到补报**：不带 `plan_run_id` 的 patch **保留**既有归属（不清空）；
  PULL_FAILED 重试直达 REMOTE、UPLOAD_PENDING（控制面 scan 标记，直写 SQL，
  属控制面信任域）等路径均在矩阵内。

### 未覆盖（有意）

- 共享密钥泄漏的横向移动：超出本层（密钥轮换/每主机密钥另议）；
- 控制面自身直写 DLE 状态（saq_tasks 标记 UPLOAD_PENDING）：中央信任域，
  不经本端点；
- 事件内容/路径的可信度：`remote_path` 有 plan_run 前缀与 UUID 规整
  （`_validated_remote_path`），不做内容鉴定。
