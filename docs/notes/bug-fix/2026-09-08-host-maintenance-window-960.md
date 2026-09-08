# 主机维护窗口：热更新期间禁止新派发与 claim（#960）

Status: implemented
Class: bug-fix

## Decision

#960（R04-F17，设计风险）：热更新的互斥只覆盖「发起前一次检查」——409 / abort
drain / 前端预检。检查结束到上传、rsync、重启之间是**无互斥窗口**：期间仍可新
派发或 claim 到该主机，重启会打断刚派下去的作业；并发热更新还共用固定远端 tar
路径（`/tmp/stp-agent-update.tar.gz`）互相覆盖。本轮未验证竞态实际发生。

修复（用户裁决：DB 新列 + 迁移）：

- `host` 增加 `maintenance_until TIMESTAMPTZ NULL` + `maintenance_holder
  VARCHAR(128) NULL`（迁移 `m8n9o0p1q2r3_host_maintenance_window`）。
  **用截止时刻而不是布尔标志**：持有进程崩溃时窗口到点自然失效，既不需要对账
  清扫，也不会把主机永久钉在维护态；holder 用于并发热更新互相识别。
- 新增 `backend/services/host_maintenance.py`：`in_maintenance_window`（唯一判据，
  naive 输入按 UTC 解释）、`acquire/release`（`SELECT ... FOR UPDATE` + 立即提交
  —— 不提交对其他进程不可见，等于没有互斥；release 只在 holder 匹配时生效，避免
  迟到的释放擦掉别人新开的窗口）、`maintenance_window` 上下文管理器（异常路径也
  释放）。
- 三处热更新入口统一占窗口：UI `POST /hosts/{id}/hot-update`（占用冲突 → 409
  `HOST_IN_MAINTENANCE`）、precheck 回退 `sync_host_via_hot_update`（占用冲突 →
  `host_in_maintenance` 失败）、`backend/scripts/batch_hot_update.py`（占用冲突 →
  跳过该主机）。
- 派发侧 `plan_dispatcher_sync._classify_dispatch_devices_sync` 新增拒因
  `host_maintenance`（与 `host_offline` 同为**可重试**调度状态，V2 准入队列下进
  QUEUED 等待，不进 `_FATAL_DISPATCH_REASONS`）；claim 侧 `_claim_jobs_for_host`
  在 host 行锁内检查同一判据，命中即空手返回。
- `host_updater` 远端 tar 改为每次操作独立路径
  （`/tmp/stp-agent-update-<uuid>.tar.gz`），执行完 best-effort `sftp.remove`。

## Alternatives

- 复用 `HostStatus.MAINTENANCE`：不加列不迁移，但 `Host.status` 现为
  ONLINE/OFFLINE/DEGRADED，心跳 reconciler、schema Literal、ai_assistant、前端都
  按三值判定；引入第四态会把「维护」与「失联/降级」耦合进同一状态机，放弃。
- Redis 瞬时互斥：无迁移，但维护窗口是**控制面派发事实**（决定能不能派），放
  Redis 与 AGENTS.md「Redis 不作为业务事实存储」冲突，且分区/崩溃语义更弱。
- 只改 tar 路径不动互斥：解决并发覆盖，但「检查后到重启之间可新派发」这个主诉
  仍在，放弃。

## Verification

- `pytest backend/tests/services/test_host_maintenance.py`：11 passed（判据四态 /
  acquire 互斥 / 过期窗口可被接管 / 错误 holder 不释放 / 上下文管理器异常路径也
  释放）；
- `pytest backend/tests/services/test_plan_dispatcher_device_validation.py` +
  `backend/tests/api/test_agent_api_watcher.py`：52 passed（新增
  `host_maintenance` 拒因与「claim 跳过维护窗口」用例）；
- `pytest backend/tests/services/test_precheck_sync.py test_plan_precheck.py
  test_host_maintenance.py`：53 passed（precheck 回退路径未被窗口逻辑破坏）；
- `pytest backend/tests/services/test_host_updater.py`：新增远端 tar 路径唯一性用例；
- `alembic heads` 单一 head = `m8n9o0p1q2r3`（父 `n4o5p6q7r8s9`）；
- `pytest backend/tests`：全量通过。

## Revisit

- 窗口只挡**新派发与 claim**，不拦截已在跑的作业（那由既有的 abort drain 负责）；
  若将来热更新需要「窗口内连 recovery/resume 也拒」，应在同一判据处扩展，而不是
  另起一套标志；
- TTL 默认 900s、上限 3600s（`STP_HOST_MAINTENANCE_TTL_SECONDS` 可调）：若真实热
  更新（含依赖安装）实测超过上限，需要调配置而不是延长代码常量；
- UI 与批量脚本目前对「占用冲突」是失败/跳过，没有排队重试；若运维出现高频冲突，
  下一步是把冲突变成显式排队而不是报错；
- `maintenance_holder` 目前只用于并发识别与释放校验，未开放给 API 展示；若要给
  UI 展示「谁在更新这台主机」，需要显式加出参（schema 变更另走 PR）。
