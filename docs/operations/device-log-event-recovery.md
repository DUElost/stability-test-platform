# 事件目录数据恢复 runbook（DLE / extract）

extract（`run_extract_sync`）的事件发现**只**来自 `device_log_event` 表的
`REMOTE/ARCHIVED/PRUNED` 行（`list_remote_paths_for_extract`）。中心存储上
存在目录、但表里没有对应行的数据不会被 extract 收进 `jira/{plan_run_id}/`，
重跑也不会。本文给出把这类目录补进提取范围的手工流程。

## 什么时候用

- 存量数据迁移：DLE 机制上线前上传到 `{root}/devices/{plan_run_id}/` 的目录；
- 上传成功但 DLE 行未落库（Agent patch 失败且本地已删）。

## 恢复流程

1. 确认目录在中心存储上的绝对路径，例如
   `/mnt/cifs/devices/123/2026-08-13_14-30-00_db.01`。
2. 用 venv 里的 psycopg 直连生产库（env 源见仓库根 `.env.backend`，只读
   SELECT 优先、写操作先备份），插入一行 `state='REMOTE'` 的 DLE：

   ```sql
   INSERT INTO device_log_event (
     id, serial, platform, event_type, detected_at, state,
     local_path, remote_path, host_id, created_at, updated_at
   ) VALUES (
     gen_random_uuid(), 'UNKNOWN', 'UNKNOWN', 'UNKNOWN', now(), 'REMOTE',
     '', '/mnt/cifs/devices/123/2026-08-13_14-30-00_db.01', '<host_id>',
     now(), now()
   );
   ```

   - `remote_path` 必须落在 `{STP_AEE_NFS_ROOT}/devices/` 之下（extract 的
     安全校验要求，`resolve_extract_event_src`）；
   - `plan_run_id` 需要关联时直接在 INSERT 里带上该列；
   - 不关联则留 NULL，extract 的 `associate_unassigned_events_to_plan_run`
     会按 job/serial+时间窗尝试自动归队（前提是目录在
     `devices/unassigned/{event_id}/` 下，此时按该布局存放）。

3. 重跑该 PlanRun 的 extract（重新触发终态或 SAQ 手动 enqueue
   `extract:{plan_run_id}`），确认 `jira/{plan_run_id}/` 出现该目录、
   `run_context.extract.copied` 计数 +1。

## 边界

- 同一 basename 已在 jira 目录中时，后补的行不会覆盖既有内容（同名去重，
  只取每组第一行；其余保持 REMOTE 并记
  `run_context.extract.same_basename_left_remote`）。目录内容确有差异时，
  先把 jira 里同名目录改名再重跑。
