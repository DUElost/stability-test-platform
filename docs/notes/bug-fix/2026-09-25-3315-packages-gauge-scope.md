# #3315：fleet 包模式 gauge 被单机 refresh 部分作用域覆盖（2026-09-25）

Status: implemented
Class: bug-fix

## Decision

#3222 首采当天（rev 37a56b41 部署后）实测：DB `host.script_packages_mode` 48/48 = package
正确，但 Prometheus `stability_host_script_packages_mode{mode="package"}=1`（计数总和=1）。
根因：`POST /script-presence/refresh?host_id=` 走 `run_sweep(host_ids=[单个])`，而
`_persist_modes` 不区分作用域，把本轮 1 台的 `modes` 切片 Counter 直接 `.set()` 到 fleet
gauge 上。本单修复：`_persist_modes` 增加 `full_scope`（与 `_cleanup_orphans` 既有
`full_scope=host_ids is None` 同构），**仅全量 sweep 更新 gauge**；列写是 per-host upsert，
天然不受作用域影响，单机 refresh 照常只写列。

## Alternatives

- **按列全表重算 gauge**：单机 sweep 后重读 DB 聚合再 set——多一次全表查询且把「本轮观测」
  与「历史累计」混在一个语义里（refresh 会把别的 host 的陈旧列也算进本轮指标）；弃。
- **refresh 改走独立持久化路径**：复制 _persist_modes 逻辑，违背单一写点；弃。
- 选择：调用方传作用域，gauge 语义收窄为「最近一次**全量** sweep 的 fleet 聚合」——
  与 ADR-0051 对账不变量（package == hosts_total）的读取方式一致。

## Verification

- 红绿双向：新增 `test_host_scoped_sweep_writes_column_but_not_gauge` 在修复前代码上
  **恰红 1**、修复后 4 passed（先构造反例证明守卫会失败）；
  `test_full_sweep_sets_fleet_gauge_with_host_count` 钉住全量聚合形状 {package:2,其余:0}
- `test_script_presence.py` + `test_script_presence_api.py` → 26 passed；
  `check:quick` → 17 gates OK
- 部署后：等下一次全量 sweep（每日 timer 或手动无 host_id 的 refresh），期望 gauge
  package=48；再触发一次单机 refresh，确认 gauge **不再掉数**

## Revisit

- #3222 Revisit 的告警规则（`tree|mixed > 0 持续 30m`）在本修复部署前**不要**基于 gauge 加——
  会被任何一次 refresh 打成假样；本单合入后即可照常推进。
