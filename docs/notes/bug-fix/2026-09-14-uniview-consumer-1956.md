# #1956 展锐（UNIVIEW）事件不进异常仪表盘、且 DLE 停在 LOCAL

Status: implemented
Class: bug-fix

## Decision

采集侧打通后（#1946），真机事件已进 `job_log_signal` / `device_log_event`（#73 验收），
但两个**消费面**没跟上，外部表现就是「DLE 有行、异常仪表盘为 0、状态永远不动」。
本单按**最小且可证**的方式补上：

1. **仪表盘口径统一到单一真源**。原 `_load_deduped_aee_events` 硬编码
   `category IN ('AEE','VENDOR_AEE','ANR')`，而风险汇总侧早已有
   `_SIGNAL_RISK_CATEGORIES = (…, 'UNIVIEW')` —— 两处各写一份清单必然漂移。
   现由 `log_observation.ANOMALY_SIGNAL_CATEGORIES` 派生，并加防线用例。
2. **UNIVIEW 自成一组**（`group='UNIVIEW'`），先于 `ANR`/`VENDOR_AEE` 判定：
   展锐也有 `ANR` 子类型，若不分开会被并进 MTK 的 `AEE/ANR` 桶，混平台后失真。
   包名细分组连带带上 `group`，前端按 `${group}-${subtype}` 做 key，避免同名项互相覆盖。
3. **无 scan 门禁的平台入库即可上送**。Agent 的 `EventUploader._recover_states()`
   **只**上送 `UPLOAD_PENDING`；MTK 靠控制面 `upload_task` 依 scan xls 引用打标，
   展锐没有 scan 产物 → 永远停在 `LOCAL`。而 `LOCAL` 在本模型里的语义是
   「**已被 scan 但未被引用 → 有意不传**」（见 `count_pending_upload_events` 注释），
   对展锐属语义误用；`saq_tasks` 里既有的 "barrier … before UNISOC uploads land"
   也说明设计本就预期展锐会上送。故在 DLE 入库处（两处落库点）把
   `UNIVIEW` 的等待态提升为 `UPLOAD_PENDING`。

**范围边界（明确不做）**：`WatcherSummary.total` / `aee_breakdown` 仍按
`crash/vendor_crash/anr` 三个计数器聚合，语义上容不下 UNIVIEW，需新增计数器与 UI，
属独立改动，不在本单。`ARCHIVED`/extract 挂在 scan/dedup 链（#463 的地盘），
本次只把状态推进到可上送（→ `REMOTE`），**不声称已解决归档解压**。

## Alternatives

- **只改 WHERE 加 UNIVIEW**：会产出 `crash/vendor_crash/anr` 全 0 的行，被既有
  「三类计数全 0 跳过」的兜底丢掉，等于没改 —— 故不动 `aee_breakdown`。
- **让 Agent 自己把状态写成 `UPLOAD_PENDING`**：绕过控制面策略，且偏离 ADR-0028
  方案 A「控制面决定谁该传」的分工；故仍在中心侧入库时判定。
- **重开 continuous 模式（LOCAL 全传）**：`#287` 已删除该分支，且会让 MTK 的
  「有意不传」重新变成全传，影响面远大于本问题。
- **只在 run 终态加一个「补齐打标」任务**：更贴近「控制面决定」，但需要新增任务
  与轮次编排钩子；本次选更小的入库判定，若后续要统一到任务侧，迁移成本很低。

## Verification

- 真机侧（#73 已验收）：Z2582 `serial=62002360` 一次普通 PlanRun 即产出
  `device_log_event(UNIVIEW, Java Crash/ANR/Boot Category)` 与对应
  `job_log_signal(category=UNIVIEW, source=reconciler)`。
- 仪表盘侧：新增 `backend/tests/api/test_watcher_summary_uniview_1956.py`
  —— 展锐 2 条计入 `total_events`；`("UNIVIEW","ANR")` 与 `("AEE","ANR")` **分桶独立**；
  同一 `nfs_path` 重复拉取只计 1 次；包名细分组带 `group`。
- 上送侧：新增 `backend/tests/services/test_device_log_event_uniview_upload_1956.py`
  —— `UNIVIEW/LOCAL → UPLOAD_PENDING`，`AEE/LOCAL` 与在流程态**不变**；
  并断言两处落库点都接入（防止只改一处）。
- **反例实证**：把口径改回硬编码三元组 → 功能用例与防线用例**同时转红**
  （`assert 2 == 4`），恢复后全绿。
- 回归：`test_plan_run_aggregation_endpoints.py` 等 84 项通过；其中既有整字典断言
  因新增 `group` 字段同步更新（属有意的附加字段）。
- 前端：`AnomalyDashboard.test.tsx` 10 项通过（含新增 UNIVIEW 分组用例），`tsc` 无错。

## Revisit

- 仪表盘与 `aee_breakdown` 的口径若仍各写一份，会再次漂移；倾向后续把
  `crash/vendor/anr` 三计数器扩展为按类别计数（含 UNIVIEW），届时删除硬编码。
- 「入库即可上送」是**平台级策略**：若后续要区分「展锐事件是否一律上传」，
  应改为可配置（按平台/事件类型的白名单），而不是散在 ingest 里的常量。
- 展锐事件的 `device_timestamp`（UTC）在仅有 `kick_datetime` 时仍为 `None`
  （设备时区未核实，见 #73 评论），不影响本条修复。
