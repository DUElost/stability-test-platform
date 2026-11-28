# ADR-0067：项目级日志预设（`TestProject.log_profile`）与五层参数合并序

- 状态：**Accepted** v1.0（2026-11-28 经 PR #4214 第 2 轮权威独立复核通过（`41810324e`）并由 Owner 同日裁决接受——采纳 D1–D6 全文，§7 终裁维持最小边界（首批仅 `log_profile`，`timezone` 继续由 `sync_device_time` 显式配置；`#3823` 与 `#4042` 继续外置承接）；归属域 `project-taxonomy`；按 ADR-0058 批次待实施）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-11-27
- 版本记录：
  - v1.0（2026-11-28）：PR #4214 第 2 轮权威独立复核通过（`41810324e`），Owner 同日裁决转 `Accepted`（§7 终裁确认首批仅 `log_profile`、`timezone` 留于 `sync_device_time`、`#3823`/`#4042` 继续由各自关联单外置承接）；同步吸收第 2 轮两项非阻塞措辞收紧（D2.6 明确 `scan` 永不覆写已有 `default_params` 且 `changed_from_empty` 补齐包校验字段，U2 共享夹具钉死 `param_schema` 容量上限），并在同 PR 完成 §6 传播清单（ADR-0029 v2.6、ADR-0060 v1.2、`docs/design/2026-semantic-ownership.md` 与 `docs/adr/README.md`）。
  - v0.2（2026-11-28）：按 PR #4214 第 1 轮权威独立复核（`07bea96d`）全面收口 5 项 P1/P2 阻塞项与 1 项非阻塞建议：① D3/D4 统一预览与 `prepare_plan_run` 的 `#2649` `serial_conflict` 有效目标集口径，区分请求设备集与判定设备集并保持 `PlanRunTrigger.device_ids` `min_length=1`（`pending_devices` 仅限无设备静态投影）；② 纠偏 §1.2.3 与 D2.5——`script_catalog` 扫描新建写入空 `param_schema`/`default_params`，改由新版本 `log_prepare@1.1.0` + Alembic 种子迁移建立可冻结控制面默认后再清理 9 套常规模板冗余 `4096`，明确 U2→U3 激活序与存量 Plan/复制草稿保留语义；③ 校准 D1.2 `mobilelog_size_mb` 下限至 `256`（与 `log_prepare.py` 一致），规定 `ylog_required_streams` 必含 `{"android", "kernel"}` 且仅允许追加已实证子流（`uboot`、`lastlog`），堵死 A57 静默漏采回退口；④ D2 细化第 5 层各消费方（WiFi / 套件 / ADR-0066）缺键/`null`/`""`/`0`/`false` 填空契约与 `#975` 冲突硬拒优先序；⑤ D3.4 补齐服务端 `DraftParameterProjectionIn.project_key` 严格 schema 契约与前端防旧响应覆盖规则；⑥ D4/D5 明确单次解析复用与归属判定冻结时点（`READ COMMITTED` 读快照边界），并新增 §7 登记 `timezone` 待 Owner 终裁项及 `#3823`/`#4042` 外置边界。
  - v0.1（2026-11-27）：初稿，基于 `#4182` Owner 确认决策基线、Phase 2（PR #4191）与 Phase 1（PR #4209 / PR #4211）生产实证及代码基线 `origin/main@cccb0d6a0` 起草。
- 决策者：Owner（2026-11-28 裁决接受）；起草：Antigravity（本地工作面）
- 标签：项目域, 日志配置, 参数分层, 确认指纹, 投影
- 归属域：semantic-ownership project-taxonomy
- 关联：
  [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)（PlanStep 与参数约束）；
  [ADR-0023](./ADR-0023-script-traceability.md)（脚本溯源与 `plan_snapshot` 不可变快照）；
  [ADR-0029](./ADR-0029-project-taxonomy-and-param-layering.md)（项目分类域与挂起中的 D4 三层参数、D5 派发门禁、v2.5 D10 派生归属）；
  [ADR-0030](./ADR-0030-multi-case-suite-management.md)（托管套件派发期参数注入）；
  [ADR-0051](./ADR-0051-release-unit-and-content-addressing.md)（发布单元与脚本版本 `default_params` 不可变）；
  [ADR-0058](./ADR-0058-planned-batch-execution.md)（批次交付流程）；
  [ADR-0059](./ADR-0059-contract-and-sop-writing-conventions.md)（契约写作约定）；
  [ADR-0060](./ADR-0060-plan-parameter-meaning-and-effective-values.md)（计划参数四层投影、显示状态与 D5 确认指纹）；
  [ADR-0066](./ADR-0066-unified-network-supply-and-usb-net.md)（统一网络供给模块，D1.6 明确引用本 ADR 维护的唯一五层合并序）；
  `#4181`（展锐 Full Sysdump 独立由 `unisoc_sysdump_config@1.0.0` 管理）、`#4182`（日志开启前置统一化与项目/专项/脚本/计划职责边界演进）、`#3823`（`AutoTestTool.apk` 外置路由）、`#4042`（`version_prop` 归不可变固件包）；
  `backend/models/project.py`、`backend/models/project_model.py`、`backend/models/plan.py`、`backend/services/project_registry.py`、`backend/services/project_attribution.py`、`backend/services/project_mapping.py`、`backend/services/script_catalog.py`、`backend/services/script_seed_governance.py`、`backend/services/script_params.py`、`backend/services/plan_dispatcher_core.py`、`backend/services/plan_dispatcher_sync.py`、`backend/services/plan_confirmation.py`、`backend/services/plan_parameter_projection.py`、`backend/api/routes/plans.py`、`backend/api/schemas/project.py`、`backend/api/schemas/plan_parameter_projection.py`、`backend/agent/scripts/log_prepare/log_prepare.py`、`backend/schemas/pipeline_templates/monkey.json`、`backend/schemas/pipeline_templates/gpu.json`、`frontend/src/pages/execution/PlanExecutePage.tsx`、`frontend/src/pages/orchestration/usePlanEditForm.ts`

---

## 1. 背景与实证事实（基线 `origin/main@cccb0d6a0`）

### 1.1 问题本质与 ADR-0029 D4 复议触发

[ADR-0029](./ADR-0029-project-taxonomy-and-param-layering.md) v2（2026-08-18）曾将 D4（`test_project.variables` + `${project.x}` 三层参数解析）挂起，原因是当时的核心动机（按项目切换 MTBF 用例 APK）已由脚本端设备指纹路由吸收；同时 ADR-0029 D4 明确写下了复议触发条件：**「出现项目级非 APK 参数需求」**。

`#4182` Phase 1 的真机取证（2026-11-27）证实，设备持续日志开启存在无法仅凭芯片厂商或设备型号推断的**项目/客户级配置差异**：

| 维度 | 实测基线与差异 | 来源 |
|---|---|---|
| 荣耀 MTK（`MLD_NX2/NX3`、`ELA_LX2/LX3`） | 持续日志走 `com.debug.loggerui` / `mobile_log_d`，目标总容量 4096 MB；部分专项按荣耀基线需设置 `persist.vendor.mtk.aee.mode=3`（设备出厂 `aeev.mode=4`） | `#4182` Phase 1 实机取证 |
| 传音 MTK（`INFINIX_X6851`） | 同样走 `com.debug.loggerui`，但出厂 `mobilelog_logsize=1000`、`persist.vendor.mtk.aee.mode=3` / `aeev.mode=3`，容量基线与部分子日志开关不同于荣耀 | `#4182` Phase 1 实机取证 |
| 展锐 UNISOC（`P525AA` `MLY_LX2/LX3` vs `A57` `Z2581`） | 持续日志统一走 `/product/bin/ylogctl`（YLog 5.x）；实测 `ylogctl query` / `/data/ylog/ylog.conf` 中的核心日志子流为 `android`、`kernel`、`uboot`、`lastlog`（其中 `A57 Z2581` 存在 `status=enable` 但 `android`/`kernel` 保持 `Close` 的静默漏采坑，而 `sysinfo`/`sgm`/`trustlog` 仍会使总文件体积增长造成假阳性） | `#4182` Phase 1 实机取证（`#7211180563`） |
| 专项级例外（GPU 等） | GPU 压测需 10240 MB 持续日志容量，显著高于常规专项（4096 MB）；此类差异属于专项维度而非项目维度，已在模板 `gpu.json` 的步骤参数中显式表达 | PR #4211（生产已生效） |

目前 `#4182` 的前两阶段均已在生产生效：

- **Phase 2（PR #4191）**：建立「从模板创建 Plan」「复制为草稿」「脚本轻标签筛选」与 `specialty_key → template_id` 显式映射；
- **Phase 1（PR #4209 + PR #4211）**：上线统一持续日志脚本 `log_prepare@1.0.0`（按芯片能力内部分流，不碰 Full Sysdump），并将其按「清理后置、GPU 10240M、其余 4096M」接入全部 10 套稳定性模板。

在四层职责边界（`Script` 原子能力 → `Specialty` 模板与专项默认 → `Project` 经验证的项目默认 → `Plan` 显式步骤与覆写）下，当前仍缺少 `Project` 层的结构化日志默认承载：若不引入项目预设，运营与测试人员在为不同客户/项目复用同一专项模板时，仍须逐个 Plan 手动改写 `aee_mode`、`mtk_sublog_cmd`、`ylog_required_streams` 或容量阈值；而若直接复活 ADR-0029 D4 自由字典 `variables` 与 `${project.x}` 模板替换，又会引入无 schema 约束、自由键蔓延与跨项目静默串扰的风险。

### 1.2 既有代码约束与关键接缝

1. **项目归属派生化（ADR-0029 v2.5 D10）**：设备归属由 `device.model ⋈ project_model`（`is_active = TRUE`）实时派生（`backend/services/project_attribution.py`）；`plan.project_id` 可空（`NULL` 表示「不限」）；`prepare_plan_run` 中 `_infer_frozen_project_id` 在 `plan.project_id IS NULL` 时仅按众数推断报表维度 `plan_run.project_id`，不作派发阻断（D5 挂起）。
2. **现行参数合并与物化注入单点（`script_params.py` / `plan_dispatcher_core.py`）**：
   - 静态层仅合并三层 `schema.default < default_params < step.params`；
   - 物化期（`materialize_jobs_and_allocations`）叠加 `inject_wifi_params`（及 ADR-0066 统一网络供给注入）与 `inject_suite_params`（ADR-0030）：现网两处注入均采用 Python 真值判空（`if not params.get(key):`），会对缺键、`None`、`""` 以及 `expected_testpoint_count=0` 执行填空；同时 `inject_suite_params` 先于填空执行 `#975` `project` 冲突硬拒。
3. **控制面脚本默认缺失与模板覆写冲突面**：
   - 核实 `backend/services/script_catalog.py`（扫描新建脚本行路径）确认：`POST /api/v1/scripts/scan` 新建 `script` 行时固定写入 `param_schema={}` 与 `default_params={}`；当前已发布的 `log_prepare@1.0.0` 在控制面 `script` 表中无 `default_params`，`4096` 仅存在于 `log_prepare.py` 的运行时回落（`_as_int(..., 4096)`）及 9 套常规模板的 `step.params` 中。
   - 同时按 `AGENTS.md` 硬不变量，已存在脚本版本 `log_prepare@1.0.0` 的 `default_params` 不可原地修改。因此若直接删去 9 套常规模板的 `4096`，新站或无预设/混跑回退场景下 L1/L2/L3 投影会从显式 `4096` 退化为缺键；必须先按仓库既有的新版本 + Alembic 种子迁移范式（`backend/services/script_seed_governance.py`）在控制面建立可版本化、可冻结的 `log_prepare@1.1.0` 默认值，再把模板切换至 `1.1.0` 并清理常规模板冗余键（保留 `gpu.json` 的显式 `10240`）。
4. **脚本运行时边界（`log_prepare.py`）**：
   - MTK 容量参数在 `_prepare_mtk` 中以 `minimum=256, maximum=65536` 钳制（`log_prepare.py:359`）；若控制面白名单放行 `64..255`，会导致确认/快照值与设备实值分裂；
   - 展锐必需子流在 `_prepare_unisoc` 中由 `_as_stream_list(cfg.get("ylog_required_streams"), ("android", "kernel"))` **整表替换**默认元组（`log_prepare.py:680`）；若控制面白名单允许省略 `android` 或 `kernel`（如 `["sysinfo"]`），会直接重开 A57 静默漏采路径。
5. **预览/派发设备集与前端校验（`plan_dispatcher_sync.py` / `plans.py` / `PlanExecutePage.tsx`）**：
   - `PlanRunTrigger.device_ids`（`plans.py:241`，同时用于 preview 与 trigger）强制 `min_length=1`；
   - `PlanExecutePage.tsx:756-763` 要求 `preview.device_ids` 与前端提交的原始 `frozenDeviceIds` 完全一致；
   - `prepare_plan_run` 会按 `#2649` 剔除 `serial_conflict` 设备并单独物化为 `FAILED` job（`plan_dispatcher_sync.py:602-617`）；因此预览与派发必须复用同一有效判定集口径，并区分「请求设备集」与「参与预设判定的设备集」。
6. **草稿投影请求契约（`plans.py` / `usePlanEditForm.ts`）**：
   - `DraftParameterProjectionIn`（`plans.py:224-235`）设置了 `ConfigDict(extra="forbid")`，当前不含 `project_key`；编辑器 `usePlanEditForm.ts` 持有 `projectKey` 但未随草稿投影请求发送。
7. **确认指纹、并发锁与归属修改面（`plan_confirmation.py` / `project_mapping.py` / `heartbeat.py`）**：
   - `load_plan_graph_for_confirmation` 当前仅对 `plan` 取 PostgreSQL `FOR SHARE`；`project_mapping.py` 的型号解绑直接删除 `project_model` 行，`heartbeat.py` 可更新 `device.model`。
8. **跨 ADR 协同（ADR-0066 D1.6）**：ADR-0066 已明确网络供给池与测试项目正交，并约定派发期参数注入遵循本 ADR 在 `plan_dispatcher_core.py` 维护的唯一五层合并序。

---

## 2. 决策

> 以下 D1–D6 节规则句遵循 [ADR-0059](./ADR-0059-contract-and-sop-writing-conventions.md) 写作约定：仅使用「必须 / 不得 / 建议 / 可以」，并明确执行者。

### D1 结构化项目日志预设 `TestProject.log_profile`（对 ADR-0029 D4 的最小收敛修订）

1. 平台必须通过在 `test_project` 表新增受控结构化列 `log_profile`（`JSONB`，可空，默认 `NULL`）与单调递增版本号 `log_profile_version`（`INTEGER`，非空，默认 `0`）承载项目级日志默认配置，不得复活 ADR-0029 D4 原草案中的自由字典 `test_project.variables` 或 `${project.x}` 占位符替换机制。
2. `TestProject.log_profile` 在首期必须采用白名单强校验（Pydantic `ConfigDict(extra="forbid")`），并严格对齐已发布 `log_prepare` 脚本的取值与回读边界；只可以包含下表列出的 7 个参数键，未出现或值为 `null` 的键表示该项目不对该键施加项目默认：

   | 键名 | 类型与强校验约束 | 对应 `log_prepare` 参数与边界依据 |
   |---|---|---|
   | `mobilelog_size_mb` | 整数，`256 <= v <= 20480` | MTK `mobilelog` 总容量上限（MB）；下限 `256` 对齐 `log_prepare.py` 的 `minimum=256`，防止确认/快照值与脚本钳制结果分裂 |
   | `mtk_auto_start` | 布尔 | MTK `mobilelog` 开机自启开关 |
   | `mtk_sublog_cmd` | 字符串，匹配 `^[A-Za-z0-9_.-]{1,64}$` | MTK 可选子日志开关指令（如 `set_sublog_4_5_0`） |
   | `aee_mode` | 字符串，匹配 `^[0-9]{1,2}$` | MTK 可选 `persist.vendor.mtk.aee.mode` 目标值；未设则保留设备当前值 |
   | `ylog_size_mb` | 整数，`64 <= v <= 10240` | 展锐 `ylogctl aplogmaxsize` 容量上限（MB）；与 `log_prepare.py` 的 `[64, 10240]` 一致 |
   | `ylog_rotate` | 布尔 | 展锐 `ylogctl rotatelog` 循环覆盖开关 |
   | `ylog_required_streams` | 非空去重字符串列表，**必须同时包含 `"android"` 与 `"kernel"`**，且全部元素必须属于已实证子流白名单 `{"android", "kernel", "uboot", "lastlog"}` | 展锐 `ylogctl query` 必须处于 `Open` 态的子流清单；项目预设不得移除基础日志子流 `android`/`kernel` 或写入仅会触发假阳性的辅助流（如 `sysinfo`） |

3. 若未来需要在 `ylog_required_streams` 白名单中扩展新子流，维护者必须先完成真机 `ylogctl query` 回读与重启持久验证，再通过普通代码 PR 扩展白名单集合（无需数据库迁移），且任何配置均不得移除基础子流 `{"android", "kernel"}`。
4. 控制面不得将以下配置纳入首期 `log_profile`：
   - `timezone`（2026-11-28 Owner 终裁维持最小边界，继续由 `sync_device_time` 步骤显式配置，见 §7）；
   - `unisoc_sysdump_config` 开关与参数（继续由 `#4181` 独立步骤管理）；
   - 网络供给池与凭据（WiFi / `usb_net`，按 ADR-0066 D1.6 与项目保持正交）；
   - `#3823`（`AutoTestTool.apk` 路径）与 `#4042`（固件 `version_prop`）相关配置；
   - 任何敏感凭据、密钥、主机路径或未在白名单登记的自由键。
5. 控制面对 `source = 'SEED'` 或 `status = 'ARCHIVED'` 的 `TestProject` 行必须拒绝写入 `log_profile`（与 `project_registry.py` 现有守卫一致）。
6. 每次 `log_profile` 发生有效内容变更时，控制面必须在同一事务内将 `log_profile_version` 原子加 1、通过 `record_audit` 写入变更前后值与版本号审计日志，并在提交后广播 `emit_project_changed(project.id, "updated")`；未发生内容变化时，控制面不得推进 `log_profile_version`。
7. `TestProject.log_profile` 的写入权限必须与 `PUT /api/v1/projects/{project_key}` 保持一致（仅管理员可以写入，所有登录用户可以读取）。

### D2 唯一五层参数合并序与控制面默认版本化（统一 ADR-0029、ADR-0060 与 ADR-0066）

1. 控制面与前端草稿编辑器必须采用以下唯一五层参数合并序（自底向上），实施者不得在任何路由、预览或物化路径中另写第二套优先级规则：

   | 层次 | 来源标识（`ParamSource`） | 事实源 | 覆盖语义与生效范围 |
   |---|---|---|---|
   | 第 1 层 | `schema_default` | `Script.param_schema[k].default` | 最低优先级，适用于全部步骤 |
   | 第 2 层 | `script_default` | `Script.default_params[k]` | 覆盖第 1 层；已发布版本不可原地修改（ADR-0051） |
   | 第 3 层 | `project_preset` | `TestProject.log_profile[k]` | 覆盖第 1–2 层；**仅**作用于 `script:log_prepare` 步骤、**仅**注入非 `null` 键、且**仅**在满足 D3 全部准入条件时生效 |
   | 第 4 层 | `step_override` | `PlanStep.params[k]` | 覆盖第 1–3 层（含专项模板写入的专项例外，如 GPU `10240` MB）；显式存在于 `PlanStep.params` 中的键优先于第 1–3 层 |
   | 第 5 层 | `dispatch_injection` | 物化期动态注入（`inject_wifi_params` / ADR-0066 网络供给 / `inject_suite_params`） | 仅在物化期（L3）按第 5 条定义的分消费方填空规则执行，不得破坏既有派发行为或 `#975` 冲突硬拒 |

2. 在 `PlanStep.params`（第 4 层）中，针对 `log_prepare` 接受 `null` 值的可选参数（`aee_mode`、`mtk_sublog_cmd`），若用户显式写入 `"aee_mode": null`（键存在且值为 `null`），控制面必须将其视为显式步骤覆写，用于覆盖第 3 层 `project_preset` 并回退为「保留设备当前值」；对于非空类型参数（如整数容量、布尔开关、必需子流列表），保存期校验器必须继续拒绝显式 `null`。
3. 在第 3 层 `project_preset` 中，未出现或值为 `null` 的键必须跳过，控制面不得用 `project_preset` 中的 `null` 抹除第 1–2 层的脚本默认值。
4. 合并后的最终步骤参数必须继续通过 `validate_params_against_schema`（`backend/services/script_params.py`）校验；`merge_effective_params` 与 `build_lifecycle_from_steps` / `build_lifecycle_from_snapshot` 必须保持为后端唯一计算入口（ADR-0060 D1.2）。
5. **第 5 层（`dispatch_injection`）分消费方填空与冲突规则**（严格保持既有行为兼容，消除「空值」与「显式覆写」歧义）：
   - **`inject_wifi_params`（`connect_wifi`、`monkey_setup` 及 ADR-0066 网络供给兼容路径）**：对目标凭据键（`ssid`、`password` 及 `wifi.ssid`、`wifi.password`），当第 1–4 层合并结果为**缺键、`null` 或空字符串 `""`** 时执行填空（与现网 `not params.get(key)` 同构）；当合并结果为非空字符串时不得覆盖，来源保持原层（如 `step_override`）；不注入布尔键。
   - **`inject_suite_params`（`mtbf_*` 步骤）**：
     1. 必须**先于填空**执行 `#975` 冲突硬拒：若 `step_override`（或合并结果）已声明非空 `project`（非 `None` 且非 `""`），且其字符串值与冻结套件 `dispatch_suite.export_dir` 不一致，控制面必须抛出 `PlanDispatchError` 拒绝派发；
     2. 冲突检查通过后，对 `project` 在**缺键、`null` 或 `""`** 时执行填空，对 `expected_testpoint_count` 在**缺键、`null` 或 `0`** 时执行填空（与现网 `not params.get(key)` 同构）；当合并结果为正整数或匹配的 `project` 值时不得覆盖，来源保持原层。
   - **布尔值 `false` 约束**：第 5 层消费方均不得将布尔值 `false` 视为可覆盖的「空值」；未来新增第 5 层注入消费方时，实施者必须在对应 ADR 中逐键声明填空谓词，不得仅写模糊的「空值」。
   - **来源标注（ADR-0060 D1.5）**：仅当第 5 层实际写入了该键时，L3 投影才可以将其 `source` 标为 `dispatch_injection`；未被覆盖的键必须保留其第 1–4 层原来源。
6. **控制面可冻结默认值的版本化与模板清理前置**（解决 `script_catalog` 扫描默认为空字典的问题）：
   - 实施者不得原地修改已发布 `log_prepare@1.0.0` 的 `default_params` 或 `param_schema`（`AGENTS.md` 硬不变量），也不得在模板仍指向 `log_prepare@1.0.0` 时移除 9 套常规模板的 `4096` 键；
   - 实施者必须在清理常规模板前，按 ADR-0051 流程登记并发布新版本 **`log_prepare@1.1.0`**（不改变脚本执行语义与协议），并配套 1 份遵循 `backend/services/script_seed_governance.py`（`#942`）的幂等 Alembic 种子迁移，为 `script(name='log_prepare', version='1.1.0')` 写入权威且不可变的 `param_schema` 与 `default_params`（`mobilelog_size_mb=4096`、`mtk_auto_start=true`、`ylog_size_mb=4096`、`ylog_rotate=true`、`ylog_required_streams=["android", "kernel"]`；`aee_mode` 与 `mtk_sublog_cmd` 不写入 `default_params`，仅在 `param_schema` 中声明类型与约束；U2 共享夹具与种子 `param_schema` 必须明确钉死容量上下限，防止步骤覆写与脚本钳制再次漂移）；
   - 新站初始化（`alembic upgrade head` + `--publish` + `POST /api/v1/scripts/scan`）中，`POST /api/v1/scripts/scan` 永不覆写已存在的 `param_schema`/`default_params`，同时经 `script_catalog.py` 既有的 `changed_from_empty` 路径补齐 `support_files_manifest`、`capabilities` 与 `package_sha256`；
   - 仅在 `log_prepare@1.1.0` 完成种子迁移、发包与生产 `scan` 后，U3 才可以将全部 10 套稳定性模板的版本升级至 `log_prepare@1.1.0`、移除 9 套常规模板 `log_prepare` 步骤中的冗余 `4096` 键（使其在无预设或混跑回退时由第 2 层 `script_default` 确定性产出 `4096` 并冻结进 `plan_snapshot`），并继续在 `gpu.json` 保留显式 `10240` MB 作为第 4 层 `step_override`；
   - **存量 Plan 与复制草稿兼容**：已保存的存量 Plan（引用 `log_prepare@1.0.0` 或在 `PlanStep.params` 中持久化了显式 `4096`）及从其复制生成的草稿，控制面必须原样保留其脚本版本与显式 `PlanStep.params`（L1/L2/L3 按 `step_override` 展示 `4096`），不得在升级迁移中静默删改存量步骤参数。

### D3 应用门槛、有效目标集统一与草稿投影契约（零静默跨项目串扰）

1. **预览与派发共用同一有效判定设备集（消除 `#2649` 确认环死锁）**：
   - 公开 API `POST /api/v1/plans/{id}/preview` 与 `POST /api/v1/plans/{id}/trigger` 必须保持 `PlanRunTrigger.device_ids` 的 `min_length=1` 约束，不得扩展为空设备集预览；
   - `preview_plan_dispatch_sync` 与 `prepare_plan_run` 必须调用同一个 `#2649` `serial_conflict` 过滤辅助函数，将请求入参 `requested_device_ids` 拆分为：
     - `excluded_serial_conflict_device_ids`：命中占位/冲突序列号（`serial_conflict`）的设备 ID 升序列表；
     - `evaluated_device_ids`：剔除 `serial_conflict` 后剩余的有效目标设备 ID 升序列表。
   - 若 `evaluated_device_ids` 为空（全部请求设备均命中 `serial_conflict`），`preview_plan_dispatch_sync` 与 `prepare_plan_run` 必须统一抛出 `PlanDispatchError` 拒绝（与 `prepare_plan_run` 既有行为一致）；
   - 若 `evaluated_device_ids` 非空：`preview["device_ids"]` 必须继续回传原始 `requested_device_ids`（保持 `PlanExecutePage.tsx` 既有集合一致性校验不被破坏），`prepare_plan_run` 必须继续为 `excluded_serial_conflict_device_ids` 写 `run_context["dispatch_rejected_devices"]` 并物化 `#2649` `FAILED` job，同时两处均**唯一使用 `evaluated_device_ids`** 执行第 2 条的项目归属一致性判定。
2. 在 `preview_plan_dispatch_sync` 与 `prepare_plan_run` 中，控制面必须仅在以下**五项条件同时满足**时应用第 3 层 `project_preset`（状态记为 `applied`）：
   1. `plan.project_id IS NOT NULL`（Plan 显式绑定了项目）；
   2. 绑定的 `TestProject` 满足 `source = 'USER'` 且 `status = 'ACTIVE'`；
   3. 该项目的 `log_profile` 至少包含一个非 `null` 白名单键；
   4. 该 Plan 至少包含一个 `enabled = TRUE` 的 `script:log_prepare` 步骤；
   5. `evaluated_device_ids` 非空，且其中**每一台设备**经 ADR-0029 v2.5 D10 派生出的项目归属（`device.model ⋈ project_model`，`is_active = TRUE`）均精确等于 `plan.project_id`。
3. 任一条件不满足时，控制面**不得**将 `project_preset` 注入步骤参数，也**不得**因此拒绝派发（ADR-0029 D5 仍保持挂起），而必须回退为 Plan 自身的第 1、2、4 层配置，并按以下受控枚举产出确定性判定状态 `project_log_profile.status`：

   | `status` 值 | 触发条件与适用接口 | 对参数合并的影响 |
   |---|---|---|
   | `applied` | `preview` / `prepare_plan_run` 中满足全部 5 项条件 | 应用 `TestProject.log_profile` 到所有启用的 `log_prepare` 步骤 |
   | `not_applicable` | Plan 无启用的 `script:log_prepare` 步骤（全入口通用） | 不应用 |
   | `skipped_unbound_plan` | `plan.project_id IS NULL`（或草稿未选项目；全入口通用） | 不应用（不得从目标设备反推项目预设） |
   | `skipped_project_inactive` | 绑定项目不存在、非 `USER` 或处于 `ARCHIVED`（全入口通用） | 不应用 |
   | `skipped_empty_profile` | 绑定项目 `log_profile` 为空或全 `null`（全入口通用） | 不应用 |
   | `skipped_device_mismatch` | `preview` / `prepare_plan_run` 中 `evaluated_device_ids` 存在至少一台未映射项目或归属其他项目的设备 | 不应用，并附不匹配摘要（未映射台数与涉及的 `project_key` 列表） |
   | `pending_devices` | **仅限无目标设备的静态投影入口**（`GET /plans/{id}/parameter-projection` 与 `POST /plans/parameter-projection`），且满足条件 1–4；不得出现在 `preview`、`prepare_plan_run` 或确认指纹中 | 见第 5 条 |

4. 控制面不得在 `plan.project_id IS NULL` 时复用 `_infer_frozen_project_id` 的众数推断结果来激活 `log_profile`；`_infer_frozen_project_id` 继续只用于 `plan_run.project_id` 的报表归因。
5. **静态与未保存草稿投影契约（含 `project_key` 传输与防乱序覆盖）**：
   - 控制面必须在 `DraftParameterProjectionIn`（`backend/api/routes/plans.py`，维持 `ConfigDict(extra="forbid")`）与前端 `DraftParameterProjectionRequest`（`frontend/src/utils/api/types.ts`）新增可选可空字段 `project_key: Optional[str] = None`；
   - `GET /api/v1/plans/{id}/parameter-projection` 读取 `plan.project_id` 对应项目，`POST /api/v1/plans/parameter-projection` 按入参 `project_key`（空白按 `None` 归一）解析项目；若满足条件 1–4，控制面必须产出 `status = "pending_devices"`、以 `source = "project_preset"` 展示计划基线预估，并在 `dispatch_decisions` 中输出 `kind = "project_log_profile"`、`state = "pending_dispatch"` 的说明（标明「仅在全部有效目标设备归属该项目时生效；混跑或含未映射设备时回退为脚本默认与步骤显式配置」）；若草稿传入的 `project_key` 对应项目不存在、为 `SEED` 或已 `ARCHIVED`，控制面不得返回 404/409 中断编辑，而必须返回 `status = "skipped_project_inactive"` 并回退至第 1、2、4 层；
   - 前端 `usePlanEditForm.ts` 必须将当前 `projectKey ?? null` 纳入草稿投影请求体及其依赖列表，在切换或清空项目时重新拉取服务端投影，并通过请求代次/取消标记丢弃过期响应，不得让旧项目的迟到响应覆盖当前项目选择；前端编辑器本地即时回显（ADR-0060 D1.8 例外）必须使用与后端同源的静态四层合并函数读取当前项目的 `log_profile` 并在服务端投影返回后对齐。
6. 定时调度（ADR-0055）与链式派发（`next_plan_id`）均经过 `prepare_plan_run`，控制面必须对其执行与手动派发完全一致的 D3 判定，不得为定时或链式路径开设豁免旁路。

### D4 执行前确认指纹修订（对 ADR-0060 D5 的扩展）、并发锁序与归属冻结时点

1. 控制面必须将确认指纹协议升级至 `stp-l1-v2:`（域分隔符 `stp-plan-confirmation-v2`，同步更新 `fingerprint_format_ok` 及 `PlanRunTrigger` 校验提示），在 `canonical_confirmation_document`（`backend/services/plan_confirmation.py`）中纳入受控的 `project_log_profile` 块：
   - 必含键：`status`（仅取 D3.3 中适用于 `preview`/`prepare_plan_run` 的六态之一，不含 `pending_devices`）、`project_id`、`project_key`；
   - 当 `status == "applied"` 时，必须同时包含 `log_profile_version` 与规范化后的 `effective_profile`（仅非 `null` 白名单键，键序确定）；
   - 当 `status != "applied"` 时，`effective_profile` 必须规范化为空对象 `{}`、`log_profile_version` 置 `null`，避免对未启用或因不匹配而未生效的 Plan 造成无关指纹失效。
2. 预览与派发之间发生以下任一变化时，若请求携带了 `confirmation_fingerprint`，`assert_confirmation_matches` 必须抛出 `PlanConfirmationChanged` 拒绝本次派发并要求重新预览确认，不得静默沿用新配置派发：
   - 绑定项目的 `log_profile` 或 `log_profile_version` 发生变化（且在预览或派发任一侧处于 `applied`）；
   - 项目状态（`ACTIVE ↔ ARCHIVED`）或目标设备型号归属（`project_model` / `device.model`）在 `prepare_plan_run` 读取前发生变化，导致判定状态在 `applied` 与 `skipped_*` 之间翻转；
   - 提交派发时的目标 `device_ids` 与预览时不同，导致有效设备归属一致性判定结果变化。
3. **单次解析复用、无环锁序与归属冻结时点**：
   - 在 PostgreSQL 事务中，当 `plan.project_id IS NOT NULL` 时，`load_plan_graph_for_confirmation`（供 `preview_plan_dispatch_sync` 与 `prepare_plan_run` 共用）必须按固定无环锁序 **`Plan (FOR SHARE)` → `TestProject (FOR SHARE)`** 锁定对应项目行，与 `update_project_log_profile` 的 `TestProject (FOR UPDATE)` 配对；任何写路径不得反向按 `TestProject → Plan` 取锁。
   - **单次解析复用**：在单次 `preview_plan_dispatch_sync` 调用内，控制面必须只执行一次 D3 解析，并将同一解析结果复用于 `lifecycle` 构建、`parameter_projection` 与 `confirmation_fingerprint`；在单次 `prepare_plan_run` 事务内，控制面必须在获取 `Plan → TestProject` 行锁之后只执行一次 D3 解析，并将同一解析结果复用于 `assert_confirmation_matches`、`validate_pipeline_def` 与 `build_plan_snapshot`，不得在同一调用或事务内二次回读归属表。
   - **设备归属冻结时点（并发语义）**：为避免与心跳高频更新 `device.model`（`heartbeat.py`）产生热行锁争用并保持 ADR-0029 v2.5 D10 非阻塞型号解绑语义（`project_mapping.py`），`prepare_plan_run` 不对 `project_model` 与 `device` 行加跨表排他锁；本次 `PlanRun` 的设备归属冻结时点定义为 `prepare_plan_run` 在 PostgreSQL `READ COMMITTED` 下执行上述单次 D3 解析时的读取快照：
     - 在该读取之前已提交的型号解绑/重映射或 `device.model` 变更，属于**冻结前变更**——`prepare_plan_run` 必然读取到新归属，若与预览判定分歧则抛出 `PlanConfirmationChanged`；
     - 在该读取之后（包括写入 `plan_snapshot` 提交前的短暂窗口，以及 `PlanRun` 进入 `QUEUED` 之后的任意时刻）才提交的型号解绑/重映射或 `device.model` 变更，属于**冻结后变更**——在途 `PlanRun` 保持其已冻结的 `plan_snapshot["project_log_profile"]` 不变，新归属立即生效于后续新的预览与派发（与 ADR-0029「在途归属转移语义」一致）。
4. 升级窗口内的在途旧客户端若携带 `stp-l1-v1:` 指纹，控制面必须将其判定为指纹不一致（或格式不符触发前端重新预览），不得将 `v1` 降级跳过校验；未携带 `confirmation_fingerprint`（`None`）的既有 API / 定时 / 链式调用语义维持 ADR-0060 D5 现状不变。

### D5 `plan_snapshot` 不可变冻结与四层投影对齐（ADR-0023 D2 / ADR-0060 D1）

1. `prepare_plan_run` 在构造 `plan_snapshot`（`build_plan_snapshot`）时，必须将 D3 单次解析得到的 `project_log_profile` 块作为顶层不可变字段写入 `plan_snapshot["project_log_profile"]`（包含 `status`、`project_id`、`project_key`、`log_profile_version`、`effective_profile`、`evaluated_device_ids`、`excluded_serial_conflict_device_ids` 及 `mismatch_summary`），**不得**将 `effective_profile` 覆盖写回 `plan_snapshot["steps"][i]["params"]`。
2. `build_lifecycle_from_snapshot` 必须只消费 `plan_snapshot["project_log_profile"]`（仅当 `status == "applied"` 时作为第 3 层输入传入 `merge_effective_params`），**不得**在准入物化、重放、`#2649` 失败占位 Job 物化或 L2/L3 投影期间回读活表 `test_project`、`project_model` 或 `device.model`。
3. 缺少 `project_log_profile` 键的历史 `plan_snapshot` 必须按「未启用项目预设」处理，控制面不得对历史 PlanRun 执行数据回填迁移。
4. 后端（`backend/api/schemas/plan_parameter_projection.py`）与前端（`frontend/src/utils/api/types.ts`）必须将 `ParamSource` 枚举扩展为五值：`schema_default`、`script_default`、`project_preset`、`step_override`、`dispatch_injection`。
5. L1（编辑器与执行前确认）、L2（运行快照设计视图）与 L3（按设备下发视图）的参数清单组件必须对来自第 3 层的参数明确标注来源 `project_preset` 及对应 `project_key@v<log_profile_version>`；在 `skipped_device_mismatch` 时，前端必须在执行前确认页与运行快照抽屉展示可读回退原因，不得静默隐藏。
6. 实施者必须更新 ADR-0060 D1.8 约定的前后端共享参数合并一致性测试夹具，使 `pytest` 与 `vitest` 同时验证四层静态合并（含 `script_default`、`project_preset` 生效、GPU `step_override` 覆盖、可空参数显式 `null` 覆写、存量显式 `4096` 保留以及 `project_preset` 缺失/跳过）。

### D6 明确不做的事与复议触发条件

1. **不做**：
   - 本 ADR 不复活自由字典 `test_project.variables` 与 `${project.x}` 模板解析；
   - 本 ADR 不复活 ADR-0029 D5 跨项目派发硬拒门禁；
   - 本 ADR 不修改 Agent 进程代码或 `STP_STEP_PARAMS` 协议，也不原地修改已发布的 `log_prepare@1.0.0`（为建立控制面可冻结的 `default_params`/`param_schema`，仅按 D2.6 与 ADR-0051 流程登记发布 `log_prepare@1.1.0` 及配套种子迁移，不改动脚本执行逻辑）；
   - 本 ADR 不为存量 `TestProject` 自动回填 `log_profile`（全量默认 `NULL`，零存量行为漂移），也不自动改写存量 Plan 的 `PlanStep.params` 或 `script_version`；
   - 本 ADR PR 不新增迁移、不改业务代码或 API（实施交付按 §5 批次推进）。
2. **复议触发条件**：
   - 若出现跨多个项目且经真机证实的 `unisoc_sysdump_config` 项目级策略分化 → 规划者可以复议将 `sysdump_enabled` 纳入 `log_profile` 白名单（并同步评估 `#4181` 边界）；
   - 若除 `log_prepare` 之外出现第二类需要项目级默认参数的非 APK 脚本族 → 规划者可以复议按领域增设受控 profile 列或结构化命名空间，但不得退化为无 schema 自由字典；
   - 若出现同一次混合项目 PlanRun 要求「按单台设备所属项目分别注入不同 `log_profile`（L3 逐设备解析）」的真实业务场景 → 规划者可以另立 ADR 评估将项目预设从 L2（PlanRun 级统一冻结）下沉至 L3（逐设备冻结），并重新评估确认指纹与 UI 展示复杂度；
   - 若 ADR-0051 Phase 4 落地「`tool_manifest.json` / 脚本包原生携带 `param_schema`/`default_params`」→ 后续 `log_prepare` 新版本可以切至原生包元数据通道，替代单独的 Alembic 种子迁移。

---

## 3. 备选方案与权衡

| 方案 | 做法 | 不选的原因 |
|---|---|---|
| 方案 A（原 ADR-0029 D4） | 复活自由 JSONB `test_project.variables` + `${project.x}` 模板替换 | 缺乏白名单强校验，易演变为无边界全局配置垃圾桶；模板占位符使参数静态校验、L1 投影与 UI 表单复杂度陡增，违背「首批只做 `log_profile`」的最小收敛共识 |
| 方案 B（按设备归属在 L3 逐台注入） | 混跑 PlanRun 也在物化期按每台设备所属项目分别套用各自 `log_profile` | 同一 PlanRun 下不同设备实际日志参数分裂，L1 执行前确认与确认指纹需逐设备展开，难以人工核对；且混跑本身常见于临时验证，显式步骤配置更可预测 |
| 方案 C（原地回填 `log_prepare@1.0.0` `default_params`） | 用数据迁移直接给已发布 `1.0.0` 写入 `default_params=4096` | 违反 `AGENTS.md` 硬不变量「已存在脚本版本的 `default_params` 不可原地修改」及 `script-versioning.md` 种子迁移治理（已被 `plan_step` 引用的版本拒改） |
| 方案 D（直接删模板 `4096` 依赖脚本内回落） | 不建控制面 `default_params`，直接删去 9 套常规模板的 `4096` | 无预设或混跑回退时 L1/L2/L3 投影变成缺键，人类确认页看不到实际容量，而设备却按脚本内回落跑成 `4096`，破坏 ADR-0060 可解释性 |
| 方案 E（直接改写 `plan_snapshot.steps[].params`） | prepare 时把合并后的字面值直接塞回 `steps[].params` | 丢失 `project_preset` 与 `step_override` 的来源区分，破坏 ADR-0060 L2 投影 |
| **选定方案（D1–D5）** | 白名单 `TestProject.log_profile`（对齐脚本边界与必留子流）+ 单调版本号 + `log_prepare@1.1.0` 种子默认 + 唯一五层合并序 + 统一 `#2649` 有效判定集 + `stp-l1-v2` 指纹 + `plan_snapshot.project_log_profile` 冻结 | 最小变更面、零 Agent/协议改动、全链路可审计可回溯、完全兼容 ADR-0029 v2.5 / ADR-0051 / ADR-0060 / ADR-0066 |

---

## 4. 影响面与风险控制

- **数据库**：
  - U1：1 份前向兼容迁移（`test_project` 新增 `log_profile JSONB NULL` 与 `log_profile_version INTEGER NOT NULL DEFAULT 0`），无锁表回填，无历史数据改写；
  - U2：1 份幂等脚本种子迁移（遵循 `script_seed_governance.py`，为新版本 `log_prepare@1.1.0` 写入 `param_schema` 与 `default_params`，不动 `log_prepare@1.0.0`）。
- **脚本包**：按 ADR-0051 登记并发布 `log_prepare@1.1.0`（执行逻辑不变，仅承接控制面版本化默认元数据）；生产需按常规流程 `--publish` + `POST /api/v1/scripts/scan`；Agent 代码零改动。
- **后端**：
  - `backend/models/project.py`、`backend/api/schemas/project.py`、`backend/services/project_registry.py`、`backend/api/routes/projects.py`；
  - `backend/services/script_params.py`、`backend/services/plan_dispatcher_core.py`、`backend/services/plan_dispatcher_sync.py`、`backend/services/plan_confirmation.py`、`backend/services/plan_parameter_projection.py`、`backend/api/schemas/plan_parameter_projection.py`、`backend/api/routes/plans.py`（`DraftParameterProjectionIn` 增可空 `project_key`、`PlanRunTrigger` 更新 `stp-l1-v2:` 校验提示）；
  - `backend/schemas/pipeline_templates/*.json`（U3：仅在 `log_prepare@1.1.0` 激活后，将 10 套模板切至 `1.1.0`，移除 9 套常规模板冗余 `4096` 键，保留 `gpu.json` 的 `10240`）。
- **前端**：
  - `frontend/src/utils/api/types.ts`（`ProjectOut`/`ProjectUpdateIn` 增 `log_profile`/`log_profile_version`，`DraftParameterProjectionRequest` 增 `project_key?: string | null`，`ParamSource` 增 `project_preset`）；
  - `frontend/src/pages/orchestration/usePlanEditForm.ts`（发送 `project_key` 并防过期响应覆盖当前项目选择）；
  - 项目详情页增加 `log_profile` 结构化表单编辑与审计可见性；
  - `PlanStepInspector` / `ParameterProjectionList` / `DispatchCockpit` / `PlanSnapshotDrawer` 支持 `project_preset` 来源徽标、一键清除步骤覆写回落项目预设、以及 `skipped_device_mismatch` / `#2649` 剔除设备回退提示。
- **回退**：
  - 未配置 `log_profile` 的项目保持 `NULL`，与上线前行为完全等价；
  - 若需紧急停用项目预设，可将对应项目的 `log_profile` 清空（经正常审计写路径），无需代码回滚；已创建 PlanRun 的 `plan_snapshot` 保持自洽不中断。

---

## 5. 落地规划、激活顺序与验收反例（ADR-0058 批次草案）

本 ADR 经第 2 轮独立复核与 Owner 裁决转 `Accepted` 后，按 [ADR-0058](./ADR-0058-planned-batch-execution.md) 拆分为 3 个串行激活单元实施：

- **U1（数据模型与项目预设写面）**：Alembic 迁移（`log_profile`、`log_profile_version`）、D1.2 白名单强校验（含 `mobilelog_size_mb >= 256`、`ylog_required_streams` 必含 `android`/`kernel` 且限白名单）、`project_registry` 写路径 + `record_audit` + `emit_project_changed`、SEED/ARCHIVED 拒绝守卫与单元/API 测试。
- **U2（五层合并序、统一判定集、v2 指纹、快照冻结与 `log_prepare@1.1.0` 种子默认）**：
  - 唯一五层 `merge_effective_params` 与 D2.5 第 5 层兼容性守卫测试；
  - D3.1 统一 `#2649` 有效判定集辅助函数与 D3.2 五条件判定器；
  - `DraftParameterProjectionIn.project_key` 契约与静态 `pending_devices` 投影；
  - `stp-l1-v2:` 确认指纹、单次解析复用与 `Plan → TestProject` `FOR SHARE` 锁序；
  - `plan_snapshot["project_log_profile"]` 顶层冻结与 L1/L2/L3 投影扩展 + 共享一致性夹具；
  - `log_prepare@1.1.0` 登记（`tool_manifest.json`）与遵循 `script_seed_governance.py` 的 Alembic 种子迁移。
- **激活闸门（U2 → U3 之间）**：在生产完成 U2 部署（自动执行 `log_prepare@1.1.0` 种子迁移）、`--publish` 发布 `packages/log_prepare/1.1.0.tar.gz`、`POST /api/v1/scripts/scan` 核验 `log_prepare@1.1.0` 激活且 `default_params` 完整。
  - **遮蔽窗口说明**：在 U2 已上线而 U3 尚未合入的过渡窗内，由 9 套常规模板（仍指向 `1.0.0` 且带显式 `4096`）新建的 Plan，`mobilelog_size_mb` 与 `ylog_size_mb` 仍被第 4 层 `step_override` 遮蔽，但非容量键（`aee_mode`、`mtk_sublog_cmd`、`mtk_auto_start`、`ylog_rotate`、`ylog_required_streams`）已可通过 `project_preset` 生效；U3 合入并上线后，由常规模板新建的 Plan 才完整继承项目容量预设。
- **U3（模板升级至 `1.1.0`、冗余键清理与前端全链路接线）**：10 套稳定性模板升级至 `log_prepare@1.1.0`、清理 9 套常规模板冗余 `4096` 键（保留 `gpu.json` `10240`）、前端项目日志预设编辑 / `usePlanEditForm` `project_key` 联动 / 三处参数清单与确认页/快照抽屉接线、真实浏览器（`stp-ui-review`）端到端验收。

**必须覆盖的边界反例**：

1. **绑定项目 + 全设备一致**：Plan 绑定荣耀项目（采用 `log_prepare@1.1.0` 且步骤未覆写容量），所有有效目标设备均映射到该项目，`log_profile={"aee_mode": "3", "mobilelog_size_mb": 2048}` → 合并后生效 `aee_mode="3"`、`mobilelog_size_mb=2048`（来源均为 `project_preset`），`plan_snapshot["project_log_profile"]["status"] == "applied"`，`steps[].params` 保持原始步骤覆写不被篡改。
2. **GPU 专项覆写优先**：同一项目跑 GPU 模板（`step.params={"mobilelog_size_mb": 10240, "ylog_size_mb": 10240}`）→ 合并后 `mobilelog_size_mb=10240`（`step_override`）且 `aee_mode="3"`（`project_preset`），互不干扰。
3. **显式 `null` 取消项目 `aee_mode`**：项目 `log_profile={"aee_mode": "3"}`，Plan 步骤显式 `params={"aee_mode": null}` → 合并后 `aee_mode=None`（`step_override`），设备保留原值。
4. **`#2649` 正常设备 A + 冲突设备 B 同选可确认派发**：Plan 绑定项目 A，目标集为 `{正常设备 A (项目 A), serial_conflict 设备 B (项目 B 或未映射)}` → `preview` 返回原始 `device_ids=[A, B]`、`evaluated_device_ids=[A]`、`excluded_serial_conflict_device_ids=[B]`、`status="applied"`；使用同一选择与指纹调用 `trigger` 成功创建 `PlanRun`（`status="applied"`），设备 B 按 `#2649` 单独物化为 `FAILED` job，无确认环死锁；若全部目标设备均为 `serial_conflict`，`preview` 与 `prepare_plan_run` 均统一拒绝。
5. **新站扫描、无预设/混跑回退与存量 Plan 保留**：
   - 新站 `alembic upgrade head` + `--publish` + `scan` 后，`log_prepare@1.1.0` 具有完整 `param_schema` 与 `default_params`（`mobilelog_size_mb=4096, ylog_size_mb=4096, ...`）；
   - 使用清理后的常规模板（无项目预设或 `skipped_device_mismatch` 混跑回退）时，L1 → L2 → L3 全链路确定性产出 `4096`（`source="script_default"`），不退化为缺键；
   - 存量 Plan（引用 `log_prepare@1.0.0` 且 `PlanStep.params` 持久化了 `4096`）及其复制出的草稿，原样保留显式 `4096` 并标为 `source="step_override"`。
6. **白名单边界与展锐基础子流防漏采**：
   - 写入 `mobilelog_size_mb=64` 或 `255` 返回 422；写入 `256`、`20480` 通过；
   - 写入 `ylog_required_streams=["sysinfo"]` 或 `["android"]`（缺 `kernel`）返回 422；写入 `["android", "kernel", "uboot"]` 通过；在 `android`/`kernel` 为 `Close`、`sysinfo` 为 `Open` 的 A57 仿真态下，`log_prepare` 回读必须判定未收敛并触发恢复或报错，不得假成功。
7. **混跑 / 含未映射设备安全回退与未绑定 Plan 不反推**：
   - Plan 绑定项目 A，有效目标设备含 1 台项目 B 或 1 台未映射设备 → 不拒绝派发，`status == "skipped_device_mismatch"`，`effective_profile == {}`，回退至第 1/2/4 层；
   - `plan.project_id IS NULL`，即使目标设备全归属项目 A → `status == "skipped_unbound_plan"`，不应用项目 A 的 `log_profile`（`plan_run.project_id` 仍按 `_infer_frozen_project_id` 记为项目 A 供报表使用）。
8. **确认指纹防漂移与并发归属冻结时点**：
   - 预览得 `stp-l1-v2:...`（`applied`，v1）后，管理员修改 `log_profile` 至 v2，或改写目标设备 `project_model` 导致 `applied → skipped_device_mismatch`（提交于 `prepare_plan_run` 读取前）→ 携带旧指纹派发时必须抛出 `PlanConfirmationChanged`；
   - **判定后、快照写入前并发解绑型号**（PostgreSQL 并发交错测试）：若型号解绑在 `prepare_plan_run` 单次 D3 读快照之后提交，本次 `PlanRun` 按冻结后语义完成 `applied` 快照落库，紧随其后的新预览立即看到 `skipped_device_mismatch`；
   - **排队（`QUEUED`）后改归属或改 `log_profile`**：准入物化仅消费冻结的 `plan_snapshot["project_log_profile"]`，不回读活表。
9. **草稿投影 `project_key` 联动与防乱序覆盖**：编辑器新建草稿「选择项目 A → 切换项目 B → 清空项目」，验证 `POST /plans/parameter-projection` 携带对应 `project_key` 并分别返回项目 A 预估（`pending_devices`）、项目 B 预估（`pending_devices`）、无预设回退（`skipped_unbound_plan`）；模拟项目 A 响应晚于项目 B/清空返回时，前端必须丢弃旧响应不得覆盖当前选择。
10. **第 5 层填空兼容与跨轨正交性（ADR-0030 / ADR-0066）**：
    - `connect_wifi` / `monkey_setup` 显式空串密码被 WiFi 池填空，非空密码不被覆盖；`mtbf_*` 显式 `expected_testpoint_count=0` 被套件填空，冲突 `project` 仍先于填空触发 `#975` 硬拒；
    - 同时启用网络供给池与项目 `log_profile` 时，第 3 层仅作用于 `log_prepare`，第 5 层不覆写非空 `step_override`，两层零耦合。
11. **归档 / 种子项目守卫**：尝试写 `SEED` 或 `ARCHIVED` 项目 `log_profile` 返回 422/409；项目归档后派发绑定它的 Plan，`status == "skipped_project_inactive"`。

---

## 6. 转 Accepted 同步传播清单（2026-11-28 已完成）

本 ADR 已于 2026-11-28 经 Owner 裁决转 `Accepted` v1.0，以下四处同步已在同 PR（#4214）中逐一完成：

1. [ADR-0029](./ADR-0029-project-taxonomy-and-param-layering.md)（v2.6）：在 D4 挂起注记与修订记录中补录「非 APK 持续日志参数已由 ADR-0067 以结构化 `TestProject.log_profile` 收敛启用；通用 `variables` 与 `${project.x}` 仍保持挂起」，并补齐头部 `归属域：semantic-ownership project-taxonomy`；
2. [ADR-0060](./ADR-0060-plan-parameter-meaning-and-effective-values.md)（v1.2）：D1.5 `ParamSource` 增补 `project_preset`、D1.8 共享夹具增补 `project_preset`、D5 确认指纹输入增补已解析的 `project_log_profile` 块，并指向 ADR-0067；
3. [`docs/design/2026-semantic-ownership.md`](../design/2026-semantic-ownership.md)：更新 `project-taxonomy` 与 `plan-param-projection` 表行说明与修订记录，登记 ADR-0067 决策节实锚（S15③/⑤）；
4. [`docs/adr/README.md`](./README.md)：将 ADR-0067 主表与 M7 里程碑看板状态翻转为 `Accepted` v1.0，并同步 ADR-0029 v2.6 / ADR-0060 v1.2 版本注记。

---

## 7. Owner 裁决记录（2026-11-28）

1. **`timezone` 归属终裁（已采纳最小边界）**：针对 `#4182` 历史记录中的两条口径（`#7188172814`「首批仅 `log_profile`，`timezone` 留于 `sync_device_time`」vs `#7197113409`「`log_profile + timezone`」），Owner 于 2026-11-28 终裁**维持最小边界**——首批仅包含 `log_profile`，`timezone` 继续由 `sync_device_time` 步骤显式配置，不纳入 `TestProject.log_profile`。
2. **`#3823` 与 `#4042` 边界终裁（确认外置）**：Owner 同日确认 `#3823`（`AutoTestTool.apk` 走冻结 `project_key` 上下文 + 脚本侧路由 + `step_trace` sha256，不存 NFS 路径进项目预设）与 `#4042`（`version_prop` 真源归固件包 `manifest.json`）继续由各自关联单独立承接，不并入 `TestProject.log_profile`。
3. **转 `Accepted` 裁决**：Owner 同日裁决接受 ADR-0067 v1.0 全文，按 §6 完成传播同步并将 PR #4214 翻为 Ready；代码实施按 §5 批次规划（U1–U3）在日志参数稳定后推进。
