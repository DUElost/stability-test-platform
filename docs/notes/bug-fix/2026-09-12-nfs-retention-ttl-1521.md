# retention 增加 NFS 轨回收（#1521 / R-01）

Status: implemented
Class: bug-fix

## Decision

`run_retention_cleanup` 自述清终态 PlanRun，但函数体**只删 DB 行**——NFS 侧
`{nfs_root}/devices/{plan_run_id}/` 与 `{nfs_root}/dedup/{run_id}/` 的原始
日志与产物**无保留期机制**（全仓 13+53 处文件删除点逐一核对：均为本地 SSD
prune 或坏副本重传，无一针对 NFS 保留期）。R-01 的 916GB 盘打满即此双轨
缺口：DB 轨每小时清理、NFS 轨无对应任务；且 **DB 行是目录的唯一索引**——
行删后目录不可回溯（越清越无法回溯）。

修复（`cron_scheduler.py`）：

1. `purge_run_storage_dirs(run_ids)`：删 `{root}/devices/{id}/` 与
   `{root}/dedup/{id}/`（root 取 `resolve_shared_storage_root()`，未配置时
   跳过并告警）；run_id 整型路径分量（无注入面），仅精确两级目录；
2. **顺序：文件先、DB 行后**（同批 safe_run_ids 在删行前清理）；
3. **失败自愈**：文件清理失败的 run 剔除出本批 DB 删除——行保留 → 下轮
   retention 重选同批重试（先文件后行的顺序使失败不会破坏索引）；
4. 三维判据的落地口径：终态 + mtime 水位由既有候选查询承担（终态状态 +
   `started_at < cutoff`）；**容量水位触发的激进回收未实施**（Revisit）。

## Alternatives

- **独立 NFS 扫描任务（按目录 mtime 判 TTL，不依赖 DB）**——放弃（本单
  范围）：可回收「DB 已删的存量孤儿目录」，但需要安全边界设计（目录名与
  run 的对应、unassigned/ 排除）；本单先把「同批双轨」闭环（增量止漏），
  存量孤儿属 Revisit；
- **容量水位触发激进回收**——放弃（本单）：需要盘用量探针与阈值策略
  （issue 三维之一），常规时间水位已闭合主缺口；
- **删除 jira/ 等其它 NFS 子目录**——暂缓：issue 列 devices//dedup/ 两个
  确定对象；其余子目录若同源再扩展。

## Verification

- **反例实证**：回退 cron_scheduler 实现保留测试 → purge 用例失败（目录
  未清）；修复版全绿；
- 新增用例（`test_retention_cleanup.py` +3）：到期 run 两目录随行删除 /
  活跃（RUNNING）run 目录保留 / **rmtree 失败时 DB 行保留待下轮重试**；
- `test_retention_cleanup.py` 全套 **9 passed**；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- **存量孤儿目录**：DB 行已删（本修复前）的 `devices/`、`dedup/` 残留无
  索引可回溯，需一次性扫描/人工核对后清理（可评估 mtime-only 的安全扫描
  任务）；
- **容量水位激进回收**（issue 三维之三）：用量超阈值时的加速回收策略待
  设计（与 #1522 Agent 侧追打同族思路）；
- `jira/` 等其它 NFS 子目录的保留期是否同规，视使用面另议。
