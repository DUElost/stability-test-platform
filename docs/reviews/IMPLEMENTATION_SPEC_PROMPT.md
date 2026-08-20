# 阶段 3 重构实现规格 —— Agent 工作提示词

> 将此提示词发给新的 Claude Code Agent，产出 `docs/design/2026-device-log-event-implementation-spec.md`。
> Agent 完成初稿后，人工审核并解决其标注的待决策项，然后归档为阶段 3 开实现的唯一规格文档。

---

## 任务

编写 **DeviceLogEvent 重构实现规格文档**（阶段 3，对应 ADR-0028 D1~D8），使另一个 Agent 能**不依赖你来问问题**直接开工写代码。

## 前置阅读（Agent 必须先完整阅读）

1. `docs/reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md` — v3.0 全文，重点：
   - §五（三个根因）
   - §六（重构方向与架构对比）
   - §八（11 条 DoD 验收用例）
2. `docs/adr/ADR-0028-device-log-event-and-continuous-upload.md` — 8 个架构决策
3. `backend/agent/aee/paths.py` — 当前路径解析
4. `backend/agent/aee/reconciler.py` — 当前 Reconciler
5. `backend/agent/upload_manager.py` — 当前上传
6. `backend/agent/local_disk_monitor.py` — 当前 HddSpill
7. `backend/services/dedup_scan.py` — 当前 merge
8. `backend/services/dedup_extract.py` — 当前 extract
9. `backend/tasks/saq_tasks.py` — 当前 scan/upload/merge SAQ 链
10. `AGENTS.md` / `CLAUDE.md` — 项目约定

## 输出要求

产出文件：`docs/design/2026-device-log-event-implementation-spec.md`

篇幅：300~500 行。不重复 ADR-0028 的决策理由（那是 ADR 的事），只写**实现细节和边界条件**。

## 必须覆盖的 6 个专题

### 专题 1：`device_log_event` 表完整 schema

- 每个字段的 PostgreSQL 类型、nullable、default
- 索引策略（列出每一条索引及其覆盖的查询场景）
- 外键约束（`host_id` → `host`、`plan_run_id` → `plan_run`、`job_id` → `job_instance`——各自的 ON DELETE 行为）
- `job_log_signal` 表新增 `device_log_event_id` 外键（`SET NULL` on delete）
- `job_log_signal.job_id` 的 `ondelete` 从 `CASCADE` 改为 `SET NULL`（migration 如何处理已有数据）
- `PlanRunArtifact` 新增 `scan_round_id`（类型、索引）
- ORM model 定义（Pydantic v2 `ConfigDict(from_attributes=True)` + 表名单数约定 + `__tablename__ = "device_log_event"`）
- 状态枚举 `EventState` 定义（值与含义）

### 专题 2：连续上送 `EventUploader` 设计

- 线程模型：Agent 进程内几个线程？queue 模型（per-process 单队列还是 per-event）？
- 并发上限：同时最多几个 copytree？（CIFS 并发写入保护）
- 上传流程：`LOCAL` → acquire slot → copytree → checksum 校验 → `UPDATE state=REMOTE, remote_path=...`
- 失败处理：重试次数、退避策略、`UPLOAD_FAILED` 状态 → 多久后重试
- Agent 重启恢复：启动时扫描 `state IN (LOCAL, UPLOADING, UPLOAD_FAILED)` → 重新入队
- Feature flag：env 变量名、默认值、回滚切换方式
- 新旧上送路径并存策略：
  - 并存期间如何避免同一事件被上传两次？
  - 确认旧路径已删：Agent 无 `upload_events` / `upload_event_dirs`；
    `collect_upload_event_dir_names` 仅被 upload_task（scan xls 过滤）使用

### 专题 3：HddSpill 改造

- 当前行为 vs 目标行为对照表
- 如何判定「可溢出事件」：`state=LOCAL` + `detected_at` 排序（替代 mtime 扫目录）
- spill 与正常上传的优先级关系
- spill 走同一上传通道的具体方式（直接调用 EventUploader 的方法？还是写入同一 queue？）
- SSD 模式下自动禁用 spill 的判定条件（与 `STP_AEE_SSD_FALLBACK_ROOT` 的关系）
- `_MAX_SPILL_PER_CYCLE` 在新模型下的语义

### 专题 4：scan/upload/merge/extract SAQ 链改造

- 每个 task 的「改前 → 改后」对照
- `scan_task`：不再触发 upload（只发 `scan_now` + poll `PlanRunArtifact`）
- `upload_task`：从「emit SocketIO 命令等 Agent 上送」改为「确认所有事件 state=REMOTE」
- `merge_task`：`_load_org_files_for_merge` 按 `scan_round_id` 或 `created_at >= round_started_at` 过滤
- `extract_task`：`collect_upload_event_dir_names` 废弃 → 查 `device_log_event` 表
- `_count_devices_event_dirs_sync`（NFS 扫目录）→ 改为 DB 查询
- 短期 P0-1 双根遍历（`devices/{plan_run_id}/` + `devices/{folder}/{serial}/`）在 extract 中的落点——标注「阶段 1 临时补丁，阶段 3 由 DeviceLogEvent.remote_path 替代后删除」

### 专题 5：`PlatformCollector` 接口精确签名

- 完整的方法签名（参数类型、返回值类型、异常约定）
- `TriggerInfo` / `EventMetadata` 等辅助类型的定义
- 错误处理约定：Collector 方法抛异常时 Reconciler 的行为
- MTK 实现从现有 Reconciler 抽离的详细步骤（哪些代码搬、哪些留）
- UNISOC / QCOM 的存根实现（仅 `detect`，其余方法 `raise NotImplementedError`）
- 与 `STP_WATCHER_AEE_RECONCILE_PLATFORMS` 配置的关系

### 专题 6：数据迁移与兼容性

- `device_log_event` 表初始化为空——不回溯填充历史数据（标注决策理由）
- `job_log_signal.job_id` migration：`ALTER COLUMN DROP NOT NULL` + `DROP CASCADE, ADD SET NULL`
- 已有的 HDD 事件目录：不录入 `device_log_event`（标注理由：无可靠方式重建事件元数据）
- 已有的 CIFS 上 `devices/{plan_run_id}/` 目录：保持原样，不被新路径影响
- 已有的 CIFS 上 `devices/{folder}/{serial}/`（HddSpill 溢出）：标注为「遗留数据，手动清理或等保留策略自然淘汰」
- DB migration 版本号约定（按项目 Alembic 命名规则）

## 写作风格

- 每个专题开头用一句话结论，然后展开
- 代码示例用 Python 伪代码或关键字段声明（不是完整实现）
- 模块路径写完整（`backend/agent/...`、`backend/services/...`）
- 不重复 ADR-0028 的内容，只写 ADR 没覆盖的实现细节
- 遇到无法从现有代码推定的决策点，写 `[待决策]` 标记并列出选项——不要编造

## 产出后的人工审核清单

Agent 完成后，审核以下项：

- [ ] 6 个专题全部覆盖，无一遗漏
- [ ] `[待决策]` 标记不超过 3 处，且每处都已给出选项
- [ ] 索引策略与查询场景一一对应
- [ ] 新旧路径并存策略有明确的 feature flag + 删除时间点
- [ ] migration 步骤可执行（Alembic upgrade 不报错）
- [ ] DoD 验收用例（v3.0 §八）在本规格中都有对应的实现路径
