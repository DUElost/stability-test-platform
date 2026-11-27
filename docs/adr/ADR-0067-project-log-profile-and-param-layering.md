# ADR-0067：项目级日志预设（`TestProject.log_profile`）与五层参数合并序

- 状态：**Proposed** v0.1（2026-11-27 起草，待独立复核与 Owner 裁决；承接 [#4182](https://github.com/DUElost/stability-test-platform/issues/4182) Phase 3，并落实 [ADR-0066](./ADR-0066-unified-network-supply-and-usb-net.md) D1.6 预留的统一参数合并序）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-11-27
- 版本记录：
  - v0.1（2026-11-27）：初稿，基于 `#4182` Owner 确认决策基线、Phase 2（PR #4191）与 Phase 1（PR #4209 / PR #4211）生产实证及代码基线 `origin/main@cccb0d6a0` 起草。本 PR 为纯文档提案，不改业务代码、数据库、API 或既有 Accepted ADR；待 Owner 裁决转 `Accepted` 时按 §6 传播清单同步修订 ADR-0029、ADR-0060 与 `docs/design/2026-semantic-ownership.md`。
- 决策者：Owner（待裁决）；起草：Antigravity（本地工作面）
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
  `#4181`（展锐 Full Sysdump 独立由 `unisoc_sysdump_config@1.0.0` 管理）、`#4182`（日志开启前置统一化与项目/专项/脚本/计划职责边界演进）；
  `backend/models/project.py`、`backend/models/project_model.py`、`backend/models/plan.py`、`backend/services/project_registry.py`、`backend/services/project_attribution.py`、`backend/services/script_params.py`、`backend/services/plan_dispatcher_core.py`、`backend/services/plan_dispatcher_sync.py`、`backend/services/plan_confirmation.py`、`backend/services/plan_parameter_projection.py`、`backend/api/schemas/project.py`、`backend/api/schemas/plan_parameter_projection.py`、`backend/agent/scripts/log_prepare/log_prepare.py`、`backend/schemas/pipeline_templates/monkey.json`、`backend/schemas/pipeline_templates/gpu.json`

---

## 1. 背景与实证事实（基线 `origin/main@cccb0d6a0`）

### 1.1 问题本质与 ADR-0029 D4 复议触发

[ADR-0029](./ADR-0029-project-taxonomy-and-param-layering.md) v2（2026-08-18）曾将 D4（`test_project.variables` + `${project.x}` 三层参数解析）挂起，原因是当时的核心动机（按项目切换 MTBF 用例 APK）已由脚本端设备指纹路由吸收；同时 ADR-0029 D4 明确写下了复议触发条件：**「出现项目级非 APK 参数需求」**。

`#4182` Phase 1 的真机取证（2026-11-27）证实，设备持续日志开启存在无法仅凭芯片厂商或设备型号推断的**项目/客户级配置差异**：

| 维度 | 实测基线与差异 | 来源 |
|---|---|---|
| 荣耀 MTK（`MLD_NX2/NX3`、`ELA_LX2/LX3`） | 持续日志走 `com.debug.loggerui` / `mobile_log_d`，目标总容量 4096 MB；部分专项按荣耀基线需设置 `persist.vendor.mtk.aee.mode=3`（设备出厂 `aeev.mode=4`） | `#4182` Phase 1 实机取证 |
| 传音 MTK（`INFINIX_X6851`） | 同样走 `com.debug.loggerui`，但出厂 `mobilelog_logsize=1000`、`persist.vendor.mtk.aee.mode=3` / `aeev.mode=3`，容量基线与部分子日志开关不同于荣耀 | `#4182` Phase 1 实机取证 |
| 展锐 UNISOC（`P525AA` `MLY_LX2/LX3` vs `A57` `Z2581`） | 持续日志统一走 `/product/bin/ylogctl`（YLog 5.x）；不同项目对 `ylog_size_mb`、`ylog_rotate` 及必需子流 `ylog_required_streams`（如默认 `["android", "kernel"]` vs 扩展子流）存在项目级差异 | `#4182` Phase 1 实机取证 |
| 专项级例外（GPU 等） | GPU 压测需 10240 MB 持续日志容量，显著高于常规专项（4096 MB）；此类差异属于专项维度而非项目维度，已在模板 `gpu.json` 的步骤参数中显式表达 | PR #4211（生产已生效） |

目前 `#4182` 的前两阶段均已在生产生效：

- **Phase 2（PR #4191）**：建立「从模板创建 Plan」「复制为草稿」「脚本轻标签筛选」与 `specialty_key → template_id` 显式映射；
- **Phase 1（PR #4209 + PR #4211）**：上线统一持续日志脚本 `log_prepare@1.0.0`（按芯片能力内部分流，不碰 Full Sysdump），并将其按「清理后置、GPU 10240M、其余 4096M」接入全部 10 套稳定性模板。

在四层职责边界（`Script` 原子能力 → `Specialty` 模板与专项默认 → `Project` 经验证的项目默认 → `Plan` 显式步骤与覆写）下，当前仍缺少 `Project` 层的结构化日志默认承载：若不引入项目预设，运营与测试人员在为不同客户/项目复用同一专项模板时，仍须逐个 Plan 手动改写 `aee_mode`、`mtk_sublog_cmd`、`ylog_required_streams` 或容量阈值；而若直接复活 ADR-0029 D4 自由字典 `variables` 与 `${project.x}` 模板替换，又会引入无 schema 约束、自由键蔓延与跨项目静默串扰的风险。

### 1.2 既有代码约束与关键接缝

1. **项目归属派生化（ADR-0029 v2.5 D10）**：设备归属由 `device.model ⋈ project_model`（`is_active = TRUE`）实时派生（`backend/services/project_attribution.py`）；`plan.project_id` 可空（`NULL` 表示「不限」）；`prepare_plan_run` 中 `_infer_frozen_project_id` 在 `plan.project_id IS NULL` 时仅按众数推断报表维度 `plan_run.project_id`，不作派发阻断（D5 挂起）。
2. **现行参数合并单点（`script_params.py` / `plan_dispatcher_core.py`）**：静态层仅合并三层 `schema.default < default_params < step.params`；物化期（`materialize_jobs_and_allocations`）叠加 `inject_wifi_params`（及 ADR-0066 统一网络供给注入）与 `inject_suite_params`（ADR-0030），只填空不覆盖显式值。
3. **模板导入与步骤覆写冲突面**：当前 PR #4211 的 9 套常规模板（如 `monkey.json`）在 `log_prepare` 步骤的 `params` 中显式写入了 `{"mobilelog_size_mb": 4096, "ylog_size_mb": 4096}`，而 `log_prepare@1.0.0` 的 `default_params` 本身已是 `4096`。若 `PlanStep.params` 始终优先于项目预设，则常规模板中的冗余 `4096` 会遮蔽项目预设的容量值，必须在 Phase 3 同步清理常规模板的冗余默认键，同时保留 `gpu.json` 的显式 `10240` 作为专项覆写。
4. **确认指纹与行锁配对（ADR-0060 D5 / `plan_confirmation.py`）**：`preview_plan_dispatch_sync` 与 `prepare_plan_run` 共用 `load_plan_graph_for_confirmation`（对 `plan` 取 PostgreSQL `FOR SHARE`）与 `stp-l1-v1` HMAC 指纹，但当前指纹仅覆盖 `plan`、`steps`、`scripts`，尚未覆盖 `TestProject.log_profile` 与目标设备归属一致性判定结果。
5. **四层投影与快照冻结（ADR-0060 D1 / `plan_parameter_projection.py`）**：`ParamSource` 当前仅含 `schema_default`、`script_default`、`step_override`、`dispatch_injection` 四值；`build_lifecycle_from_snapshot` 只读 `plan_snapshot` 而不回读活表。
6. **跨 ADR 协同（ADR-0066 D1.6）**：ADR-0066 已明确网络供给池与测试项目正交，并约定派发期参数注入遵循本 ADR 在 `plan_dispatcher_core.py` 维护的唯一五层合并序。

---

## 2. 决策

> 以下 D1–D6 节规则句遵循 [ADR-0059](./ADR-0059-contract-and-sop-writing-conventions.md) 写作约定：仅使用「必须 / 不得 / 建议 / 可以」，并明确执行者。

### D1 结构化项目日志预设 `TestProject.log_profile`（对 ADR-0029 D4 的最小收敛修订）

1. 平台必须通过在 `test_project` 表新增受控结构化列 `log_profile`（`JSONB`，可空，默认 `NULL`）与单调递增版本号 `log_profile_version`（`INTEGER`，非空，默认 `0`）承载项目级日志默认配置，不得复活 ADR-0029 D4 原草案中的自由字典 `test_project.variables` 或 `${project.x}` 占位符替换机制。
2. `TestProject.log_profile` 在首期必须采用白名单强校验（Pydantic `ConfigDict(extra="forbid")`），只可以包含下表列出的已实证 `log_prepare` 参数键；未出现或值为 `null` 的键表示该项目不对该键施加项目默认：

   | 键名 | 类型与约束 | 对应 `log_prepare` 参数与含义 |
   |---|---|---|
   | `mobilelog_size_mb` | 整数，`64 <= v <= 20480` | MTK `mobilelog` 总容量上限（MB） |
   | `mtk_auto_start` | 布尔 | MTK `mobilelog` 开机自启开关 |
   | `mtk_sublog_cmd` | 字符串，匹配 `^[A-Za-z0-9_.-]{1,64}$` | MTK 可选子日志开关指令（如 `set_sublog_4_5_0`） |
   | `aee_mode` | 字符串，匹配 `^[0-9]{1,2}$` | MTK 可选 `persist.vendor.mtk.aee.mode` 目标值；未设则保留设备当前值 |
   | `ylog_size_mb` | 整数，`64 <= v <= 10240` | 展锐 `ylogctl aplogmaxsize` 容量上限（MB） |
   | `ylog_rotate` | 布尔 | 展锐 `ylogctl rotatelog` 循环覆盖开关 |
   | `ylog_required_streams` | 非空字符串列表，元素匹配 `^[a-z0-9_]{1,32}$` 且去重 | 展锐 `ylogctl query` 必须处于 `Open` 态的子流清单 |

3. 控制面不得将以下非连续日志配置纳入首期 `log_profile`：
   - `timezone`（继续由 `sync_device_time` 步骤显式配置）；
   - `unisoc_sysdump_config` 开关与参数（继续由 `#4181` 独立步骤管理）；
   - 网络供给池与凭据（WiFi / `usb_net`，按 ADR-0066 D1.6 与项目保持正交）；
   - 任何敏感凭据、密钥、主机路径或未在白名单登记的自由键。
4. 控制面对 `source = 'SEED'` 或 `status = 'ARCHIVED'` 的 `TestProject` 行必须拒绝写入 `log_profile`（与 `project_registry.py` 现有守卫一致）。
5. 每次 `log_profile` 发生有效内容变更时，控制面必须在同一事务内将 `log_profile_version` 原子加 1、通过 `record_audit` 写入变更前后值与版本号审计日志，并在提交后广播 `emit_project_changed(project.id, "updated")`；未发生内容变化时，控制面不得推进 `log_profile_version`。
6. `TestProject.log_profile` 的写入权限必须与 `PUT /api/v1/projects/{project_key}` 保持一致（仅管理员可以写入，所有登录用户可以读取）。

### D2 唯一五层参数合并序（统一 ADR-0029、ADR-0060 与 ADR-0066）

1. 控制面与前端草稿编辑器必须采用以下唯一五层参数合并序（自底向上），实施者不得在任何路由、预览或物化路径中另写第二套优先级规则：

   | 层次 | 来源标识（`ParamSource`） | 事实源 | 覆盖语义与生效范围 |
   |---|---|---|---|
   | 第 1 层 | `schema_default` | `Script.param_schema[k].default` | 最低优先级，适用于全部步骤 |
   | 第 2 层 | `script_default` | `Script.default_params[k]` | 覆盖第 1 层；已发布版本不可原地修改（ADR-0051） |
   | 第 3 层 | `project_preset` | `TestProject.log_profile[k]` | 覆盖第 1–2 层；**仅**作用于 `script:log_prepare` 步骤、**仅**注入非 `null` 键、且**仅**在满足 D3 全部准入条件时生效 |
   | 第 4 层 | `step_override` | `PlanStep.params[k]` | 覆盖第 1–3 层（含专项模板写入的专项例外，如 GPU `10240` MB）；显式存在于 `PlanStep.params` 中的键始终优先 |
   | 第 5 层 | `dispatch_injection` | 物化期动态注入（`inject_wifi_params` / ADR-0066 网络供给 / `inject_suite_params`） | 仅在物化期（L3）对第 1–4 层合并后仍缺失或为空值的键执行填空，不得覆盖已显式配置的 `step_override`（ADR-0030 `#975` 冲突硬拒规则保持不变） |

2. 在 `PlanStep.params`（第 4 层）中，针对 `log_prepare` 接受 `null` 值的可选参数（`aee_mode`、`mtk_sublog_cmd`），若用户显式写入 `"aee_mode": null`（键存在且值为 `null`），控制面必须将其视为显式步骤覆写，用于覆盖第 3 层 `project_preset` 并回退为「保留设备当前值」；对于非空类型参数（如整数容量、布尔开关），保存期校验器必须继续拒绝显式 `null`。
3. 在第 3 层 `project_preset` 中，未出现或值为 `null` 的键必须跳过，控制面不得用 `project_preset` 中的 `null` 抹除第 1–2 层的脚本默认值。
4. 合并后的最终步骤参数必须继续通过 `validate_params_against_schema`（`backend/services/script_params.py`）校验，合并函数 `merge_effective_params` 与 `build_lifecycle_from_steps` / `build_lifecycle_from_snapshot` 必须保持为后端唯一计算入口（ADR-0060 D1.2）。
5. 实施者在 Phase 3 落地时必须清理常规稳定性模板（非 GPU 专项）中 `log_prepare` 步骤的冗余默认容量键（`4096` 已由 `log_prepare@1.0.0` 的 `default_params` 提供），避免导入模板后在 `PlanStep.params` 形成第 4 层硬覆写而遮蔽第 3 层 `project_preset`；GPU 模板（`gpu.json`）中的 `10240` MB 属于专项级例外，必须继续保留在模板的 `params` 中作为第 4 层覆写。

### D3 应用门槛与跨项目/未归属回退语义（零静默跨项目串扰）

1. 在 `preview_plan_dispatch_sync` 与 `prepare_plan_run`（已知目标设备集合 `device_ids`）中，控制面必须仅在以下**五项条件同时满足**时应用第 3 层 `project_preset`（状态记为 `applied`）：
   1. `plan.project_id IS NOT NULL`（Plan 显式绑定了项目）；
   2. 绑定的 `TestProject` 满足 `source = 'USER'` 且 `status = 'ACTIVE'`；
   3. 该项目的 `log_profile` 至少包含一个非 `null` 白名单键；
   4. 该 Plan 至少包含一个 `enabled = TRUE` 的 `script:log_prepare` 步骤；
   5. 有效目标设备集合（`prepare_plan_run` 中为剔除 `#2649` `serial_conflict` 后的 `device_ids`，`preview_plan_dispatch_sync` 中为入参 `device_ids`）非空，且其中**每一台设备**经 ADR-0029 v2.5 D10 派生出的项目归属（`device.model ⋈ project_model`，`is_active = TRUE`）均精确等于 `plan.project_id`。
2. 任一条件不满足时，控制面**不得**将 `project_preset` 注入步骤参数，也**不得**因此拒绝派发（ADR-0029 D5 仍保持挂起），而必须回退为 Plan 自身的第 1、2、4 层配置，并按以下受控枚举产出确定性判定状态 `project_log_profile.status`：

   | `status` 值 | 触发条件 | 对参数合并的影响 |
   |---|---|---|
   | `applied` | 满足全部 5 项条件 | 应用 `TestProject.log_profile` 到所有启用的 `log_prepare` 步骤 |
   | `not_applicable` | Plan 无启用的 `script:log_prepare` 步骤 | 不应用 |
   | `skipped_unbound_plan` | `plan.project_id IS NULL` | 不应用（不得从目标设备反推项目预设） |
   | `skipped_project_inactive` | 绑定项目非 `USER` 或处于 `ARCHIVED` | 不应用 |
   | `skipped_empty_profile` | 绑定项目 `log_profile` 为空或全 `null` | 不应用 |
   | `skipped_device_mismatch` | 目标设备中存在至少一台未映射项目或归属其他项目的设备 | 不应用，并附不匹配摘要（未映射台数与涉及的 `project_key` 列表） |
   | `pending_devices` | 无目标设备集合的静态视图（如 Plan 编辑器、未选设备的 L1 投影）或空 `device_ids` 预览 | 见第 4 条 |

3. 控制面不得在 `plan.project_id IS NULL` 时复用 `_infer_frozen_project_id` 的众数推断结果来激活 `log_profile`；`_infer_frozen_project_id` 继续只用于 `plan_run.project_id` 的报表归因。
4. 在未指定目标设备的静态视图（`GET /plans/{id}/parameter-projection` 与编辑器草稿预览）中，若满足条件 1–4，控制面必须将绑定项目的 `log_profile` 以 `source = "project_preset"` 展示为计划基线预估，并在 `dispatch_decisions` 中输出 `kind = "project_log_profile"`、`state = "pending_dispatch"` 的说明（标明「仅在全部目标设备归属该项目时生效；混跑或含未映射设备时回退为步骤显式配置」）。
5. 定时调度（ADR-0055）与链式派发（`next_plan_id`）均经过 `prepare_plan_run`，控制面必须对其执行与手动派发完全一致的 D3 判定，不得为定时或链式路径开设豁免旁路。

### D4 执行前确认指纹修订（对 ADR-0060 D5 的扩展）与并发锁序

1. 控制面必须将确认指纹协议升级至 `stp-l1-v2:`（域分隔符 `stp-plan-confirmation-v2`），在 `canonical_confirmation_document`（`backend/services/plan_confirmation.py`）中纳入受控的 `project_log_profile` 块：
   - 必含键：`status`、`project_id`、`project_key`；
   - 当 `status == "applied"` 时，必须同时包含 `log_profile_version` 与规范化后的 `effective_profile`（仅非 `null` 白名单键，键序确定）；
   - 当 `status != "applied"` 时，`effective_profile` 必须规范化为空对象 `{}`、`log_profile_version` 置 `null`，避免对未启用或因不匹配而未生效的 Plan 造成无关指纹失效。
2. 预览与派发之间发生以下任一变化时，若请求携带了 `confirmation_fingerprint`，`assert_confirmation_matches` 必须抛出 `PlanConfirmationChanged` 拒绝本次派发并要求重新预览确认，不得静默沿用新配置派发：
   - 绑定项目的 `log_profile` 或 `log_profile_version` 发生变化（且在预览或派发任一侧处于 `applied`）；
   - 项目状态（`ACTIVE ↔ ARCHIVED`）或目标设备型号归属（`project_model`）变化，导致判定状态在 `applied` 与 `skipped_*` 之间翻转；
   - 提交派发时的目标 `device_ids` 与预览时不同，导致设备归属一致性判定结果变化；
   - 预览时 `device_ids` 为空（`status = "pending_devices"`）而派发时为非空（`status = "applied"`）。
3. 在 PostgreSQL 事务中，当 `plan.project_id IS NOT NULL` 时，`load_plan_graph_for_confirmation`（供 `prepare_plan_run` 复用）必须按固定无环锁序 **`Plan (FOR SHARE)` → `TestProject (FOR SHARE)`** 锁定对应项目行，与 `update_project_log_profile` 的 `TestProject (FOR UPDATE)` 配对；任何写路径不得反向按 `TestProject → Plan` 取锁。
4. 升级窗口内的在途旧客户端若携带 `stp-l1-v1:` 指纹，控制面必须将其判定为指纹不一致并触发重新预览，不得将 `v1` 降级跳过校验；未携带 `confirmation_fingerprint`（`None`）的既有 API / 定时 / 链式调用语义维持 ADR-0060 D5 现状不变。

### D5 `plan_snapshot` 不可变冻结与四层投影对齐（ADR-0023 D2 / ADR-0060 D1）

1. `prepare_plan_run` 在构造 `plan_snapshot`（`build_plan_snapshot`）时，必须将 D3 判定得到的 `project_log_profile` 块作为顶层不可变字段写入 `plan_snapshot["project_log_profile"]`（包含 `status`、`project_id`、`project_key`、`log_profile_version`、`effective_profile` 及 `mismatch_summary`），**不得**将 `effective_profile` 覆盖写回 `plan_snapshot["steps"][i]["params"]`。
2. `build_lifecycle_from_snapshot` 必须只消费 `plan_snapshot["project_log_profile"]`（仅当 `status == "applied"` 时作为第 3 层输入传入 `merge_effective_params`），**不得**在准入物化、重放或 L2/L3 投影期间回读活表 `test_project` 或 `project_model`。
3. 缺少 `project_log_profile` 键的历史 `plan_snapshot` 必须按「未启用项目预设」处理，控制面不得对历史 PlanRun 执行数据回填迁移。
4. 后端（`backend/api/schemas/plan_parameter_projection.py`）与前端（`frontend/src/utils/api/types.ts`）必须将 `ParamSource` 枚举扩展为五值：`schema_default`、`script_default`、`project_preset`、`step_override`、`dispatch_injection`。
5. L1（执行前确认）、L2（运行快照设计视图）与 L3（按设备下发视图）的参数清单组件必须对来自第 3 层的参数明确标注来源 `project_preset` 及对应 `project_key@v<log_profile_version>`；在 `skipped_device_mismatch` 时，前端必须在执行前确认页与运行快照抽屉展示可读回退原因，不得静默隐藏。
6. 实施者必须更新 ADR-0060 D1.8 约定的前后端共享参数合并一致性测试夹具，使 `pytest` 与 `vitest` 同时验证四层静态合并（含 `project_preset` 生效、GPU `step_override` 覆盖、可空参数显式 `null` 覆写及 `project_preset` 缺失/跳过）。

### D6 明确不做的事与复议触发条件

1. **不做**：
   - 本 ADR 不复活自由字典 `test_project.variables` 与 `${project.x}` 模板解析；
   - 本 ADR 不复活 ADR-0029 D5 跨项目派发硬拒门禁；
   - 本 ADR 不修改 Agent 进程代码、`log_prepare@1.0.0` 脚本包、或 `STP_STEP_PARAMS` 协议（`log_prepare@1.0.0` 现已支持 D1 全部 7 个参数）；
   - 本 ADR 不为存量 `TestProject` 自动回填 `log_profile`（全量默认 `NULL`，零存量行为漂移）；
   - 本起草 PR 不新增迁移、不改业务代码或 API，也不提前改写既有 Accepted ADR 正文。
2. **复议触发条件**：
   - 若出现跨多个项目且经真机证实的 `unisoc_sysdump_config` 项目级策略分化 → 复议将 `sysdump_enabled` 纳入 `log_profile` 白名单（并同步评估 `#4181` 边界）；
   - 若除 `log_prepare` 之外出现第二类需要项目级默认参数的非 APK 脚本族 → 规划者可以复议按领域增设受控 profile 列或结构化命名空间，但不得退化为无 schema 自由字典；
   - 若出现同一次混合项目 PlanRun 要求「按单台设备所属项目分别注入不同 `log_profile`（L3 逐设备解析）」的真实业务场景 → 另立 ADR 评估将项目预设从 L2（PlanRun 级统一冻结）下沉至 L3（逐设备冻结），并重新评估确认指纹与 UI 展示复杂度。

---

## 3. 备选方案与权衡

| 方案 | 做法 | 不选的原因 |
|---|---|---|
| 方案 A（原 ADR-0029 D4） | 复活自由 JSONB `test_project.variables` + `${project.x}` 模板替换 | 缺乏白名单强校验，易演变为无边界全局配置垃圾桶；模板占位符使参数静态校验、L1 投影与 UI 表单复杂度陡增，违背「首批只做 `log_profile`」的最小收敛共识 |
| 方案 B（按设备归属在 L3 逐台注入） | 混跑 PlanRun 也在物化期按每台设备所属项目分别套用各自 `log_profile` | 同一 PlanRun 下不同设备实际日志参数分裂，L1 执行前确认与确认指纹需逐设备展开，难以人工核对；且混跑本身常见于临时验证，显式步骤配置更可预测 |
| 方案 C（项目预设高于步骤参数） | 让 `project.log_profile` 覆盖 `PlanStep.params` | 直接破坏 GPU 模板 `10240 MB` 等专项例外，以及单次 Plan 的手工排障覆写，违背「显式步骤参数优先」直觉 |
| 方案 D（直接改写 `plan_snapshot.steps[].params`） | prepare 时把合并后的字面值直接塞回 `steps[].params` | 丢失 `project_preset` 与 `step_override` 的来源区分，破坏 ADR-0060 L2 投影与「为何生效该值」的可解释性 |
| **选定方案（D1–D5）** | 结构化白名单 `TestProject.log_profile` + 单调版本号 + 唯一五层合并序 + 绑定且全员一致门槛 + `stp-l1-v2` 指纹 + `plan_snapshot.project_log_profile` 冻结 | 最小变更面、零 Agent/脚本改动、全链路可审计可回溯、完全兼容 ADR-0029 v2.5 / ADR-0060 / ADR-0066 |

---

## 4. 影响面与风险控制

- **数据库**：1 份前向兼容迁移（`test_project` 新增 `log_profile JSONB NULL` 与 `log_profile_version INTEGER NOT NULL DEFAULT 0`），无锁表回填，无历史数据改写。
- **后端**：
  - `backend/models/project.py`、`backend/api/schemas/project.py`、`backend/services/project_registry.py`、`backend/api/routes/projects.py`；
  - `backend/services/script_params.py`、`backend/services/plan_dispatcher_core.py`、`backend/services/plan_dispatcher_sync.py`、`backend/services/plan_confirmation.py`、`backend/services/plan_parameter_projection.py`、`backend/api/schemas/plan_parameter_projection.py`；
  - `backend/schemas/pipeline_templates/*.json`（仅移除常规模板 `log_prepare` 步骤中与 `default_params` 重复的 `4096` 键，保留 `gpu.json` 的 `10240`）。
- **前端**：
  - `frontend/src/utils/api/types.ts`（`ProjectOut`/`ProjectUpdateIn` 增 `log_profile`/`log_profile_version`，`ParamSource` 增 `project_preset`）；
  - 项目详情页增加 `log_profile` 结构化表单编辑与审计可见性；
  - `PlanStepInspector` / `ParameterProjectionList` / `DispatchCockpit` / `PlanSnapshotDrawer` 支持 `project_preset` 来源徽标、一键清除步骤覆写回落项目预设、以及 `skipped_device_mismatch` 回退提示。
- **Agent / 脚本**：零改动。
- **回退**：
  - 未配置 `log_profile` 的项目保持 `NULL`，与上线前行为完全等价；
  - 若需紧急停用项目预设，可将对应项目的 `log_profile` 清空（经正常审计写路径），无需代码回滚；已创建 PlanRun 的 `plan_snapshot` 保持自洽不中断。

---

## 5. 落地规划与验收反例（ADR-0058 批次草案）

本 ADR 经独立复核与 Owner 裁决转 `Accepted` 后，建议在 `#4182` Phase 3 日志参数稳定窗口到达时，按 [ADR-0058](./ADR-0058-planned-batch-execution.md) 拆分为 3 个交付单元实施：

- **U1（数据模型与项目预设写面）**：Alembic 迁移（`log_profile`、`log_profile_version`）、白名单 Pydantic 校验器、`project_registry` 写路径 + `record_audit` + `emit_project_changed`、SEED/ARCHIVED 拒绝守卫与单元/API 测试。
- **U2（五层合并序、准入门槛、v2 指纹与快照冻结）**：`merge_effective_params` 与 `plan_dispatcher_core` 唯一五层合并实现、D3 五条件判定器、`stp-l1-v2` 确认指纹与 `Plan → TestProject` `FOR SHARE` 锁序、`plan_snapshot["project_log_profile"]` 冻结、L1/L2/L3 投影扩展与前后端共享一致性夹具、PostgreSQL 并发交错测试。
- **U3（模板默认键收口与前端展示/编辑）**：清理 9 套常规模板 `log_prepare` 冗余 `4096` 键（保留 `gpu.json` `10240`）、前端项目日志预设编辑与三处参数清单/确认页/快照抽屉接线、真实浏览器（`stp-ui-review`）端到端验收。

**必须覆盖的边界反例**：

1. **绑定项目 + 全设备一致**：Plan 绑定荣耀项目，所有目标设备均映射到该项目，`log_profile={"aee_mode": "3", "mobilelog_size_mb": 2048}` → 合并后生效 `aee_mode="3"`、`mobilelog_size_mb=2048`，来源为 `project_preset`，`plan_snapshot["project_log_profile"]["status"] == "applied"`，`steps[].params` 保持原始步骤覆写不被篡改。
2. **GPU 专项覆写优先**：同一项目跑 GPU 模板（`step.params={"mobilelog_size_mb": 10240, "ylog_size_mb": 10240}`）→ 合并后 `mobilelog_size_mb=10240`（`step_override`）且 `aee_mode="3"`（`project_preset`），互不干扰。
3. **显式 `null` 取消项目 `aee_mode`**：项目 `log_profile={"aee_mode": "3"}`，Plan 步骤显式 `params={"aee_mode": null}` → 合并后 `aee_mode=None`（`step_override`），设备保留原值。
4. **混跑 / 含未映射设备安全回退**：Plan 绑定项目 A，目标设备含 1 台项目 B 或 1 台未映射设备 → 不拒绝派发，但 `status == "skipped_device_mismatch"`，`effective_profile == {}`，步骤回退到脚本默认 + 步骤显式值。
5. **未绑定 Plan 不反推预设**：`plan.project_id IS NULL`，即使目标设备全部归属项目 A → `status == "skipped_unbound_plan"`，不应用项目 A 的 `log_profile`（`plan_run.project_id` 仍按 `_infer_frozen_project_id` 记为项目 A 供报表使用）。
6. **确认指纹防漂移**：预览得 `stp-l1-v2:...`（`applied`，v1）后，管理员将项目 `log_profile` 改为 v2，或改写其中一台设备的 `project_model` 导致 `applied → skipped_device_mismatch` → 携带旧指纹派发时 `prepare_plan_run` 必须抛出 `PlanConfirmationChanged`。
7. **冻结隔离性**：`prepare_plan_run` 冻结 `plan_snapshot`（`log_profile_version=1`）进入 `QUEUED` 后，管理员修改项目 `log_profile` 至 v2 → 准入物化生成 `JobInstance.pipeline_def` 时仍使用 v1 冻结值，不回读活表。
8. **归档 / 种子项目守卫**：尝试写 `SEED` 或 `ARCHIVED` 项目 `log_profile` 返回 422/409；若项目被归档后派发绑定它的 Plan，`status == "skipped_project_inactive"`。
9. **跨轨正交性（ADR-0066）**：同时启用网络供给池与项目 `log_profile` 的 PlanRun，第 3 层 `project_preset` 仅作用于 `log_prepare`，第 5 层网络供给注入不覆写显式 `step_override`，两层零耦合。

---

## 6. 转 Accepted 时的同步传播清单

本 ADR 当前为 `Proposed`，作者不得在本起草 PR 中提前修改以下文档，必须在 Owner 裁决本 ADR 转 `Accepted` 的同一 PR 中逐一完成：

1. [ADR-0029](./ADR-0029-project-taxonomy-and-param-layering.md)（v2.6）：在 D4 挂起注记中补录「非 APK 持续日志参数已由 ADR-0067 以结构化 `TestProject.log_profile` 收敛启用；通用 `variables` 与 `${project.x}` 仍保持挂起」，并同步其传播清单的既有位置；
2. [ADR-0060](./ADR-0060-plan-parameter-meaning-and-effective-values.md)（v1.2）：D1.5 `ParamSource` 增补 `project_preset`，D5 确认指纹输入增补已解析的 `project_log_profile` 块，并指向 ADR-0067；
3. [`docs/design/2026-semantic-ownership.md`](../design/2026-semantic-ownership.md)：更新 `project-taxonomy` 与 `plan-param-projection` 表行说明与修订记录，登记 ADR-0067 `## 2. 决策` 锚点（S15③/⑤）；
4. [`docs/adr/README.md`](./README.md)：将 ADR-0067 主表与 M7 里程碑看板状态翻转为 `Accepted`。
