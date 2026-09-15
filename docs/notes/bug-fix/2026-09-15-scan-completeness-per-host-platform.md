# scan 完备性按 (host, platform) 期望集判定，对齐 ADR-0032 B1

Status: implemented
Class: bug-fix

## Decision

1. **控制面**：`dedup_scan.count_hosts_with_scan_artifacts(..., require_platforms=...)`
   退役，改由 `dedup_scan.scan_completeness(run_id, expected, *, since)` 判定；完备性
   单位从「host」改为 **(host, platform) 对**。返回值 `ScanCompleteness` 同时携带
   `hosts_with_artifacts`（host 级）、`units_satisfied` / `units_expected`（对级）。

2. **期望集派生**：新增
   `plan_run_scan_scope.load_expected_scan_platforms(db, run_id, host_ids)`
   → `{host_id: {平台分区}}`，来源与 `load_plan_run_scan_host_ids` 同源
   （`JobInstance` 实际执行 + `PlanRunTargetDevice.host_id_snapshot` prepare 快照）。
   无设备证据的 host 不产生期望。

3. **设备平台 → 归档分区**：新增
   `core.dedup_platform.dedup_platform_for_device_platform`——
   `MTK` / `UNKNOWN` / `NULL` → `mtk`（与 Agent 路由一致：`job_session` 把 MTK 与
   UNKNOWN 都交给 AEE Reconciler，`collector.get_collector_for_platform` 同理）；
   `UNISOC` → `unisoc`；QCOM 等**无采集/扫描实现**的平台 → `None`（不产生期望，
   不让 PlanRun 空等）。

4. **barrier 语义**：`scan_task` 轮询在 `units_satisfied >= units_expected` 时结束；
   `units_expected == 0` 时不为空期望等待（仍跑一次注册以登记既有产物）；
   `saq_scan_no_artifacts`（ERROR）条件从「host 数为 0」改为「有期望但一个单位都
   未满足」，`saq_scan_partial_artifacts`（WARNING）与 `_scan_poll_grace_seconds`
   全部改为单位口径。日志消息保留原 `hosts=%d/%d` 前缀、追加 `units=%d/%d`，
   不破坏基于日志串的既有观测。

5. **保留的不变量**：`since` 水位线、限定本轮 triggered 集合、host 级计数按 host
   去重（不按产物文件数）、**部分未齐仍链后继**（部分报表优于零报表）。

涉及：`backend/core/dedup_platform.py`、`backend/services/plan_run_scan_scope.py`、
`backend/services/dedup_scan.py`、`backend/tasks/saq_tasks.py`、
`docs/design/2026-scan-upload-merge-contract.md`；测试见
`backend/tests/services/test_dedup_scan_merge.py`、
`backend/tests/services/test_plan_run_scan_scope.py`、
`backend/agent/tests/test_saq_scan_pipeline.py`。

**未同步处（已知，随 ADR-0032 v0.8 处理）**：`docs/adr/ADR-0032` 的 B1 实现约束行仍写
`count_hosts_with_scan_artifacts` 这一旧函数名（语义不变，仅名称）。ADR 正文修订需
owner 裁决 + `docs/adr/README.md` 版本行同步，见
[ADR-0032 v0.8 修订提案](../architecture/2026-09-15-adr0032-v08-platform-routing-revision.md)。
本改动**对齐** B1 语义，未改写 B1。`docs/reviews/` 下 2026-09-13 及更早的审查报告
引用旧函数名为其当时事实，保留不改。

## Alternatives

- **维持「每 host 双平台齐」+ 新增 host 级平台能力声明**：改的是 Agent→控制面的上报
  契约（新字段 + 迁移），代价大于本问题；登记为后续方向（ADR-0032 v0.8 提案 R1 备选）。
- **按 Agent 实际配置的 scan 工具动态期望平台**：控制面目前拿不到 per-host scan 工具
  配置，本次先用「设备平台构成」这一可得证据收窄；残留风险见 Revisit。
- **完全去掉平台维度、只保留 host 级计数**：会让 MTK 先到即开 merge、UNISOC 产物漏出
  本轮——正是 #1071 要解决的原问题，不可取。
- **让期望由产物反推**（有产物就算齐）：循环论证，等于取消屏障。

## Verification

- `TESTING=1 JWT_SECRET_KEY=test-secret .venv/bin/python -m pytest
  backend/tests/services/test_dedup_scan_merge.py
  backend/tests/services/test_plan_run_scan_scope.py
  backend/agent/tests/test_saq_scan_pipeline.py -q` → **80 passed**
- 扩大范围（+ `test_dedup_extract.py` / `test_dedup_scan_endpoints.py` /
  `test_dedup_jira_endpoints.py` / `test_dedup_helpers.py` / `test_dedup_platform.py` /
  `test_scan_runner.py` / `test_unisoc_scan_runner.py` /
  `test_plan_run_abort_aggregator_race.py`）→ **233 passed**
- 新增/改写用例（三型 host + 收窄维度）：
  - 纯 MTK host 期望只有 `mtk`：单 mtk 产物即 complete（回归护栏）
  - 纯 UNISOC host 期望只有 `unisoc`：对称面
  - 混平台 host 期望 `{mtk, unisoc}`：MTK 先到 → `1/2` 不 complete；补齐后 complete
  - 三型 fleet（纯 MTK + 纯 UNISOC + 混平台）→ 4 个单位、3 个 host
  - `load_expected_scan_platforms`：MTK/NULL → `mtk`、UNISOC → `unisoc`、QCOM 不计入、
    无设备证据 host 不出现
  - `since` 水位线与 triggered 集合收窄不回退（原用例改写为新 API）
- 门禁（2026-09-15 全量重跑）：`check:pr` → **[OK] 18 gates**；`check:quick` → **[OK] 10 gates**；
  根 `tests/` → **925 passed**；`backend/tests/{api,services,tasks,core}` → **2209 passed**；
  前端 `CI=1 npx vitest run` → **813 passed（106 files）**、`tsc --noEmit` 通过；
  `ruff check backend/ tools/ scripts/` → All checks passed。
- **未运行**：无真实 NFS + 真实 `start_log_scan.py` 的端到端（沿用既有 G-B1 缺口）；
  「纯平台 host 一轮内即达 complete」为单元/集成级验证，未在真实 fleet 观测。

## Revisit

- **残留风险（本轮未解决）**：混平台 host 若只配置了一个平台的 scan 工具，期望仍包含
  另一平台 → 该单位必然超时并记 partial。根治需要 per-host 能力声明（本轮 Alternatives
  已登记）。触发条件：该形态在生产实测中产生可观测的 partial 告警。
- 若 ADR-0032 v0.8 的 R1 被 owner 裁决为「收回 B1 语义、改走能力声明」，本 note 的
  Decision 1–4 需相应回滚，并恢复平台维度的 host 级判定。
- `docs/adr/ADR-0032` B1 的旧函数名一旦在 v0.8 修订中同步，本 note 的「未同步处」
  段落应删除。
