# 上线前兼容回退清理（#288 / #291 / #287 / #289）

- **日期**：2026-08-24
- **类型**：simplification
- **关联**：Epic #286；ADR-0026（§3 时钟纪律）；ADR-0028（方案 A）；#213 前任

## 决定了什么

四个「上线前删除回退」issue 在同一分支按序落地，每 issue 一个 commit
（+1 个事故恢复 commit）：

| Issue | 决定 |
|---|---|
| #288 | recycler 存活判定只认 execution 三信号；缺信号 = 未上报，锚定下发时刻 `COALESCE(started_at, created_at)`，一个完整窗口后进 UNKNOWN。extend-batch / coordinator heartbeat 对所有写入钉住 `updated_at`（防 Column.onupdate 刷新假活性钟） |
| #291 | merge 只走 `-merge_files_list`（探测失败=配置错误，RuntimeError）；LeaseRenewer 只走 batch（404/405 记 ERROR、状态保留、下 tick 重试，无逐 Job 回退；`_extend_lock` 方法删除）；前端删 `WS_DASHBOARD_ENDPOINT`/`WS_BASE_URL`/`VITE_WS_BASE_URL` 与 dev `/ws` 代理，改用 `DASHBOARD_SUBSCRIPTION` 描述符；`PlanRunStatus.DEGRADED` 从 enum/终态集合/通知/保留清理/广播全部摘除；`hidden_legacy_plan_ids` 隐藏守卫删除（`LEGACY_AEE_*_NAMES` 校验器保留） |
| #287 | 双 flag 合并为单一 `STP_DEVICE_LOG_EVENT_ENABLED` 且**默认开**（连接参数缺失仍自然 no-op）；`STP_EVENT_UPLOADER_ENABLED` / `STP_EVENT_UPLOADER_CONTINUOUS` 删除——过滤模型是唯一路径，HddSpill 保留 `force=True`；watcher/reconciler 恒被拒绝的非 force 入队死调用摘除 |
| #289 | 中心存储唯一主键 `STP_AEE_NFS_ROOT`（CIFS/WATCHER 别名回落、`STP_AEE_NFS_ROOT_LEGACY` 双源 extract 遍历全删）；健康页 node job 只认 `STP_CONTROL_PLANE_NODE_JOB`；`STP_NFS_ROOT` 钉死为**脚本专用别名**（hot-update 镜像主键值给已发布脚本，运行时零引用） |

## 放弃的备选

- **缺信号 job 立即打 UNKNOWN**：会让 claim→首次续租窗口内的健康 job 被误杀；
  改为锚定下发时刻 + 一个完整执行窗口。
- **PG enum 里物理删除 `DEGRADED` 值**：PostgreSQL 不支持 remove value，
  迁移收益为零（生产库已确认 0 行），代码层不可达即可。
- **脚本改读 `STP_AEE_NFS_ROOT`**：scripts/ 是已发布的版本化工件，不可改写；
  选文档钉死别名语义（方案 B）。
- **保留 EventUploader 独立开关**：两 flag 实际永远同值同配，合并降低配置面。

## 如何验证

- 生产库（`.env.backend` → stp）只读确认：`plan_run` 无 DEGRADED 行；
  `plan_step` / `script` 中 scan_aee / export_mobilelogs 零引用 —— 删除前置成立。
- agent 1129 / backend services+scheduler+core 551(+16 skip) / backend api 752 /
  frontend vitest 602 + tsc 全绿；ruff、eslint 干净。

## 事故记录

期间另一并发 agent 会话对本工作区做了 stash/checkout/restore，导致部分已改文件
在其后的提交中静默回退（recycler、LeaseRenewer 等 6 文件）。已在 f0dbaf7 全量
恢复并重跑全套测试；教训：共享工作区上的长链条未提交改动必须在每个逻辑单元后
立即 commit + push。

## 何时重议

- 若未来出现真正的「旧 Agent ↔ 新控制面」滚动窗口（当前未上线、可同版本发布），
  batch 续租 404 回退与 updated_at 双写需要作为特性重新设计，而非恢复旧代码。
- `plan_run_status` PG enum 中的 DEGRADED 值如需物理清理，等 PostgreSQL 支持
  `ALTER TYPE ... REMOVE VALUE` 或做 type-rebuild 迁移，与其它 enum 变更合批。
