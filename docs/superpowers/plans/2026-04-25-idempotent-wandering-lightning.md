# Idempotent Wandering Lightning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按任务逐项实现。步骤使用 checkbox 语法跟踪。

**目标：** 将 Workflow 级 setup/teardown、脚本版本目录、Agent 脚本执行、设备级幂等准备步骤落到现有稳定性测试平台。

**架构：** 平台端新增 `script` 元数据表和 `/api/v1/scripts` API，扫描 NFS `scripts/{category}/{name}/v{x.y.z}/` 后注册不可变版本。Dispatch 阶段把 `WorkflowDefinition.setup_pipeline`、`TaskTemplate.pipeline_def`、`WorkflowDefinition.teardown_pipeline` 解析成每个 `JobInstance.pipeline_def`。Agent 侧通过 SQLite 缓存 `ScriptRegistry`，`PipelineEngine` 解析并执行 `script:<name>`，同时支持 `StepResult.skipped`。

**Tech Stack：** FastAPI、SQLAlchemy、Alembic、Pydantic、jsonschema、Python Agent、SQLite WAL、requests、subprocess、React、TypeScript、Vite。

---

## 设计修正

- 原文 `_resolve_pipeline()` 忽略 `TaskTemplate.prepare`，会破坏旧 Workflow。实现时采用：有 workflow setup 时 `prepare = workflow.setup.prepare + task.prepare`；无 workflow setup 时结果必须与当前行为一致。
- 原文 `push_resources` 用远端 `manifest.json` 文件 sha 比较 `bundle_sha256` 不成立。实现时用远端 marker 文件 `${remote_dir}/.stp_bundle_sha256` 比对 `manifest["bundle_sha256"]`，并在推送前校验本地 bundle sha。
- 当前 Agent 实际主机心跳走 `/api/v1/heartbeat`，`/api/v1/agent/heartbeat` 基本未被主循环调用。脚本 catalog 版本刷新先接入现有 `/api/v1/heartbeat` 链路，同时保留 `/api/v1/agent/heartbeat` 字段兼容。

## 文件结构

- 新建 `backend/models/script.py`：`Script` ORM。
- 修改 `backend/models/__init__.py`、`backend/models/workflow.py`、`backend/models/host.py`：导出模型，新增 workflow setup/teardown 和 host script catalog version。
- 新建 `backend/alembic/versions/o3i4j5k6l7m8_add_scripts_and_workflow_setup.py`：建 `script` 表，给 `workflow_definition` 和 `host` 加字段。
- 新建 `backend/api/routes/scripts.py`：脚本 CRUD、分类、扫描。
- 新建 `backend/services/script_catalog.py`：NFS 扫描、sha256、不可变版本检测。
- 修改 `backend/main.py`：注册 scripts router。
- 修改 `backend/api/routes/orchestration.py`：Workflow create/update/out schema 支持 `setup_pipeline`、`teardown_pipeline`。
- 修改 `backend/services/dispatcher.py`：拼接 workflow/task pipeline，校验 `tool:` 与 `script:` 引用。
- 修改 `backend/schemas/pipeline_schema.json`、`backend/core/pipeline_validator.py`、`backend/api/routes/action_templates.py`：允许 `script:`，`tool:` 和 `script:` 均要求 `version`。
- 新建 `backend/agent/registry/script_registry.py`，修改 `backend/agent/registry/local_db.py`：Agent 脚本 catalog 缓存。
- 修改 `backend/agent/pipeline_engine.py`、`backend/agent/pipeline_runner.py`、`backend/agent/job_runner.py`、`backend/agent/main.py`：传入并执行 `script:` action。
- 修改 `backend/agent/heartbeat.py`、`backend/agent/heartbeat_thread.py`、`backend/api/schemas/host.py`、`backend/api/routes/heartbeat.py`、`backend/api/routes/agent_api.py`：script catalog version 上报和过期响应。
- 修改 `backend/agent/actions/device_actions.py`：`connect_wifi`、`install_apk`、`push_resources` 幂等。
- 修改 `frontend/src/utils/api/types.ts`、`frontend/src/utils/api/tools.ts`、`frontend/src/utils/api/index.ts`、`frontend/src/utils/api/orchestration.ts`：类型和 scripts API。
- 修改 `frontend/src/components/pipeline/StagesPipelineEditor.tsx`、`frontend/src/pages/orchestration/WorkflowDefinitionEditPage.tsx`：支持 Workflow setup/teardown 和 `script:` action。

## Task 1：Script 数据模型、迁移、API

**测试先行：**
- 新建 `backend/tests/api/test_scripts.py`
- 覆盖：
  - `POST /api/v1/scripts` 可创建脚本，重复 `name+version` 返回 409。
  - `DELETE /api/v1/scripts/{id}` 只软删除 `is_active=false`。
  - `POST /api/v1/scripts/scan` 扫描临时目录后注册 `device/connect_wifi/v1.0.0/connect_wifi.sh`。
  - 同版本内容变化返回 `conflicts`，不覆盖 DB。
  - NFS 删除后再次 scan 将对应版本置为 inactive。

**实现步骤：**
- 在 `backend/models/script.py` 定义 `Script`，字段与原文一致，`nfs_path` 用 `Text`，`param_schema` 用 `JSONB`，加 `UniqueConstraint("name", "version")`。
- 在迁移中创建 `script` 表、索引 `idx_script_active_name`、`idx_script_category`。
- 在 `backend/services/script_catalog.py` 实现：
  - `detect_script_type(path)`：`.py -> python`、`.sh -> shell`、`.bat/.cmd -> bat`。
  - `sha256_file(path)`：按 1MB chunk 读取。
  - `scan_script_root(db, root)`：遍历 `{category}/{name}/v{semver}/entry`，返回 `{created, skipped, deactivated, conflicts}`。
- 在 `backend/api/routes/scripts.py` 实现 `GET/POST/PUT/DELETE /api/v1/scripts`、`GET /categories`、`POST /scan`。
- 在 `backend/main.py` 引入并 `include_router(scripts_router)`。

**验证命令：**
- `pytest backend/tests/api/test_scripts.py -v`
- `alembic upgrade head`

## Task 2：Workflow 级 setup/teardown 与调度拼接

**测试先行：**
- 新建 `backend/tests/services/test_dispatcher_setup_pipeline.py`
- 用一个 WorkflowDefinition 设置：
  - `setup_pipeline.stages.prepare = [setup_wifi]`
  - TaskTemplate `prepare=[task_prepare] execute=[task_execute] post_process=[task_post]`
  - `teardown_pipeline.stages.post_process = [cleanup]`
- Dispatch 后断言 JobInstance.pipeline_def：
  - `prepare == [setup_wifi, task_prepare]`
  - `execute == [task_execute]`
  - `post_process == [task_post, cleanup]`
- 再测 setup/teardown 为 null 时，JobInstance.pipeline_def 与原 TaskTemplate.pipeline_def 完全相等。

**实现步骤：**
- `backend/models/workflow.py` 增加：
  - `setup_pipeline = Column(JSONB, nullable=True)`
  - `teardown_pipeline = Column(JSONB, nullable=True)`
- `backend/api/routes/orchestration.py`：
  - `WorkflowDefCreate/Update/Out` 增加 `setup_pipeline`、`teardown_pipeline`。
  - 新增 `_validate_optional_pipeline(name, pipeline_def)`，仅非 null 时调用 `validate_pipeline_def()`。
  - create/update 写入并返回两个字段。
- `backend/services/dispatcher.py`：
  - 新增 `_resolve_pipeline(setup, task, teardown)`。
  - dispatch 创建 JobInstance 时写入 resolved pipeline。
  - `_validate_tool_references()` 改名 `_validate_pipeline_references()`，校验 resolved pipeline，而不是只校验 TaskTemplate 原始 pipeline。

**验证命令：**
- `pytest backend/tests/services/test_dispatcher_setup_pipeline.py -v`
- `pytest backend/tests/api/test_workflows.py -v`

## Task 3：Pipeline Schema 与 script 引用校验

**测试先行：**
- 扩展 `backend/core/test_pipeline_validator.py`：
  - `script:push_bundle` 带 `version` 时合法。
  - `script:push_bundle` 不带 `version` 时非法。
  - `builtin:check_device` 仍不要求 `version`。
- 扩展 `backend/api/routes/test_orchestration_pipeline_validation.py`：create workflow 接受 `script:` schema。

**实现步骤：**
- `backend/schemas/pipeline_schema.json`：
  - action pattern 改为 `^(tool:\\d+|builtin:.+|script:.+)$`
  - 条件改为 `^(tool:|script:)` 时 required `version`。
- `backend/api/routes/action_templates.py`：`ACTION_PATTERN` 改为 `r"^(tool:\d+|builtin:.+|script:.+)$"`，错误信息同步。
- `backend/services/dispatcher.py`：
  - 收集 `script:` 的 `(name, version)`。
  - 查询 `Script.name/version/is_active`，缺失时报 `Scripts not found or inactive: [...]`。

**验证命令：**
- `pytest backend/core/test_pipeline_validator.py backend/api/routes/test_orchestration_pipeline_validation.py -v`

## Task 4：Agent ScriptRegistry 与 `script:` 执行

**测试先行：**
- 新建 `backend/agent/tests/test_script_registry.py`
  - server load 成功时写入 SQLite。
  - server 失败时从 SQLite 回退。
  - `resolve("push_bundle", "2.0.0")` 返回匹配 entry。
- 新建 `backend/agent/tests/test_pipeline_engine_script_action.py`
  - 临时 Python 脚本读取 `STP_STEP_PARAMS` 并输出 `{"metrics": {"ok": 1}}`。
  - `PipelineEngine` 执行 `script:echo_params` 成功。
  - 脚本 stdout 输出 `{"skipped": true, "skip_reason": "already done"}` 时 StepTrace status 为 `SKIPPED`，且不重试。
  - timeout 返回 `exit_code=124`。

**实现步骤：**
- `backend/agent/registry/local_db.py` 增加 `script_cache` 表和 `save/load/update_script_cache()`。
- `backend/agent/registry/script_registry.py` 复用 ToolRegistry 模式：
  - `GET /api/v1/scripts?is_active=true`
  - cache key 使用 `name::version`
  - catalog version 使用 sorted `(name, version, content_sha256)` 的 md5 前 12 位。
- `backend/agent/pipeline_engine.py`：
  - `StepContext` 增加只读信息：`log_dir: str = ""`、`adb_path: str = ""`、`nfs_root: str = ""`。
  - `StepResult` 增加 `skipped: bool = False`、`skip_reason: str = ""`。
  - `__init__` 增加 `script_registry=None`、`nfs_root=os.getenv("STP_NFS_ROOT", "")`。
  - `_resolve_action_stages()` 增加 `script:` 分支。
  - `_run_script_action(ctx, step)` 用 subprocess 执行，环境变量包含 `STP_DEVICE_SERIAL`、`STP_ADB_PATH`、`STP_LOG_DIR`、`STP_STEP_PARAMS`、`STP_NFS_ROOT`、`STP_JOB_ID`。
  - stdout JSON 支持 `metrics`、`skipped`、`skip_reason`。
  - `_run_step_with_retry_stages()` 遇到 skipped 直接成功返回。
  - `_report_step_trace_mq()` 增加 `output` 参数，skipped 时 `status="SKIPPED"`、`output=skip_reason`。
- `pipeline_runner.py`、`job_runner.py`、`main.py` 逐层传入 `script_registry`。

**验证命令：**
- `pytest backend/agent/tests/test_script_registry.py backend/agent/tests/test_pipeline_engine_script_action.py -v`

## Task 5：心跳同步 script catalog version

**测试先行：**
- 扩展 `backend/tests/api/test_heartbeat.py`：
  - payload 带 `script_catalog_version` 时写入 `Host.script_catalog_version`。
  - 若 Host 已有旧 version，响应包含 `script_catalog_outdated=true`。
- 扩展 Agent 单测：
  - `send_heartbeat(..., script_catalog_version="abc")` payload 包含字段。
  - HeartbeatThread 收到 `script_catalog_outdated=true` 后调用 refresh callback。

**实现步骤：**
- `backend/models/host.py` 增加 `script_catalog_version = Column(String(64))`。
- `backend/api/schemas/host.py` 的 `HeartbeatIn` 增加 `tool_catalog_version: str = ""`、`script_catalog_version: str = ""`。
- `backend/api/routes/heartbeat.py`：
  - 计算 `tool_catalog_outdated`、`script_catalog_outdated`。
  - 更新 host 两个 version 字段。
  - 响应保留 `ok/host_id/devices_count`，新增两个 outdated flag。
- `backend/api/routes/agent_api.py` 同步增加 `script_catalog_version` 和 `script_catalog_outdated`，避免已有 Agent API 契约分叉。
- `backend/agent/heartbeat.py` 增加可选参数 `tool_catalog_version`、`script_catalog_version`。
- `backend/agent/heartbeat_thread.py` 增加 `catalog_versions` callable 和 `on_scripts_outdated` callable。
- `backend/agent/main.py` 把 `tool_registry.version`、`script_registry.version` 传给 HeartbeatThread；收到过期后调用 `script_registry.initialize()`。

**验证命令：**
- `pytest backend/tests/api/test_heartbeat.py backend/agent/tests/test_main.py -v`

## Task 6：幂等 builtin actions

**测试先行：**
- 扩展 `backend/agent/tests/test_actions_aee_migration.py` 或新建 `backend/agent/tests/test_idempotent_device_actions.py`：
  - `connect_wifi` 当前 ssid 已匹配时返回 `StepResult(success=True, skipped=True)`。
  - `install_apk` 当前包版本已匹配时返回 skipped，不调用 install。
  - `push_resources` legacy `files` 模式保持现有行为。
  - `push_resources` bundle 模式 marker 匹配时 skipped。
  - bundle sha 与 manifest 不一致时失败。

**实现步骤：**
- `connect_wifi`：
  - 先执行 `dumpsys wifi | grep -E "mWifiInfo|SSID"` 或 `cmd -w wifi status`，命中 ssid 则 skipped。
  - 检查失败不阻断，继续原连接逻辑。
- `install_apk`：
  - 参数支持 `pkg_name`、`required_version`。
  - `required_version` 命中 `dumpsys package <pkg> | grep versionName` 时 skipped。
  - 未给 `pkg_name` 时保持原行为。
- `push_resources`：
  - 若 params 含 `bundle` 和 `manifest`，走 bundle 模式；否则保持 legacy `files` 模式。
  - 本地读取 JSON manifest，校验 `sha256(bundle) == manifest["bundle_sha256"]`。
  - 远端 marker `${remote_dir}/.stp_bundle_sha256` 等于目标 sha 时 skipped。
  - 推送到 `${remote_dir}/.stp_tmp_bundle.tar.gz`，解压成功后写 marker。

**验证命令：**
- `pytest backend/agent/tests/test_idempotent_device_actions.py -v`
- `pytest backend/agent/tests/test_pipeline_templates.py -v`

## Task 7：前端 Workflow setup/teardown 与 script action

**测试先行：**
- 新建或扩展 `frontend/src/components/pipeline/StagesPipelineEditor.test.tsx`：
  - `scriptOptions` 存在时 action 类型下拉展示 `script:`。
  - 选择 script 后写入 `action="script:<name>"` 和最新 `version`。
  - script 的 `param_schema` 使用 `DynamicToolForm`。
- 手动验收 Workflow 编辑页：
  - 基本信息、Setup Pipeline、Task Pipeline、Teardown Pipeline 三块保存后 payload 正确。

**实现步骤：**
- `types.ts`：
  - 新增 `ScriptEntry`。
  - `WorkflowDefinition` 和 `WorkflowDefinitionCreate` 增加 `setup_pipeline?: PipelineDef | null`、`teardown_pipeline?: PipelineDef | null`。
- `tools.ts` 增加 `scripts` API namespace：`list/create/update/remove/scan/listCategories`。
- `index.ts` 导出 `scripts` 和 `ScriptEntry`。
- `orchestration.ts` update payload 类型允许 setup/teardown。
- `StagesPipelineEditor.tsx`：
  - `ActionType = 'builtin' | 'tool' | 'script'`。
  - `scriptOptions` 含 `name/version/category/param_schema/is_active`。
  - `getActionMeta()` 识别 `script:`。
  - 下拉按 category 分组展示 script。
  - script param schema 复用 `DynamicToolForm`。
- `WorkflowDefinitionEditPage.tsx`：
  - 本地 state 拆成 `setupPipeline`、`taskPipeline`、`teardownPipeline`。
  - setup 编辑器只启用 prepare，teardown 编辑器只启用 post_process；若 StagesPipelineEditor 不支持 stage 过滤，则先新增 `allowedStages` prop。
  - 保存 payload 包含 `setup_pipeline`、`teardown_pipeline`，空 pipeline 统一发 `null`。
  - 摘要统计区分别显示 setup/task/teardown step 数。

**验证命令：**
- `cd frontend; npm run type-check`
- `cd frontend; npm test -- StagesPipelineEditor`

## Task 8：端到端验证

- 准备临时 NFS 目录：
  - `scripts/resource/push_bundle/v2.0.0/push_bundle.py`
  - `scripts/device/connect_wifi/v1.0.0/connect_wifi.sh`
- 执行扫描：`POST /api/v1/scripts/scan`，确认两条 active script。
- 创建 WorkflowDefinition：
  - `setup_pipeline.prepare = [builtin:connect_wifi, builtin:install_apk, script:push_bundle]`
  - TaskTemplate 只含 execute。
  - `teardown_pipeline.post_process = [builtin:adb_pull]`
- Dispatch 到一台测试设备。
- 首次运行确认 setup 执行、execute 执行、teardown 执行。
- 第二次运行确认 setup 中已满足状态返回 `SKIPPED`，execute 仍执行。

**总验证命令：**
- `pytest backend/core/test_pipeline_validator.py backend/tests/api/test_scripts.py backend/tests/services/test_dispatcher_setup_pipeline.py backend/agent/tests/test_script_registry.py backend/agent/tests/test_pipeline_engine_script_action.py backend/agent/tests/test_idempotent_device_actions.py -v`
- `cd frontend; npm run type-check`
- `cd frontend; npm test`

## 风险与边界

- 本计划不实现 `/admin/scripts` 管理页面；脚本 CRUD API 和 Pipeline 编辑器 script 选择已足够支撑主流程，管理页可后续单独做。
- 本计划不迁移历史 TaskTemplate 的 prepare 内容；只保证新调度拼接兼容旧数据。
- NFS 目录扫描默认读取 `STP_SCRIPT_ROOT`，未设置时使用 `STP_NFS_ROOT/scripts`。生产部署必须显式配置，避免 Windows 后端扫描 Linux 路径失败。
- 同版本脚本内容变化不自动覆盖，这是版本不可变原则；scan 只返回 conflict 给运维处理。
- 不主动 git commit；每个 Task 完成后由执行者汇报变更和验证结果，用户明确要求时再提交。
