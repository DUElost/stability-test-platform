# 日志观测权威边界（#527）

- **日期**：2026-08-29
- **关联**：Epic [#527](https://github.com/DUElost/stability-test-platform/issues/527)；前任 #519 / PR #524

## 决定了什么

- **保留双链**：`job_log_signal` = 跑测中及时观测；`device_log_event` = 归档/上送/extract 权威。
- **不做** watcher-summary 全量改读 DLE（会伤害 RUNNING 秒级反馈）。
- **Phase 0（#524）**：风险评级 `log_observation.aggregate_risk_summary` 已 DLE 主 + unlinked signal 补洞。
- **Phase 1（本批）**：
  - `signal_link_reconcile` 周期 sweep 调 `link_signals_to_device_log_events_sync` 补链（#556 后；只读路由不补链）；
  - `archive.link_stats` 暴露 AEE/VENDOR_AEE 链接健康度（`link_rate` 为粗口径；
    告警用 `fixable_link_rate`，口径见 `docs/notes/feature/2026-08-30-signal-link-stats-three-way-split.md`）；
  - 新增 `GET /plan-runs/{id}/log-events` 终态 DLE 视图；
  - 设计文档 §5 消费方矩阵。

## 放弃的备选

- **#519 选项 A**（watcher-summary 改读 DLE、signal 降级）：RUNNING 延迟与 ANR/MOBILELOG 语义不匹配，不采纳。

## 如何验证

- `pytest backend/tests/services/test_log_observation.py -q`
- `pytest backend/tests/api/test_plan_run_log_events.py -q`
- 生产：`watcher-summary` → `archive.link_stats`；终态 PlanRun → `log-events` 路径与 NFS 一致。

## 何时重议

- reconciler 路径 signal↔DLE 链接率长期 < 目标阈值；
- 需把 RUNNING `abnormal_rate` 改读 DLE 时（须先证明链接 100% 且延迟可接受）。
