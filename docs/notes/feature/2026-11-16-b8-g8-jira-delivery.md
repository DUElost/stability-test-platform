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
   - **工具仓 #27 已实现（`24f86c89`，契约 `b8-g8-attribution-v1`）**：两套阶段一脚本现把 `Signature ID` / `Occurrence IDs` / `Occurrence Keys` / `Occurrence Count` / `Attribution Status` 等列追加到 `JIRA_Upload_List_*.xlsx`；阶段二在隔离工作区写 `attribution.json`（`links` / `gaps`）与 `result/*_jira_batch_create_result_*.json`。平台按本单 `record_jira_run_completion` 消费 links/gaps；真实 Jira 建单/匹配按 B8 阶段二执行，本 PR 只用模拟 Jira 结果联调。

3. **`FAILED` PlanRun 证据交付与零自动提单（§11 场景 11）**：
   - 修复 `backend/services/issue_fact_store.py`（`list_shadow_scan_run_ids`、`list_dedup_first_delivery_due_run_ids`）、`backend/services/shadow_scan_rounds.py`（已写 `DEDUP_FIRST` 模式时不覆盖回 `CURRENT`；尚无周期轮的终态 `DEDUP_FIRST` 分区仍可推进最后一轮）与 `backend/services/evidence_publication.py`（`sweep_evidence_publications` 对 `DEDUP_FIRST` 分区恢复交付版本登记），使 `FAILED` PlanRun 的 `DEDUP_FIRST` 分区在调度与清理扫面中继续完成最终轮、代表发布、交付版本恢复与 D9 判定。
   - `allow_auto_jira_for_plan_run` 对 `PlanRunStatus.FAILED` 恒返回 `False`，自动路径产生 0 份自动提单清单与 0 条自动 `JiraRun`；同时人工查看/下载与显式触发 `POST /api/v1/jira/runs` 仍可用，同 run 或对照 `CURRENT` 分区维持现行行为。

4. **受控兼容补跑（`backend/services/compat_rerun.py`、`compat_rerun` 表）**：
   - 新增 `POST /api/v1/plan-runs/{run_id}/dedup/compat-rerun` 与 `GET /api/v1/plan-runs/{run_id}/dedup/compat-reruns`。
   - 仅限 Owner/admin 对单个 `DEDUP_FIRST` 分区显式发起（非 admin 返回 403，非 `DEDUP_FIRST` 返回 409）。
   - 按 `center_evidence_verified` → `center_remote_verified` → `center_spill_verified` → 仅非 `PRUNED` 时的 `agent_local_original` 定位受 I4 保护的源材料；部分缺失时在 `missing_items` 逐项列出并将 `is_partial` 设为 `True`；产物写至独立的 `compat_reruns/{run}/{platform}/r{id}/`（含 `Result_MergeFiles_Compat_*.xls` 与 `compat_manifest.json`）。
   - 按 `(plan_run_id, platform, request_key)` 幂等重放或失败重试（`attempts` 递增）；**保持原 `DeviceLogEvent.state`（`ARCHIVED`/`PRUNED`，不写回 `LOCAL`）、原 `DeliveryVersion` 与累计发生数不变**；绑定同一 `compat_rerun_id` 的 `stage="create"` 在 `RUNNING`/`SUCCESS` 后拒绝重复消费（409）。

5. **平台侧阻塞返修（2026-11-16，固定反例基线 `90ef7f65`）**：
   - **附带表格不得绕过登记范围**：`resolve_delivery_jira_input` 与 `resolve_compat_rerun_jira_input` 在收到附带表格时调用 `validate_uploaded_jira_workbook_scope`，逐行读取身份字段（`Id` / `ExpType` / `Detail`·`CurProcess` / `Path`）。每一行已填写的字段必须**共同指向同一条登记问题**：按 `manifest.issues` 的 `(signature_id, normalized_key, signature_raw, representative_verified_path)` 关系逐项严格比对，合法 Id 搭配同版本另一问题、跨版本或跨 run 的 Path 均拒绝。空表、无身份字段或任一行无法绑定均返回 409，且发生在准备隔离工作区与启动子进程之前。独立 `source=upload` 手工上传入口保持原行为。
   - 边界：绑定入口按阶段区分格式——`stage=upload_list` 接受去重 `.xls`（按 `Id` / `ExpType ` / `Path` / `Detail`·`CurProcess` 校验），`stage=create` 必须使用工具仓 #27 生成的 `.xlsx` 上传清单（按 `Signature ID` / `Occurrence IDs` 校验）；阶段/格式错配 fail-closed（400）。独立 `source=upload` 不受此限制。
   - **登记提交成功前不启动工具**：`POST /api/v1/jira/runs` 的 `JiraRun` INSERT/COMMIT 异常不再被吞掉后继续启动，改为记录日志并返回 500；子进程启动次数为 0，数据库不留半登记行。
   - 新的只读 `.xls` 读取点按 #739 依赖收敛门禁登记到 `tests/test_excel_dependency_inventory.py` 与 `docs/notes/process/2026-09-20-dependency-convergence-739.md`；不新增写侧或产物格式。

6. **输入字段一致性与 #27 归因产物消费（2026-11-16，反例基线 `7c4cdddc2`，工具联调基线 `24f86c89`）**：
   - **列冲突前置拒绝**：读取工作簿时保留原始表头并按 `strip().casefold()` 查重；重复列、大小写冲突（如 `Path` + `path`）与同一字段的别名冲突（如 `Signature ID` + `signature_id`）在准备隔离工作区与启动子进程之前返回 409。返回的列号即工具实际消费的列，避免平台校验的字段与被工具读取的字段不一致。
   - **绑定 .xlsx**：新增 `openpyxl` 只读解析工具仓 #27 产出的上传清单；`stage=create` 按 `Signature ID` 与 `Occurrence IDs` 逐行校验，签名必须在 manifest 登记范围、发生必须命中该签名的 `attribution_candidates`。支持人工重排/筛选（按标识而非行号）；跨 run、跨版本、冲突标识与范围外发生均拒绝。`.xlsx` 只用于阶段二，去重 `.xls` 只用于阶段一。
   - **消费 links / gaps**：`record_jira_run_completion` 读取 `attribution.json` 的 `links` 与 `gaps`，保留 #27 契约的 `kind` / `stage` / `row` / `workbook_row` / `signature_id` / `occurrence_ids` / `reason` 等诊断字段；越界 link 只记 `unverified_link_scope` 不落库；重复回调仍由终态门卫幂等短路。状态判定：`stage != create` → `NOT_APPLICABLE`；有 link 无 gap → `VERIFIED`；有 link 有 gap → `PARTIAL`；无 link 有 gap → `UNATTRIBUTED`；无 link 无 gap → `NOT_APPLICABLE`。

7. **联调夹具去敏与发布前检查（2026-11-16，基线 `7059bcc02`）**：
   - 两份 `.xlsx` 夹具用工具仓#27 返修候选 `12587942` 的真实阶段一脚本重建；生成入口把 `build_log_server_access_text()` 替换为固定模拟段（`example.invalid` / `example_user` / `<simulated>`），`Reporter` 固定为 `example_reporter`，其余列、样式、归因列与行结构仍由真实工具生成。
   - 发布前逐单元格扫描地址/账号/口令与 URL/邮箱模式，并检查隐藏工作表/行/列、AutoFilter、超链接、批注、定义名称、外部链接、`docProps` 与 OOXML 包内 XML；结果未命中。当前夹具的 `creator` 仅为 `openpyxl`。
   - **公开历史**：本 PR 早期夹具提交曾包含非模拟访问字段，已在本轮重建后移除；历史提交的处置（是否改写分支历史）与相关凭据轮换需仓库负责人决定，实施者不自行改写已公开历史。

8. **两套工具从平台绑定入口联调（工具返修候选 `12587942`）**：
   - 传音与天珑分别以平台 `source=delivery_version` 的 `upload_list` 入口启动真实固定工具生成清单，再经人工删行/重排后从 `create` 入口启动真实建单入口（Jira 连接替换为模拟实现），平台消费 `attribution.json` 的 links/gaps 落库。
   - 覆盖同一链根下两个实际 `PlanRun`（A/B）、同一 run 的 v1/v2、筛选排除行、重复发生 gap、越界 link 与重复回调。每个 Jira key 只关联本次登记且选中的发生；筛选掉的问题不落 link，越界 link 只记 `unverified_link_scope` 不落库，重复回调不新增。

## Alternatives

- **方案 A（未采纳）**：按厂商 stdout 或结果 JSON 的行序直接反推并绑定 `DeliveryVersion` 发生列表。被否决：阶段一脚本会跳过/合并行且历史 duplicate key 无对应新事件，行序对应关系在真实场景中必然错配并污染 `IssueSignature.issue_keys`。
- **方案 B（未采纳）**：兼容补跑时将 `DeviceLogEvent.state` 临时改回 `LOCAL` 或复用 `dedup/{run}/Result_MergeFiles_*.xls` 写回现行目录。被否决：违反 ADR-0062 D15 与 I4，会污染现行分区判据和原交付物权威。

## Verification

- 隔离 PG16（testcontainers）+ 临时文件系统 + 真实 `RunConsole` 子进程替身回归：
  - `.venv/bin/python scripts/run_pytest.py -q backend/tests/services/test_b8_g8_jira_delivery_and_compat.py backend/tests/api/test_dedup_jira_endpoints.py backend/tests/services/test_b8_g7_partition_consumers.py backend/tests/services/test_shadow_delivery.py backend/tests/services/test_shadow_scan_rounds.py backend/tests/services/test_jira_vendor_engine.py tests/test_api_response_shape_contract.py tests/test_excel_dependency_inventory.py tests/test_api_4xx_advice_has_real_exit.py` → **181 passed**；`test_b8_g8_jira_delivery_and_compat.py` 单文件 → **29 passed**。
  - `.venv/bin/python scripts/run_pytest.py -v backend/tests/test_migration_guard.py backend/tests/test_schema_sync_guard.py backend/tests/test_health_schema_revision.py backend/tests/migration/test_delivery_generation_migration.py` → **27 passed**。
- 空 PG16 迁移与 schema 对齐验证：
  - `alembic upgrade head` → `alembic downgrade b8g6d0e1f234` → `alembic upgrade head` → `backend/scripts/check_schema_sync.py` → **0 新增 diff**（`EMPTY_PG16_MIGRATION_AND_SCHEMA_SYNC_OK`）。
- **平台侧阻塞红绿对照（同一探针，隔离 PG16 + 临时 FS + 真实替身子进程）**：
  - 固定反例基线 `90ef7f65`（独立 detached worktree）：6 个探针中 **4 failed / 2 passed**——`delivery_version` 范围外表格返回 200 且替身读到 `foreign-to-delivery`、登记事务失败仍返回 200 并启动子进程、`compat_rerun` 范围外表格返回 200、不可读取的 `.xlsx` 附带表返回 200 并启动；两个正向探针（范围内表格、启动前登记可见）通过。
  - 返修后新 HEAD：同 6 个探针 **6 passed**；范围外请求均 4xx、启动次数为 0，范围内表格与登记范围一致，登记事务失败返回 500 且零启动。
  - 第二轮同行绑定（反例基线 `81177701a`）：`delivery_version` / `compat_rerun` 各一个用例覆盖“合法 Id + 同版本另一问题 Path / 跨版本 Path / 跨 run Path”；基线首个场景即返回 200 并启动替身（该实现按 Id、ExpType 分别命中即放行，不校验 Path 与同一关系），新 HEAD 三类均 4xx、零启动，正向行（Id 与 Path 同行一致）通过并核对替身收到的表格。
  - 第三轮输入校验 / xlsx / 归因（反例基线 `7c4cdddc2`，工具联调基线 `24f86c89`）：
    - 重复列 / 大小写冲突 / 别名冲突（含 `Path` 指向另一 run、`path` 指向合法路径）在 `delivery_version` 与 `compat_rerun` 两个入口覆盖：基线返回 200 并启动替身，新 HEAD 409、零启动。
    - 厂商仅 gaps（`unknown_signature_id`，无 Jira key）：基线 `NOT_APPLICABLE` 且 `attribution_gaps=[]`，新 HEAD `UNATTRIBUTED` 且缺口可查询。
    - 真实工作簿：传音与天珑阶段一脚本各生成 3 行清单；重排/筛选后按 `Signature ID` / `Occurrence IDs` 绑定原 `DeliveryVersion`，两套均 200 且替身实收行与登记一致；跨 run、跨版本、冲突标识、范围外发生（4 场景 × 2 工具）全部 4xx、零启动。
    - 两套真实工具归因模块（模拟 Jira 结果）各产出 4 条 `links` + 2 条 `duplicate_occurrence_id` gaps；平台消费后为 `PARTIAL`，越界 link 不落库，回调重放不重复写入。
    - 基线探针 **4 failed**（2 个 `.xlsx` 正向 + 2 个 planner 反例）/ 新 HEAD **4 passed**。
    - 隐藏行与工具入口对齐：`.xlsx` 的 AutoFilter/隐藏行不参与范围校验（工具建单入口同样跳过），新增“隐藏行标识越界不阻断可见行”用例。
    - 两套工具联调（工具候选 `12587942`）：传音/天珑各跑平台 `upload_list` → 真实清单生成 → 人工筛选/重排 → 平台 `create` → 真实建单入口（模拟 Jira）→ links/gaps 落库。A v1（3 行筛除 1 行并交换）、A v2、B v1 全部只关联登记选中发生；重复发生产生 `duplicate_occurrence_id` gap 且不落 link；越界 link 不落库；重复回调幂等。该集成探针为本地联调脚本，随证据记录，不进入 CI 用例。
  - 夹具去敏与 informational 复跑（基线 `7059bcc02`）：
    - 重建后的传音/天珑夹具逐单元格与 OOXML 包检查未发现实际地址、账号或口令；隐藏内容、超链接与文档属性均无额外载荷。
    - `test_device_rebind.py::TestRebindApi::test_just_powered_off_device_rejected` 在基线与本 HEAD 用同一条件（模块导入后延迟 320s 再执行，静默窗 300s）均返回 200 而非 409；原因是测试用导入时固定的 `_NOW` 构造 `last_seen`，长收集后已越过 300s 静默窗。结论：既有测试的时间基准缺陷，与本 PR 改动无关；informational 结果相同。
- 门禁与前端验证：
  - `.venv/bin/python scripts/run_gates.py check:quick` → **16 gates 全部通过**（含 `ruff`、`eslint`、`tsc`、`knip`、`compileall`、`layering`、`orphan-models`、`gov-surface`、`ai-work`、`god-files`、`inner-imports` 等）。
  - `cd frontend && npm run test -- --run` → **154 files / 1355 tests passed**。
- Agent 与 CI 检查：
  - `.venv/bin/python scripts/run_pytest.py -q backend/agent/tests/` → **3424 passed, 2 skipped, 1 failed**（`backend/agent/tests/test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails`，与本单未修改的 `backend/agent/` 宿主组权限环境有关；以 PR 上游 6 项 required CI 实跑为准）。
  - 6 项 required CI（`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、`pr-agent-tests`、`pr-migrate-empty-db`）：本 PR 最终 HEAD 全部 SUCCESS（以 PR 页面为准）。

## Revisit

- 工具仓 #27（`24f86c89`）已交付 `stp.jira_attribution/v1` 的 links/gaps 与工作区输出；真实 Jira 建单/匹配按 B8 阶段二安排执行，平台侧已消费契约产物并用模拟 Jira 结果完成联调。
- 阶段二激活（`partition_mode` 切换）与生产发单仍需经 Owner 按 `#3800 §5` 另行放行，不随本 PR 自动生效。
