# 09-04 七日审计文档同步（ADR-0032 落地态 / 平台分区路径 / dedup 键回落 / /health 键名等）

Status: implemented
Class: process

## Decision

把 2026-09-04 七日审计（08-28→09-04，只读）确认的常驻文档漂移一次性修齐。
所有改动仅来自 HEAD 代码/迁移/提交可直接核对的证据，不夹带推测；窗口内的
09-03 同步（PR #839）已覆盖的项（DEGRADED / #220-UNISOC 表述 / #506 /
params / NFS merge 行）不重复处理。对应审计方法见
`docs/notes/process/2026-09-03-doc-drift-sync-768-772.md`。

- **ADR-0032 自身状态未随 P1 落地推进**：文档停留在 v0.6「P1 编码可开工」，
  而 P1 已由 `922049d2`（08-31 18:43）与 `2368228f` 合入 main（`aee/unisoc_reconciler.py`、
  `unisoc_scan_runner.py`、控制面双 merge）。升 v0.7：勾掉 P1 编码清单项，
  注明剩余 Z258 真机验收与 B3 spike 不阻塞。
- **scan 上送路径已平台分区，设计文档仍写「平铺」**：`upload_manager.upload_scan_report`
  带 `platform_subdir`（MTK→`dedup/{run}/mtk/`，UNISOC→`dedup/{run}/unisoc/`），
  控制面 `dedup_scan` 按 `dedup_base + mtk + unisoc` 三目录收集；而
  `docs/design/2026-adr-0025-log-flow-sequence.md` 六处仍写
  `dedup/{run}/{host_id}_*.xls（平铺）`。全部改为平台分区口径（对齐 ADR-0032 存储行）。
- **#518 删除 scan 键兼容回落，两处注释未清**：`a179b003` 删除控制面对旧无前缀
  `STP_DEDUP_SCAN_*` 的回落（`dedup_scan.py` 只读 `STP_BACKEND_DEDUP_SCAN_*`）；
  AGENTS.md Key env 表行与 `backend/.env.example` 注释仍称「兼容回落 + WARNING」。
  已按「不再回落」口径修正；同 commit 曾同步的 `docs/development/environment-variables.md`
  复核一致。
- **/health 键名 2026-08-29 起带 `_enabled`**（`d2b2cea5`）：`docs/operations/adr-0026` 字段表、
  jq 取数示例与「当前 /health」校验行、`docs/adr/ADR-0027` 两处字段表述改为新键名并注
  明改名日期（日期化日志行保留原键名作历史记录）。
- **容量公式未含认领上限**（#773）：CLAUDE.md 容量公式仍写
  `min(MAX_CONCURRENT_TASKS - active, heartbeat effective_slots)` 且 migration id 多一位
  （`q2r3s4t5u6v7w8`）；实际 `capacity_reporter` 为
  `effective_slots = min(空闲健康设备 − 活跃, 健康上限, STP_MAX_CLAIM_SLOTS 默认 5)`（#483）。
  改 CLAUDE.md 公式 + 补 AGENTS.md Key env 行。
- **新专项 onboarding runbook 三处指引过期**：specialty 必填 / project_key 可选（v2.5 D11）、
  建项目无 platform/form_factor/product_line facet（D12）、project_key 可经 rename 端点修改
  （`d94b2ee8`）。已按 HEAD schema 修正 curl 示例与口径。
- **P2.5 工作台文档自称「现行」**：`docs/design/2026-08-project-registry-p25-mapping-workbench.md`
  仍引用已删的 `device.project_id` / `match_models` 写路径。标记为「历史记录（v2.4），已由
  ADR-0029 v2.5 取代」，不改历史内容。
- **杂项**：`pr-update-branch.yml` workflow_run 触发名 `PR Agent (DeepSeek)` 已随 08-30 改名
  （`7e942f88` → `PR Agent (advisory review)`）失效——恢复为现名；`ci.yml` 全量 job 注释
  「push main / workflow_dispatch」与其 `on:`（无 push）矛盾——改为 workflow_dispatch-only。

## 验证

- 每处改动对照 HEAD 代码行核实（文件路径与 commit 证据见审计报告，2026-09-04 会话）。
- 覆盖的 open issue：#773（容量公式，合入后可关）；#788 items 1（AGENTS merge 回退表述）与
  4（pr-update-branch 触发名）为部分覆盖，其余 item 保持 open。
- 未改 #788 item 2（log-flow 文档 CONTINUOUS 逃生阀残留）等非本次审计确认项——留给原 issue。

## 何时重议

- ADR-0032：Z258 真机验收 / B3 spike 完结时推进 v0.8。
- #788 其余 items 由原 issue 跟踪。
