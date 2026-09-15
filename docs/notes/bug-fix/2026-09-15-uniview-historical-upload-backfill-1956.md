# 展锐历史事件补做上送提升（存量追溯）

Status: implemented
Class: bug-fix

## Decision

新增 `tools/dev/backfill-no-scan-gate-upload-state.py`，把**存量** UNIVIEW 事件从 `LOCAL`
纠正为 `UPLOAD_PENDING`（从而由 Agent 的 EventUploader 上送），并在生产库执行完毕。

问题本质是**语义误用**而非「链路缺失」：`LOCAL` 在本模型中的语义是「有 scan 但未被引用 →
有意不传」（ADR-0028 方案 A），而展锐（UNISOC）`event_type=UNIVIEW` **没有 scan 产物**
（有效性在 Agent 解析期即判定，见 #1946：normalboot-only 目录直接丢弃），因此入库即可上送。
#1957 起该提升在**入库时**生效（`resolve_initial_upload_state`，调用点
`backend/api/routes/agent_api.py`），但对**存量行不追溯** —— 于是 2026-09-14 产生的 4 行
永远停在 `LOCAL`、屡次验收都表现为「采集通了但不上送/不归档」。

工具的关键取舍：

1. **不复制判据**：候选由 `backend/services/device_log_event.resolve_initial_upload_state`
   判定，脚本只负责「选行 + 落库」，规则自身由其单测守（
   `backend/tests/services/test_device_log_event_uniview_upload_1956.py`）；
2. **默认 dry-run**，`--apply` 才落库；只动 `state`（+ `updated_at`），不碰路径/大小/校验和；
3. **竞态保护**：`UPDATE ... WHERE state = <旧值>`，期间被其它流程改动则跳过该行；
4. **幂等**：提升后行已不在等待态 → 重跑命中 0 行。

## Alternatives

- **改 `LOCAL` 的语义或对全部历史行做批量提升**：未选：MTK 的 `LOCAL` 是**正确**语义
  （等待 scan xls 引用），批量提升会破坏 ADR-0028 方案 A 的过滤模型。
- **在平台侧加「定时回溯」任务**：未选：存量是一次性问题（新行已由 #1957 在入库时提升），
  常驻任务会为一个已收敛的缺口附加长期复杂度；确有同类需求时可再议。
- **直接 SQL UPDATE**：未选：不可审计、不可预演、无幂等/竞态保护，且判据会与代码分叉。
- **为已完成的历史 run 重跑 scan/merge 以补齐 `ARCHIVED`**：未选：#2010 的验收显示归档是
  **run 级步骤**，对已结束的 run 属 replay 语义，超出本单范围（本次把事件恢复到**已上送**
  即达成「不丢数据」）。

## Verification

- **预演**：命中**恰好 4 行**（runs 393/394 的 UNIVIEW 行），MTK 一行未动；
- **执行**：4 行成功提升，跳过 0 行；复跑命中 0 行（幂等）；
- **状态推进（无需任何作业）**：30s 后 4 行均为 `UPLOAD_PENDING`，60s 后 4 行均为 `REMOTE`
  且 `remote_path` 非空 —— Agent EventUploader 常驻链生效；
- **用例**（`backend/tests/services/test_upload_state_backfill_1956.py`）：
  断言「只规划 UNIVIEW 等待态」+「MTK 的 `LOCAL` 不被提升」+「`REMOTE` 不被重复规划」+ `--limit` 生效；
  **反例实证**：把工具改为忽略规则（`target = row.state`）后用例转红，恢复后转绿；
- 仓库门禁：`check-internal-ip-leak.py --check` 通过、`check_governance_surface.py` 四节契约通过。

## Revisit

- **新行不再需要本工具**：入库期提升自 #1957 生效；仅当再次出现「语义误用」的历史行时才需重跑；
- **`ARCHIVED` 不属于本单**：历史 run 的归档需 replay 语义，另议；
- **若将来新增「无 scan 门禁」的平台类型**：应扩展 `resolve_initial_upload_state` 的
  `_NO_SCAN_GATE_EVENT_TYPES`，本工具会自动跟随（因判据单一真源）。
