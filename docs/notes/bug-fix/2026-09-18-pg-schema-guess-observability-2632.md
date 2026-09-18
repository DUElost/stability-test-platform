# PG「猜 schema」指纹的观测面（#2632 缺口①：观测先行）

Status: implemented
Class: bug-fix

## Decision

#2632 的三个缺口里，#2713（连接带 `application_name`，可归因）与 #2727（生产机
loopback 测试库拒载）已合入；**缺口①「无拦截、无留痕、无告警」** 仍是空的——现场是
2026-09-16/17 约 30 条一次性 SQL 错误（表名两个方向都猜错、枚举用大写、引用当时未落地的
列），**事后人读 PG 日志才发现的**；若哪次猜对了，就是一次不留审计痕迹的生产读写。
本单按 issue 的「最小方案优先」把它做成可观测面：

1. **生产者** `tools/dev/pg_error_guard.py`：只读 PG 服务日志（默认
   `/var/log/postgresql/postgresql-*-main.log*`，含轮转的 `.1`；单文件只读尾部 8MB），
   统计**最近一小时内**三类指纹并落 node-exporter textfile 指标（无标签 gauge）：
   `stp_pg_undefined_table_events` / `stp_pg_undefined_column_events` /
   `stp_pg_invalid_value_events` / `stp_pg_schema_error_events`（合计）/
   `stp_pg_guard_last_run`。
2. **部署**：`deploy/control-plane/systemd/stp-pg-guard.{service,timer}`（每 5 分钟；
   与 `stp-mem-top`/`stp-script-guard` 同款沙箱与「不写 `User=`」理由——指标目录属 root）。
3. **告警** `StabilityPgSchemaGuessing`（平台文件 + 站点子集，逐字段一致）：
   `stp_pg_schema_error_events >= 5 or time() - stp_pg_guard_last_run > 3600
   or absent(stp_pg_guard_last_run)`，`for: 10m`。
4. **接进既有判据面**：`tests/metrics_registry.py` 的 `_TEXTFILE_PRODUCERS` 增列本脚本
   （5 个名字从源码静态提取）；promtool 场景补齐；`_SELF_OWNED_METRIC_RE` 前缀集扩
   `stp_pg_`。

**关键取舍**：

- **中英两套消息形态都匹配**：生产日志是**本地化**的（zh_CN），只写英文形态会**恒零**——
  那正是本仓反复出现的「绿而空」形态（指标在、值恒 0，看起来像「没人在猜」）。
- **窗口计数（gauge）而不是增量计数器**：不需要状态文件，天然对日志轮转稳健；代价是
  每次重扫尾部 8MB（5 分钟一次，可忽略）。
- **阈值 5 条/小时**：现场是 30 条量级的**聚集**；改走 SOP 的 `information_schema`
  首步之后单次手误不报，避免对正当诊断刷屏。
- **告警里带 liveness**（`last_run` 超 1 小时 / 指标消失）：采集器停摆时计数恒 0，
  看起来像「没人在猜」——这是同一条「绿而空」防线。

## Alternatives

- **常驻 daemon 读 PG 日志**：否决。为一个 5 分钟粒度的计数长期占一个进程，收益不抵
  复杂度；timer + 尾部扫描已足够。
- **每条错误都告警**：否决。单次手误（正当诊断中的一次笔误）会刷屏；聚集才是信号。
- **用 `log_statement`/`log_min_error_statement` 打开全量语句日志**：否决。那是把
  **所有**语句（含正常查询）写进日志，隐私面与体量都不可接受；本单只要错误面。
- **走应用 `/metrics`**：不可行。PG 日志是宿主侧的，应用进程读不到（也不该读）。
- **把只读角色（缺口②）一并做掉**：不做。建角色是对生产库的**写操作**（DDL），必须由
  运维按 SOP 执行；本 PR 只把「先求证 schema」写进 SOP 与权威文档（见下）。

## Verification

- **反例构造（先证伪再采信）**：
  - A 去掉中文字面量（只留英文形态）→ 5 条用例红（含脚本自检）；
  - B 去掉窗口过滤 → 2 条红；C 去掉 ERROR 行判定（续行也计数）→ 2 条红。恢复后 7 passed。
- **既有判据面两次抓到本单的遗漏**（都是设计意图）：
  - `test_alert_metric_producers` 报「未解析出本仓自有指标名」→ 扩 `_SELF_OWNED_METRIC_RE`
    前缀集（与 #735 那次同形的第二次）；
  - `test_every_alert_rule_has_scenario_case` + `test_promtool_gate_detects_per_rule_threshold_drift`
    要求新规则有**有牙的**场景 → 场景里把 `last_run` 喂成巨大值，让另两条分支恒不成立，
    阈值变异（5 → 1e12）才能把本告警打红。
- **一处自我更正**：我原计划在 `production-diagnostics.md` 新增一节「先求证 schema」，
  落笔时发现**安全边界一节早有这条**（含「这类错误应聚合成告警」的指向）——遂删除重复章节，
  改为把该条更新为指向已落地的告警与生产者；`prod-db-readonly-diagnose` skill 的 SOP
  首步补上「先查 information_schema」（那里此前没有）。
- 实测：`pytest tests/ -q -k "site or prometheus or alert or monitoring or metrics or pg_error"`
  → **632 passed**；`python scripts/run_gates.py check:quick` → **[OK] (12 gates)**。

## Revisit

- **缺口②（手工查询用超级用户 / 应用共享凭据）**：`application_name` 已让归属可见，
  但「专用只读角色」仍未建——那是对生产库的 DDL，需运维执行；建议的出口：
  `CREATE ROLE stp_ro LOGIN PASSWORD …` + 仅 `SELECT` 授权 + SOP 改用它。
- **阈值与窗口**：5 条/小时是**估计值**（按现场 30 条量级推的）。上线后按
  `stp_pg_schema_error_events` 的真实分布再校；若出现「正当诊断被误报」，优先看
  SOP 首步是否被跳过，而不是直接抬阈值。
- **`probe@stp_probe` 认证失败 ×4**（issue 里的旁证）：监控探针凭据失效，另单处理。
- **journalctl 侧**：本采集器读文件日志（`/var/log/postgresql/…`）。若将来 PG 改成
  journal-only（`logging_collector=off`），需改为 `journalctl -u postgresql` 读法并同步
  本判据。
