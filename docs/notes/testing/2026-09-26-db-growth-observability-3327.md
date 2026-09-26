# #3327：数据库增长基线观测面

Status: implemented
Class: testing

## Decision

控制面 `/metrics` 每进程至多每 5 分钟只读采样一次 `pg_stat_user_tables`。
按 `(schema, table)` 输出 PostgreSQL 估计的 live/dead tuple、表含索引和 TOAST 的
总字节数、累计顺序/索引扫描次数，以及最近自动 vacuum/analyze 的时间戳。
空的 autovacuum/analyze 时间不伪造为 0；表删除后移除对应序列。
采集失败保留上次样本，并让 `stability_db_growth_snapshot_timestamp_seconds`
停止前进，以便区分「值稳定」和「采集失效」。Grafana 消费表大小、dead tuple 占比、
autovacuum 距今时间与样本新鲜度；不在本轮设置增长或膨胀告警阈值。

## Alternatives

- 对业务表逐一 `COUNT(*)`：大表会把监测本身变成全表扫描；采用 PostgreSQL 统计估计。
- 每 15 秒 scrape 都计算 `pg_total_relation_size`：容量数据不需要该频率；按进程限为 5 分钟。
- 直接拿一天的 tuple 数或 dead 比例定告警：无同口径增长基线，先采样再定阈值。

## Verification

- 隔离 PostgreSQL 的 `/metrics` 集成测试确认 `host` 表的大小、tuple 与扫描序列实际暴露。
- 故意让目录查询失败，验证 `/metrics` 仍返回 200、样本时间不前进且旧值保留。
- 仪表盘指标契约检查面板所用序列有真实生产者。

## Revisit

上线后核对采集耗时和序列基数，并按
`docs/operations/2026-09-26-pending-issues-data-collection.md` §5 至少相隔 7 天采两次
同表基线；同时比较 `n_live_tup`/`n_dead_tup`、总字节数、vacuum/analyze 时间与
`seq_scan`/`idx_scan` 的增量。`pg_stat_user_tables` 为估计值，表统计刷新与
`pg_stat_reset` 可使曲线跳变；累计扫描值按重置处理。阈值由增长基线与可用空间
裁定后再加告警。分区、归档与保留策略属于 ADR-0056 的后续裁决，
本 PR 不改变生产库结构或数据。
