# #3315 根因修正：包模式 gauge 改为 /metrics 拉取期从 host 列现算（2026-09-26）

Status: implemented
Class: bug-fix

## Decision

#3319 用 `full_scope` 参数让 sweep 只在全量作用域 set `stability_host_script_packages_mode`——
治了「单机 refresh 的 1 台切片覆盖 fleet 聚合」这个症状，没治病灶：**gauge 由 sweep 按本轮切片
set** 本身偏离了本仓 DB 派生指标的既有口径（`routes/metrics.py` 的 `_refresh_*_gauges(db)`
一律拉取期从表现算）。遗留后果当晚即实测到：后端 23:34 重启后到 01:32 仍 `NO_SERIES`——
全量 sweep 只有每日 09:30 cron，**每次部署重启后该不变量缺席最长 24h**，
「package == 在册 host 数」大半时间不可核验。

本单：

1. `routes/metrics.py` 新增 `_refresh_script_packages_mode_gauge(db)`，拉取期调已有的
   `fleet_packages_mode(db)`（summary API `fleet_packages` 同一函数：退役不计、NULL 计 unknown），
   四个 mode 全量落值；读失败 rollback（#3102 同款）。
2. `_persist_modes` 只写列、删除 gauge 写与 `full_scope` 参数；`script_presence.py` 随之移除仅为
   gauge 引入的 `Counter`/`metrics` 导入（均由 #3263 引入）。
3. SOP §6「CLI 跑 run_sweep」行更正一条**错误断言**：09-25 我写「alert 不受影响（吃进程内
   gauge）」，实为 `stability_script_presence_sweep_timestamp` 也在拉取期从库 `min(checked_at)`
   现算——CLI 垃圾轮会把 `StabilityScriptPresenceSweepStale` 喂成新鲜。§7 追加更正行（历史行保留）。

## Alternatives

- **保留 #3319 的 full_scope，另加「启动时从库回填一次 gauge」**：两个写点、两套语义，
  仍与本仓拉取期口径分叉；弃。
- **gauge 语义定为「最近一次全量 sweep 的观测」**：只对 cron 那一刻有意义，重启即丢，且与 summary
  API 不同源，对账时两个出口会给不同答案；弃。
- 选择：单一真值 = host 列（per-host upsert，全量/单机 sweep 都写对），gauge 与 summary 同一函数现算。
  列本身的陈旧由既有新鲜度指标覆盖，不在本 gauge 里重复表达。

## Verification

- 红绿双向（修复前 = origin/main 的 #3319 版两文件）：新增
  `test_sweep_writes_columns_only_never_the_gauge`（单机/全量 sweep 均只写列，gauge spy 零调用）与
  `test_metrics_scrape_derives_packages_gauge_from_host_column`（不跑任何 sweep 直接抓 `/metrics`，
  package=2/mixed=1/unknown=1/tree=0，退役 tree 不计）**修复前恰红 2**，修复后 4 passed
- 相邻：presence 服务/API、全部 `test_metrics_*` 与退役读过滤 → 65 passed；
  `tests/test_alert_metric_producers.py` → 8 passed；`check:quick` 见 PR
- 并行交叉核对：在窗 #3077（cursor，STALE/NO_PR）声明含 `routes/metrics.py` 但未落地，其未提交改动
  只动 `core/metrics.py` 的计数器/函数区段（≈L401/L836），与本单改的 gauge 注释（≈L228）不重叠

## Revisit

- 部署后**无需等 cron**：换 rev 重启后第一次抓取即应 `package=48, tree=mixed=unknown=0`——
  取代 #3315 note 与 D6 note 里「等 09:30 cron 核对 gauge」的 Revisit。
- 告警规则 `tree|mixed > 0 持续 30m` 的前置（gauge 可靠、重启不缺席）自此满足，可单独小 PR 推进。
- #3333：候选 ①（refresh-all 入口）的动机之一「gauge 部署后无法当场验证」被本单消解；
  候选 ②（CLI 非服务进程跑全量时 fail-closed）因新鲜度告警也会被喂假而更重要。
