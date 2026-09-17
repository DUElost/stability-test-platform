# 孤儿 DLE 清理：键集推进 + 跳过可观测 + 形态判据两侧同形（#2316 残余）

Status: implemented
Class: bug-fix

## Decision

`purge_orphan_dle_events` 旧实现每轮只取 `ORDER BY updated_at LIMIT n` 的**头一批**，
而被跳过的行（共享根未配置 / 形态不符 / 目录删不掉）**只 `continue`**：不删行、不计
失败、不推进游标。这类行恒为最老 → 每轮占据批头；积压到批大小后 `purged` 恒为 0，
且只有 warning 日志（静默空转）。三条收口：

1. **键集推进**：按 `(updated_at, id)` 翻页（`tuple_(...) > cursor`），单轮上限
   `_ORPHAN_DLE_MAX_PAGES = 10` 页——本轮可以越过跳过行继续找可清理的行；翻页有界
   是因为「整表都是跳过行」时无界翻页会把一次 cron tick 变成全表扫描。
2. **跳过可观测**：新增 `stability_dle_orphan_skipped_total{reason}`，三个跳过分支
   分别计 `root_unset` / `path_invalid` / `purge_failed`（与既有日志锚点同名）。此前
   积压完全不可观测。
3. **形态判据两侧同形**：写入侧 `validate_device_log_remote_path` 只要求路径落在
   `devices/unassigned/{event_id}/` **之下**（层级不限），清理侧 `_locate_unassigned_event_dir`
   此前只认固定层数（`p.parent.parent`）→ 更深的**合法**路径被判形态不符、恒跳过。
   改为按祖先定位（自下而上找第一个以 `unassigned/` 为父的祖先）。

**保留的既有语义**：文件先于行（目录清不掉就不删行，下轮重试）、`dry_run` 只盘点
不改动、共享根未配置时保守跳过、不把数据形态问题计入 failed（那会让整批推迟）。

**新指标准入（#2287 的人工准入项：定义与消费者同一 PR 交代）**：
`backend/core/metrics.py` 的 `dle_orphan_skipped_total` ← 生产者
`backend/scheduler/cron_scheduler.py::purge_orphan_dle_events` 的三个跳过分支
（`.labels(reason=...).inc()`，同文件直接导入，不依赖 `record_*` 间接跳）。
**当前不挂任何告警/仪表板**（先有可观测事实，阈值与告警另议）。

## Alternatives

- **持久化游标（DB 字段或 Redis）**：否决。Redis 只承载队列与瞬时通信（硬不变量），
  加 DB 字段要迁移；而「跳过行下轮**应当**重试」本身就是本清理的设计语义（目录删不掉
  多为暂时态），持久游标会把这层语义做坏。
- **把跳过行计入 failed 并前进**：否决。没有持久游标就「无处可前进」；且它把
  「文件先于行、下轮重试」改成「一次失败即放弃该行」，会留下无索引的目录。
- **无界翻页直到扫完**：否决。见上，cron tick 的耗时必须有界；10 页 × 批大小
  = 单轮最多扫 1000 行，远大于任何合理积压。
- **只加计数、不改取批**（把问题推给运维）：否决。计数能看见积压，但看不到进展——
  验收 ① 明确要求「≥100 条形态不符行时 `purged` 仍 > 0」。
- **只修形态判据、不推翻页**：否决。形态判据只消灭 `path_invalid` 这一类；目录删不掉
  （`purge_failed`，如只读挂载）同样恒占批头，两根柱子都要拆。

## Verification

- 新增三条用例（`backend/tests/scheduler/test_retention_cleanup.py`）：
  1. `test_orphan_cleanup_progresses_past_invalid_rows`：120 条形态不符行（均比好行老）
     + 1 条可清理行 → **`purged == 1`** 且目录与行都清掉（验收 ①）；
  2. `test_orphan_cleanup_counts_skipped_by_reason`：
     `REGISTRY.get_sample_value("stability_dle_orphan_skipped_total", {"reason": "path_invalid"})`
     **+1**（验收 ②）；
  3. `test_orphan_cleanup_accepts_deep_paths_under_event_dir`：
     `unassigned/{id}/sub/dir/dump.log` → 事件目录整棵清掉（形态同形）。
- **反例构造（先证伪再采信）**：
  - A 去掉键集推进（`if cursor is not None` → 恒假）→ 用例 1 **FAILED**；
  - B 恢复固定层数定位 → 用例 3 **FAILED**；
  - C 删掉 `path_invalid` 的计数 → 用例 2 **FAILED**。恢复后 39 passed。
- 实测命令与结果：
  - `TESTING=1 python -m pytest backend/tests/scheduler/ -q` → **126 passed**；
  - 指标面三门禁（`tests/test_alert_metric_producers.py` /
    `test_grafana_dashboard_contract.py` / `test_prometheus_alerts_contract.py`）→
    **39 passed**（新指标按 #2287 的「每个定义必须有生产者证据」判绿）；
  - `TESTING=1 python -m pytest tests/ -q` → 1366 passed, 1 failed：仍是
    `test_script_seed_governance.py::…`（**主线既有红灯**，与本单无关）；
  - `python -m ruff check`（改动文件）→ All checks passed；
  - `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**。

## Revisit

- **>10 页永久性跳过行仍会「零推进」**：现在单轮最多扫 `10 × limit` 行，超出后本轮
  仍可能 `purged == 0`。真正的终局是给「永久形态不符」的行一个**显式终态**（例如
  记入 failed 计数后从候选集排除，或运维清理）；本轮先把「可观测 + 有界推进」做上，
  阈值与告警等 `stability_dle_orphan_skipped_total{reason=path_invalid}` 有基线后再定。
- **索引**：候选谓词是 `(state ∈ …) AND updated_at < cutoff ORDER BY (updated_at, id)`，
  既有索引 `idx_device_log_event_state_updated(state, updated_at)` 覆盖过滤与首列排序；
  键集条件引入 `id` 后若在真机上看到 planner 退化，再评估 `(state, updated_at, id)`。
- **`purged` 的语义**：返回值仍是「本轮删掉的行数」，跳过量走指标。若将来有调用方
  想按「本轮是否推进」做判断（例如连续零推进告警），需要另加返回值或查询指标——
  当前调用点（`run_retention_cleanup`）只看日志，不改接口。
