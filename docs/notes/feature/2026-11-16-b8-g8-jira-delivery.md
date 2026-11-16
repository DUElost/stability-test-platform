# B8 G8：提单适配、FAILED 交付与受控兼容补跑（#4023）

Status: implemented
Class: feature

日期：2026-11-16　基线：`origin/main@1c320e39`　批次：#3800（v1.38）　ADR：ADR-0062（D9 / D10 / D15 / §11 场景 10–11）

## Decision

1. **按交付版本绑定的 Jira 提单与隔离工作区（`backend/services/jira_delivery.py`、`backend/api/routes/dedup.py`）**：
   - `POST /api/v1/jira/runs` 新增 `source="delivery_version"` 与 `source="compat_rerun"`，保留既有 `source="upload"` 与 `source="plan_run"` 兼容入口不变。
   - `source="delivery_version"` 严格校验 `PlanRun × platform × DeliveryVersion` 归属及 `DEDUP_FIRST` 分区模式，仅从已登记 `DeliveryVersion` 及其统计导出（`_meta/{run}/_delivery/{platform}/v{n}.json`）与关联事实（`issue_ids` / `event_ids` / `EvidencePublication(VERIFIED)`）生成输入清单与标准 `aeeexp` `.xls`，**绝不扫描物理链根目录**，跨 run、跨平台、版本错配均拒绝（404 / 409）。
   - 每次运行分配独立可写工作目录 `{STP_DEDUP_WORK_DIR}/jira_workspaces/{console_run_id}/`，厂商子进程固定以该目录为 `cwd`，不向 `evidence/` 主文件夹或中心 `devices/` 目录写入任何中间/结果文件。
   - 沿用 `#1084`「先写库再启动子进程」顺序，保证子进程秒退、启动失败回写与重复终态回调（`record_jira_run_completion` 终态门卫）幂等。

2. **有证据支持的 issue key ↔ 发生关联与不猜配缺口记录（`jira_occurrence_link`）**：
   - 仅当工作区内的可验证结构化输出（`attribution.json` / `jira_attribution.json` 或带 `signature_id` + `occurrence_id` 的结果 JSON）经本次绑定 `manifest.json` 范围核验通过后，才幂等写入 `jira_occurrence_link` 并向 `IssueSignature.issue_keys` 追加键；超范围候选被拒绝并记缺口。
   - 若厂商输出无法证明对应关系，**绝不按日志行序或目录猜配**，将缺失原因写入可查询字段 `JiraRun.attribution_status` 与 `JiraRun.attribution_gaps`（`unattributed_issue_key` / `unverified_link_scope`）。
   - **提请规划者裁定（`DUElost/automation-toolkit` `stability_Jira-Automation` 实测样本与调用点）**：
     - 阶段一 `Transsion_Jira_Tool_20260323/generate_transsion_jira_upload_list.py` 与 `Tinno_Jira_Tool_20260520/generate_tinno_jira_upload_list.py`（及对应 `*_upload_template_common.py`）仅读传统去重列名并过滤/汇总输出 `JIRA_Upload_List_*.xlsx`，丢弃 `signature_id` / `occurrence_id`；未传 `--set-output` 时写 `CURRENT_DIR/result/`（而非进程 `cwd`）。
     - 阶段二 `Transsion_Jira_Tool_20260323/create_transsion_jira_batch_from_excel.py` 与 `Tinno_Jira_Tool_20260520/create_tinno_jira_batch_from_excel.py` stdout 仅打印 `第 %d 行创建成功: %s` 与 `matched_jira_key=...`，且把结果 JSON 写往脚本自身的 `CURRENT_DIR/result/{vendor}_jira_batch_create_result_{ts}.json`，条目仅含 `{row, status, project, summary, issue_key, matched_jira_key, decision, error}`，**无 `signature_id` 或 `occurrence_id`**。因此现有工具族输出在行被跳过/去重或复用历史 key 时无法向平台证明归因，当前由平台记 `attribution_gaps`（`vendor_result_json_lacks_occurrence_id`）待规划者裁定是否向工具仓库追加结构化透传单。

3. **`FAILED` PlanRun 证据交付与零自动提单（§11 场景 11）**：
   - 修复 `backend/services/issue_fact_store.py`（`list_shadow_scan_run_ids`、`list_dedup_first_delivery_due_run_ids`）、`backend/services/shadow_scan_rounds.py`（已写 `DEDUP_FIRST` 模式时不覆盖回 `CURRENT`；尚无周期轮的终态 `DEDUP_FIRST` 分区仍可推进最后一轮）与 `backend/services/evidence_publication.py`（`sweep_evidence_publications` 对 `DEDUP_FIRST` 分区恢复交付版本登记），使 `FAILED` PlanRun 的 `DEDUP_FIRST` 分区在调度与清理扫面中继续完成最终轮、代表发布、交付版本恢复与 D9 判定。
   - `allow_auto_jira_for_plan_run` 对 `PlanRunStatus.FAILED` 恒返回 `False`，自动路径产生 0 份自动提单清单与 0 条自动 `JiraRun`；同时人工查看/下载与显式触发 `POST /api/v1/jira/runs` 仍可用，同 run 或对照 `CURRENT` 分区维持现行行为。

4. **受控兼容补跑（`backend/services/compat_rerun.py`、`compat_rerun` 表）**：
   - 新增 `POST /api/v1/plan-runs/{run_id}/dedup/compat-rerun` 与 `GET /api/v1/plan-runs/{run_id}/dedup/compat-reruns`。
   - 仅限 Owner/admin 对单个 `DEDUP_FIRST` 分区显式发起（非 admin 返回 403，非 `DEDUP_FIRST` 返回 409）。
   - 按 `center_evidence_verified` → `center_remote_verified` → `center_spill_verified` → 仅非 `PRUNED` 时的 `agent_local_original` 定位受 I4 保护的源材料；部分缺失时在 `missing_items` 逐项列出并将 `is_partial` 设为 `True`；产物写至独立的 `compat_reruns/{run}/{platform}/r{id}/`（含 `Result_MergeFiles_Compat_*.xls` 与 `compat_manifest.json`）。
   - 按 `(plan_run_id, platform, request_key)` 幂等重放或失败重试（`attempts` 递增）；**保持原 `DeviceLogEvent.state`（`ARCHIVED`/`PRUNED`，不写回 `LOCAL`）、原 `DeliveryVersion` 与累计发生数不变**；绑定同一 `compat_rerun_id` 的 `stage="create"` 在 `RUNNING`/`SUCCESS` 后拒绝重复消费（409）。

5. **平台侧阻塞返修（2026-11-16，固定反例基线 `90ef7f65`）**：
   - **附带表格不得绕过登记范围**：`resolve_delivery_jira_input` 与 `resolve_compat_rerun_jira_input` 在收到附带表格时调用 `validate_uploaded_jira_workbook_scope`，逐行读取身份列（`Id` / `ExpType` / `Detail`·`CurProcess` / `Path`），要求整张表都命中登记 `issue_ids`、`normalized_key`、`signature_raw` 或代表证据路径；空表、无身份列或任一行超范围均返回 409，且发生在准备隔离工作区与启动子进程之前。独立 `source=upload` 手工上传入口保持原行为。
   - 边界：当前厂商阶段一输出 `JIRA_Upload_List_*.xlsx` 不保留 `Id` / `ExpType` 等身份列，因此作为 `delivery_version` / `compat_rerun` 附带表格时无法证明范围而会被拒绝；运行环境只有 `xlrd`（仅 BIFF `.xls`），无法读取 OOXML `.xlsx` 做范围证明，绑定入口对 `.xlsx` 一并 fail-closed（400）。这两类表仍走既有独立手工上传入口，工具侧透传 ID 由已提请的规划者裁定处理（见 Decision 2）。
   - **登记提交成功前不启动工具**：`POST /api/v1/jira/runs` 的 `JiraRun` INSERT/COMMIT 异常不再被吞掉后继续启动，改为记录日志并返回 500；子进程启动次数为 0，数据库不留半登记行。
   - 新的只读 `.xls` 读取点按 #739 依赖收敛门禁登记到 `tests/test_excel_dependency_inventory.py` 与 `docs/notes/process/2026-09-20-dependency-convergence-739.md`；不新增写侧或产物格式。

## Alternatives

- **方案 A（未采纳）**：按厂商 stdout 或结果 JSON 的行序直接反推并绑定 `DeliveryVersion` 发生列表。被否决：阶段一脚本会跳过/合并行且历史 duplicate key 无对应新事件，行序对应关系在真实场景中必然错配并污染 `IssueSignature.issue_keys`。
- **方案 B（未采纳）**：兼容补跑时将 `DeviceLogEvent.state` 临时改回 `LOCAL` 或复用 `dedup/{run}/Result_MergeFiles_*.xls` 写回现行目录。被否决：违反 ADR-0062 D15 与 I4，会污染现行分区判据和原交付物权威。

## Verification

- 隔离 PG16（testcontainers）+ 临时文件系统 + 真实 `RunConsole` 子进程替身回归：
  - `.venv/bin/python scripts/run_pytest.py -q backend/tests/services/test_b8_g8_jira_delivery_and_compat.py backend/tests/api/test_dedup_jira_endpoints.py backend/tests/services/test_b8_g7_partition_consumers.py backend/tests/services/test_shadow_delivery.py backend/tests/services/test_shadow_scan_rounds.py backend/tests/services/test_jira_vendor_engine.py tests/test_api_response_shape_contract.py tests/test_excel_dependency_inventory.py tests/test_api_4xx_advice_has_real_exit.py` → **156 passed**。
  - `.venv/bin/python scripts/run_pytest.py -v backend/tests/test_migration_guard.py backend/tests/test_schema_sync_guard.py backend/tests/test_health_schema_revision.py backend/tests/migration/test_delivery_generation_migration.py` → **27 passed**。
- 空 PG16 迁移与 schema 对齐验证：
  - `alembic upgrade head` → `alembic downgrade b8g6d0e1f234` → `alembic upgrade head` → `backend/scripts/check_schema_sync.py` → **0 新增 diff**（`EMPTY_PG16_MIGRATION_AND_SCHEMA_SYNC_OK`）。
- **平台侧阻塞红绿对照（同一探针，隔离 PG16 + 临时 FS + 真实替身子进程）**：
  - 固定反例基线 `90ef7f65`（独立 detached worktree）：6 个探针中 **4 failed / 2 passed**——`delivery_version` 范围外表格返回 200 且替身读到 `foreign-to-delivery`、登记事务失败仍返回 200 并启动子进程、`compat_rerun` 范围外表格返回 200、不可读取的 `.xlsx` 附带表返回 200 并启动；两个正向探针（范围内表格、启动前登记可见）通过。
  - 返修后新 HEAD：同 6 个探针 **6 passed**；范围外请求均 4xx、启动次数为 0，范围内表格与登记范围一致，登记事务失败返回 500 且零启动。
- 门禁与前端验证：
  - `.venv/bin/python scripts/run_gates.py check:quick` → **16 gates 全部通过**（含 `ruff`、`eslint`、`tsc`、`knip`、`compileall`、`layering`、`orphan-models`、`gov-surface`、`ai-work`、`god-files`、`inner-imports` 等）。
  - `cd frontend && npm run test -- --run` → **154 files / 1355 tests passed**。
- Agent 与 CI 检查：
  - `.venv/bin/python scripts/run_pytest.py -q backend/agent/tests/` → **3424 passed, 2 skipped, 1 failed**（`backend/agent/tests/test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails`，与本单未修改的 `backend/agent/` 宿主组权限环境有关；以 PR 上游 6 项 required CI 实跑为准）。
  - 6 项 required CI（`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、`pr-agent-tests`、`pr-migrate-empty-db`）：待 Draft PR 创建后核对结果。

## Revisit

- 若规划者裁定升级 `DUElost/automation-toolkit`（`stability_Jira-Automation`），使其透传 `signature_id` / `occurrence_id` 并写回阶段二结果 JSON（且支持进程 `cwd` 输出），需对应补充结构化结果文件解析与工具包版本联调验证。
- 阶段二激活（`partition_mode` 切换）与生产发单仍需经 Owner 按 `#3800 §5` 另行放行，不随本 PR 自动生效。
