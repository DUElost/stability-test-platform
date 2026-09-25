# 中心存储 devices/ 跨 run 硬链接去重入库：按需运行、由磁盘水位告警提示（#3308）

Status: implemented
Class: process

## Decision

1. **脚本入库**：`backend/scripts/center_storage_hardlink_dedup.py`，取代仓库外的一次性运维脚本
   （2026-09-25 首次全量用的是 `~/stp-ops/2026-09-25-storage/l0_hardlink_dedup.py`，109 个 run 释放
   389.1 GB）。判据与安全约束不变，只做三处加固：
   - 根目录必须叫 `devices`，把「不碰 `jira/`」从约定变成检查；
   - `--eligible-from-db` 自己查出「已终态且结束超过 24h」的 run，连接整条只读
     （`default_transaction_read_only=on`），不再需要人手写 SQL 产 run 清单；
   - dry-run 按 inode 模拟剩余链接数，估算的释放量与执行时一致（原脚本对「已共享 inode」的组会低估）。
2. **按需运行，不设定时任务**（owner 2026-09-26 口径：「按需跑，可以使用已有的告警进行提示」）：
   host-storage 三条磁盘水位告警（FillingUp / LowSpace / Critical）的描述里写明「devices/ 的跨 run
   重复可按需改硬链接回收，不删证据」并指向手册 `docs/operations/center-storage-hardlink-dedup.md`。
3. **过渡登记同步**：`center-storage-interim-dedup` 的 `what` 改为指向入库脚本与手册，并写明 Phase B
   落地时脚本、手册与告警描述中的指引一并删除；出口与到期日不变（`adr:ADR-0053#Phase B`，2026-12-31）。

## Alternatives

- **定时任务（cron / APScheduler 每日跑）**：owner 明确选择按需；另外定时跑会把「靠约定保护共享 inode」
  的过渡措施变成常驻机制，与「过渡须有出口」相悖。不取。
- **脚本留在仓库外**：告警要指向一个可执行、可审查的东西；仓库外脚本无版本、无测试，其他会话看不到。不取。
- **新增专门的「该去重了」告警**：已有三条磁盘水位告警已经覆盖「空间紧张」这个触发条件，新增告警只会重复。不取。

## Verification

- `backend/tests/test_center_storage_hardlink_dedup.py`：13 passed（testcontainers 隔离库）。负向优先：
  首尾相同中段不同不链接、小文件 / 新文件 / 软链 / 未列入的 run 不碰、扫描后被改动的文件跳过、
  非 `devices` 根拒绝、状态谓词挡住「RUNNING 但带 ended_at」的病态行、连接 `transaction_read_only=on`。
- 逐条变异自证（改一处、跑测试、恢复）：去掉全量哈希层 / 重新 lstat 守卫 / S_ISREG / 大小门槛 /
  年龄门槛 / devices 根检查 / dry-run 链接数模拟 / 状态谓词 / run 年龄 / 「dry-run 不删临时链接」——
  **10 处变异全部让测试变红**。
- 告警：`promtool test rules alerts-host-resources.test.yml`（3.13.3）中三条存储告警的新描述断言通过；
  `tests/test_host_storage_alerts_3233.py` 需要整份场景文件在 3.13.3 下通过，而 main 上的内存塌陷场景
  在 3.x 下本就失败（与本改动无关，#3344 修复）——叠加 #3344 的改动后整份文件通过。
- `tools/dev/check_transitions.py`：8 条 / 6 在途，一致。

## Revisit

- ADR-0053 Phase B 落地时删除脚本、手册、测试与三条告警描述里的指引，并把过渡项置 `done`。
- 若按需执行变得频繁（例如每周都要跑），说明增长速度已超出过渡措施的承受范围，应加快 Phase B/C，而不是把它改成定时任务。
