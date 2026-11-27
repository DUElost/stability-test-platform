# B11-U1：Monkey AEE 提单表单 A 段只读生成引擎与 API（#4199 / #4174）

Status: implemented
Class: feature

关联：[#4199](https://github.com/DUElost/stability-test-platform/issues/4199)（批次 B11 方案 v1.0）、
[#4174](https://github.com/DUElost/stability-test-platform/issues/4174)（v1.1）、
[#4054](https://github.com/DUElost/stability-test-platform/issues/4054)。

## Decision

按批次 **B11（#4199 规划方案 v1.0）** 与 **#4174 v1.1** Owner 裁决，落地阶段 1（B11-U1）后端 A 段只读提单表单生成引擎与 REST API：

1. **双门禁 + 发生身份归并**（`backend/services/jira_handoff_identity.py`）：
   - 统一读取 `DEDUP_FIRST`（`ScanRound` 有效覆盖 + 空回执）与 `CURRENT`（`PlanRunArtifact` 最新 `*_org.xls` + 同目录 `.mapping.json`）两类已上送 Agent 报表；
   - 跨 run 重复观测优先按 `occurrence_key`（`backend/agent/contracts/occurrence_key.py`）归并；仅无伴随映射或设备侧 `occurrence_key` 缺失且能由选中 run 的 `DeviceLogEvent` 唯一锚定时回退 `(serial, entry)` 并在 manifest 标注 `compat_serial_entry`；
   - `*_temp` / `CurProcessNone` 占位行与未绑定行不混入计数、不静默丢弃，全部显式记入 `manifest.json` 的 `unbound_rows`；
   - 本次/基线划分抽为纯函数 `classify_event_baseline`（以 `<首轮 started_at>` 为界，2010 类坏钟回退 `DLE.detected_at` 供未来 `#4033` 复用），仅基线问题只标注不自动剔除。
2. **只读表单构建与冻结存储**（`backend/services/jira_handoff_builder.py`）：
   - 仅调用控制面轻量 `start_log_scan.py -dedup_org`（V2.1.1，受跨请求文件锁保护，缺 `*_members.json` 时 fail-closed），严禁调用 `-m` / `-m 0` / `aee_extract`，严禁向 15.4 外部共享根写盘；
   - 冻结产物原子写入中心存储受控目录 `_meta/handoff/<handoff_id>/`（含 `manifest.json` + `manifest_sha256`、15 列预览表 `preview_Result_None_None_MonkeyAEE_SH_*.xls` 与 5 页复核工作簿 `review_workbook.xls`），零 DB 迁移，经 `record_audit` 留痕；
   - A 段产物恒标 `status="PENDING_EXPORT"`，预览表 `Path` 列写显式 `PENDING_EXPORT://...` 占位且全部字符串单元格按 B3（#3561 G1 / #3237）中和公式注入。
3. **API 入口**（`backend/api/routes/jira_handoff.py`、`backend/api/schemas/jira_handoff.py`、挂载于 `backend/main.py`）：
   - 提供 `POST /api/v1/jira/handoff/preflight`、`POST /api/v1/jira/handoff/bundles`、`GET /api/v1/jira/handoff/bundles`、`GET /api/v1/jira/handoff/bundles/{handoff_id}` 及 `GET /api/v1/jira/handoff/bundles/{handoff_id}/files/{artifact_kind}`（白名单 + 防穿越/防符号链接）；
   - `platform != "mtk"` 显式返回 HTTP `400` fail-closed；本单元不触碰 `frontend/` 与 `backend/agent/`，保持与 B11-U2/U3/U4 scope 零重叠。

## Alternatives

- **方案甲（向 PG `issue_occurrence` 扩写 9 列厂商文本）**：否决。违反 #4174 Owner 裁决（一期不增迁移、不复制易漂移厂商文本列），且归并规则须保持归自研扫描工具唯一真源（ADR-0062 v1.1）。
- **方案乙（在 U1 内顺手写 15.4 共享根或同步改前端 `types.ts`）**：否决。按 #4199 §3，15.4 受控导出属 B11-U3，前端工作台与 `types.ts` 属 B11-U2（须在独立测试服务器实现与验收），保持四单元零 scope 重叠。

## Verification

实际运行命令与结果见本 PR 正文「验证结果」节（涵盖 B11-U1 全部 8 类反例测试、API 端点测试、`check:quick`、`check_governance_surface.py` 与 `check-internal-ip-leak.py`）。

## Revisit

- B11-U2 合入后在独立测试服务器联调前端 `/issue-tracker` 首 Tab「提单表单生成」与 `PlanRunDetailPage` 复数深链；
- B11-U3 消费本单元冻结 `manifest.json`（校验 `manifest_sha256`）完成 15.4 受控导出与终稿 `.xls` 发布；
- UNISOC 提单表单待 ADR-0062 v2.0 阶段 C 切量后再放开 `platform` 白名单。
