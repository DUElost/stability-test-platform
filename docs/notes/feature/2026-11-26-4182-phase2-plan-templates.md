# Agent Note: #4182 Phase 2 — UI 从模板创建 Plan、复制为草稿与脚本轻标签筛选

- **关联 Issue**：[#4182](https://github.com/DUElost/stability-test-platform/issues/4182)
- **日期**：2026-11-26

## Decision

按 #4182 评审裁决（选项 A）落地 **Phase 2**，在不引入数据库迁移、不修改已发布脚本版本（ADR-0051）的前提下打通前端自助建单闭环：

1. **补齐并规范化内置 Pipeline 模板（`backend/schemas/pipeline_templates/*.json` + `backend/api/routes/pipeline.py`）**：
   - 补齐 `mtbf.json` 的完整标准步骤（`mtbf_setup@1.4.2` / `mtbf_check@1.5.0` / `mtbf_finish@1.6.0`、`timeout_seconds: 604800`、`patrol.interval_seconds: 300`），并同步加入 `tests/test_pipeline_template_script_pins_2865.py`。
   - 为全部 11 套内置模板建立显式元数据（`display_name`、`specialty_key`、`status: ready | skeleton`、`requires_suite`、`plan_defaults`），全部映射到现网 6 个 `specialty` 字典键（含 `power-cycle → powercycle`、`monkey → [monkey, monkey_watcher_patrol]`），其中 `ddr` / `standby` 显式标为 `skeleton`，`monkey_watcher_patrol` 声明 `plan_defaults.watcher_policy = {enabled: true}`。
   - `GET /api/v1/pipeline/templates` 扩展上述元数据与可选 `specialty_key` 过滤，且保持 `pipeline_def` 只含 `{"lifecycle": ...}`（不破坏既有校验契约）。
2. **脚本轻量分类标签（`backend/services/script_tags.py` + `ScriptOut.tags`）**：
   - 采用与 ADR-0060 D2 `param_docs` 相同的外置登记模式，为脚本族提供 `role` / `specialties` / `platforms` 轻标签，零数据库迁移、零脚本发版开销，先服务于前端 `PlanStepInspector` 脚本下拉筛选。
3. **前端自助建单闭环（`TemplatePickerDialog` + `PlanListPage` + `PlanEditPage` + `PlanCanvas` + `PlanStepInspector`）**：
   - `PlanListPage` 新增「从模板创建」弹窗（支持按专项与关键字筛选、展示步骤数 / 需绑定套件 / Watcher / 骨架标识，选中跳转 `/orchestration/plans/new?template=<name>`）与行内「复制为草稿」入口（跳转 `/orchestration/plans/new?clone=<id>`）。
   - `PlanEditPage` / `usePlanEditForm` 支持 URL 种子展开（`?template=` 与 `?clone=`）及编辑页内「从模板导入」（新建态，有未保存改动时二次确认）与「复制为草稿」（编辑态）；克隆草稿保留步骤、参数、项目/专项/套件与计划级设置，刻意清空 `next_plan_id` 与乐观锁令牌，走独立 `POST /api/v1/plans`。
   - `PlanCanvas` 在检测到启用中的 `mtbf_*` 步骤且未绑定套件时展示行内提醒，衔接 ADR-0030 v1.8 套件门禁；`PlanStepInspector` 提供基于 `ScriptEntry.tags` 的筛选标签（全部 / 推荐 / 公共前置 / 当前专项 / 运维刷机）。

## Alternatives

1. **在数据库新建 `pipeline_template` 表并做后台模板编辑器**：首期拒绝。内置模板依赖代码评审与 `test_pipeline_template_script_pins_2865.py` 版本钉死守卫，Git 托管 JSON 天然保证多站点一致与可回溯。
2. **把脚本轻标签写进 `script` 表列或 `capabilities.json`**：首期拒绝。改脚本目录会触发 ADR-0051 发版流程，加表列需数据迁移；先以外置只读登记表服务 UI 筛选，成本最低且随时可演进。
3. **后端新增 `POST /api/v1/plans/{id}/clone` 直接落库**：拒绝。直接落库会制造未命名的脏副本，且无法在落库前改名/调参；改为前端「复制为草稿」复用既有 `POST /api/v1/plans` 与 ADR-0060 草稿投影更安全直观。

## Verification

- 后端模板、脚本版本 pin 与编辑器空模板守卫：
  - `.venv/bin/python scripts/run_pytest.py backend/tests/api/test_pipeline_templates_stages.py tests/test_pipeline_template_script_pins_2865.py tests/test_plan_editor_empty_lifecycle_3109.py -q` → `21 passed`
- 前端类型、定向单测与 Lint：
  - `npm --prefix frontend run type-check` → 通过
  - `(cd frontend && npx vitest run src/pages/orchestration/PlanListPage.test.tsx src/pages/orchestration/PlanEditPage.test.tsx src/components/pipeline/PlanCanvas.test.tsx src/components/pipeline/PlanStepInspector.test.tsx)` → `4 passed, 123 passed`
  - `(cd frontend && npx eslint <touched files> --max-warnings 0)` → 通过
- 仓级门禁：
  - `.venv/bin/python scripts/run_gates.py check:quick` → `16 gates` 全部通过（含 `ruff`、`eslint`、`type-check`、`knip`、`compileall`、`layering`、`tool-manifest`、`check_script_packages` 等）
- 实机 UI 预览与真实浏览器交互巡检：待部署到隔离预览或合并后实机核验（pending）。

## Revisit

- 待 Phase 1 真机取证完成并落地 `log_prepare` 后，将 `log_prepare` 接入 9 套稳定专项模板，并将 GPU 等专项容量差异通过模板步骤参数表达。
- 若后续为 `aging_ddr` / `ddr` / `standby` 新增独立 `specialty` 字典种子并补齐 `ddr` / `standby` 业务步骤，同步更新对应模板的 `specialty_key` 与 `status: "ready"`。
- 进入 Phase 3（ADR-0029 D4 最小复议）引入 `Project` 级 `log_profile` 预设时，需在 ADR 中明确预设如何进入 ADR-0060 D5 确认指纹与 `plan_snapshot` 冻结。
