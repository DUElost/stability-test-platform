# 待数据 issue 的只读采集清单（2026-09-26）

- 状态：Living（采集完成、结果全部回贴后，本文件移入 `docs/archive/`）
- 用途：下列 issue 的裁决或阈值依赖现网 / 真机 / 隔离环境的实测数据。**数据回贴到对应 issue 之前，这些单不开放认领**
  （各单已有「启动认领前提」评论指向本文）。
- 执行纪律：生产库查询按 [`production-diagnostics.md`](production-diagnostics.md) 与 `prod-db-readonly-diagnose` skill 执行——
  先求证 schema、只跑 `SELECT`、连接串只经环境变量传入、不打印凭据与主机清单；**禁止**在生产库试跑测试或压测。
- 回贴格式：每项贴「执行时间 + 基线 commit + 原始输出（可截断，注明截断）」，不贴连接串与主机凭据。

## 总览

| # | issue | 取数方式 | 执行者 | 数据到位后做什么 |
|---|---|---|---|---|
| 1 | #3066 | 生产库只读 SQL | 有生产只读权限者 | 按 issue 内分支表裁决「中止后是否续链」 |
| 2 | #3320 | 生产库只读 SQL + 机队只读命令 | 有生产与主机只读权限者 | 判定 mtbf 资源路径是否真断，确认后领单修 |
| 3 | #3289 | 生产库只读导出 + 隔离空库 bootstrap | 生产只读者 + 任一 harness | 产出差异清单，按清单定修复路径 |
| 4 | #3328 | 生产日志只读 grep | 有控制面日志读权限者 | 得到 merge wall time 分布，判断是否改分片锁 |
| 5 | #3327 | 生产库只读 SQL | 有生产只读权限者 | 以基线定告警阈值，领单做监测 |
| 6 | #3316 | 生产库只读 SQL | 有生产只读权限者 | 以分布定完整率判据，领单做对账 |
| 7 | #3184 | Prometheus 只读查询 + 生产库只读 SQL | 有监控与生产只读权限者 | 对比提前放行与满窗的 init 失败率，决定是否保留就绪门 |
| 8 | #3231 | 隔离控制面实测（不碰生产） | 任一 harness（需 Docker / PG 环境） | 给出准入持锁窗分档数据，决定是否修订 ADR-0026 |
| 9 | #3219 | 真机实测（需先合入分段计时） | 分段计时可领单；实测需有 25 台真机的主机 | 给出整轮心跳耗时分布与误判次数 |

## 1. #3066：run 503 那 481 条中止的来源

SQL 与分支裁决表见 #3066 的「方案备注」评论（`audit_logs`、`job_instance.status_reason`、`plan_run.run_context` 三条）。

## 2. #3320：mtbf 资源根是否真断

生产库（在库计划参数与引用版本）：

```sql
SELECT p.id AS plan_id, p.name, ps.stage, ps.script_name, ps.script_version, ps.params
FROM plan_step ps JOIN plan p ON p.id = ps.plan_id
WHERE ps.script_name LIKE 'mtbf\_%' ESCAPE '\'
ORDER BY p.id, ps.stage, ps.sort_order;
```

每台跑过 MTBF 的主机（只读，`<install>` 为 Agent 安装目录）：

```bash
ls -d <install>/resources/mtbf <install>/agent/resources/mtbf 2>&1
grep -c '^STP_MTBF_RESOURCES_DIR=' <install>/.env
```

判读：计划参数为空、`.env` 无该键、且只有 `<install>/agent/resources/mtbf` 存在 ⇒ 包模式下 `parents[3]` 解析落空，确认「真断」。

## 3. #3289：新站参数面与生产参数面的差异

生产库导出（只读）：

```sql
SELECT name, version, is_active,
       (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(default_params) AS k) AS param_keys
FROM script WHERE is_active ORDER BY name, version;
```

隔离环境（任一 harness，禁碰生产）：空库 `alembic upgrade head` → scan 首扫 → 对同一 SQL 导出。两份按 `(name, version)` 对齐，输出「版本 × 缺失键」清单。

## 4. #3328：merge 的 wall time 分布

控制面日志（只读，`<release>` 为发布根；日志含轮转文件）：

```bash
grep -ahE 'merge_started plan_run=|merge_done plan_run=' <release>/logs/backend.log* \
  | sed 's/\x1b\[[0-9;]*m//g'
```

按 `(plan_run, platform)` 配对 `merge_started` → `merge_done`，输出每次时长与 p50 / p95 / 最大值、每日次数。
`merge_started` 在拿到全局锁之后打印，因此它量的是锁内耗时；锁等待另以「上一个 `merge_done` 到下一个 `merge_started`」的间隔近似。

## 5. #3327：库内增长基线

```sql
SELECT relname, n_live_tup, n_dead_tup,
       round(100.0 * n_dead_tup / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       last_autovacuum, last_autoanalyze, seq_scan, idx_scan
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 25;
```

建议相隔 7 天各采一次，用两次差值得到增速；阈值取「基线增速 × 余量」，不预设数字。

## 6. #3316：证据完整率的现状分布

近 30 天已结束的 run，按 run 对齐 DLE 与产物：

```sql
SELECT pr.id AS plan_run_id, pr.status,
       (SELECT count(*) FROM device_log_event d WHERE d.plan_run_id = pr.id) AS dle_total,
       (SELECT count(*) FROM device_log_event d WHERE d.plan_run_id = pr.id
          AND d.state IN ('REMOTE', 'ARCHIVED', 'PRUNED')) AS dle_remote,
       (SELECT count(*) FROM job_artifact a JOIN job_instance j ON j.id = a.job_id
          WHERE j.plan_run_id = pr.id) AS job_artifacts,
       (SELECT count(*) FROM plan_run_artifact r WHERE r.plan_run_id = pr.id) AS run_artifacts
FROM plan_run pr
WHERE pr.ended_at > now() - interval '30 days'
ORDER BY pr.id DESC
LIMIT 200;
```

判读：`dle_remote / dle_total` 的分布即「发现 → 归档」段的完整率基线；另三段（登记、可下载）在实施时补。

## 7. #3184：就绪门提前放行是否拉高 init 失败率

Prometheus（只读）：`sum by (outcome) (increase(stability_plan_chain_settle_outcome_total[7d]))`。

控制面日志：`grep -ah 'plan_chain_trigger_settle_early_release parent=' <release>/logs/backend.log*` 取出提前放行的父 run，
再在生产库对比两组子 run 的 init 失败率：

```sql
SELECT c.parent_plan_run_id, c.id AS child_run,
       count(*) FILTER (WHERE j.status_reason LIKE 'lifecycle init failed%') AS init_failed,
       count(*) AS jobs
FROM plan_run c JOIN job_instance j ON j.plan_run_id = c.id
WHERE c.parent_plan_run_id IN (/* 日志里的父 run id 列表 */)
GROUP BY 1, 2;
```

对照组为同期未提前放行（满窗）的子 run。

## 8. #3231：准入持锁窗（隔离环境，不碰生产）

复用 #105 的合成主机手法（`STP_STATIC_DEVICE_SERALS` + 独立 `AGENT_INSTALL_DIR`），分 44 / 60 / 100 / 150 host × 25 设备四档，
每档记录：准入事务时长 p50 / p95 / p99、PG 锁等待、峰值被锁行数、准入期间心跳 `gap_max` 与 `stability_host_heartbeat_missed_total`、
重排 / 回滚次数。场景与判据对齐见 #3231 正文。

## 9. #3219：单主机 25 台真机整轮心跳耗时

前提：先合入分段计时（`_tick` 总时长 + 分段直方图，per-host 有界基数；这部分**现在即可领单**）。
合入并部署后，在一台挂满 25 台真机的主机上按 #3219 正文的分档 × 场景矩阵各跑至少一个长跑窗，回贴分布与误判 OFFLINE 次数。
