# MTBF 专项 P1 设计：test_suite/test_case 实体 + 外部管理面 + D2/D3b 派发门禁

- 日期：2026-08-20
- 状态：**设计草案**（供 P1 实施 PR 引用；P1a 评审定稿后实施）
- 上游：[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md) D1–D6（**Accepted v1.4**）、[研究文档](../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) §5.1/§5.2/§5.5、[P0 设计](./2026-08-mtbf-p0-runner-design.md)（已实施）
- P0 验收记录：[Agent Note](../notes/feature/2026-08-20-mtbf-p0-scripts-and-validate.md)

## 0. 结论摘要

1. **实体**：`test_suite` + `test_case`（粒度 = testpoint，`exec_descs` JSONB 含 1..N 执行描述）；
   `project_id` 可空 = 通用套件 / 必填 = 项目套件；套件级 `apk_binding`；快照列 `source_sha256`（导入时文件）+ `exported_sha256`（导出产物文件 sha）+ `exported_content_sha256`（导出时**库内容规范化指纹**——库漂移由门禁计算检测，不靠端点置空纪律）。
2. **API**：13 端点（研究 §5.5 草案细化定稿），挂 `/api/v1/test-suites` + `/api/v1/test-cases`；读 = 登录用户、写 = admin（初版）；**全部写操作 `record_audit`**。
3. **复用 P0 资产**：`backend/services/mtbf_suite.py`（parse / validate / patch）全部复用，新增**渲染器**（库 → runtask.xml / UiAutomatorTestData.xml）——控制面唯一实现，与解析同文件；脚本侧 `_lib.py` 的 times patch 不变（消费面不变）。
4. **D2 绑定（v1.3 修订）**：`plan.suite_id` 可空外键（NULL = P0 文件真源模式；非空 = 托管模式）→ prepare 冻结 `run_context.dispatch_suite` + precheck 五步门禁 fail-fast 挂 admission 既有链。
5. **导出落点 = 中心存储消费路径** `{STP_AEE_NFS_ROOT}/mtbf/{project}/`（P0 已部署，消费面不变）——「管理面从文件升级为 API，消费面不变」落地；atomic write + ACTIVE 引用守卫（依赖 P1b 冻结字段）。
6. **CLI**：`tools/dev/mtbf-cases.py`（单文件 kebab-case，对齐 `backfill-test-project.py` 先例）——选定后回写 ADR-0030 修订记录（D4 要求）。
7. **里程碑**：P1a 实体 + CRUD/import/export/validate + 审计 → P1b D2/D3b 绑定门禁 + 脚本侧注入（expected 替代 env）→ P1c CLI + 文档 + 状态传播。

## 1. 实体模型

### 1.1 test_suite

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | PK | |
| `name` | str unique 非空 | **管理键 / 对外引用键**（外部 agent 与 Plan 绑定均以 `name` 引用，见 §3.1 `suite_name`）；导入时默认取 runtask `name`，可重命名 |
| `display_name` | str 可空 | 展示名 |
| `project_id` | FK test_project 可空 | 空 = 通用套件（现 MTBF）；必填 = 项目套件（相机 MTBF，APK↔项目严格对应） |
| `export_dir` | str 可空 | 导出目录名（`{NFS}/mtbf/{export_dir}/`）；空 → 有 `project_id` 用 project key，否则 `legacy`（兼容 P0 部署现状） |
| `apk_binding` | JSONB 可空 | 用例 APK 文件名数组（如 `["OfflineScriptManager.apk", "ReliabilityUiautomatorTest.apk", "ReliabilityUiautomatorTestTest.apk"]`）——声明随项目严格对应；文件 sha 不存库（脚本 setup 已留 `apk_sha256[]` 痕，见 P0 设计 §3.3） |
| `root_config` | JSON | runtask 根属性**全量**（`times`/`testTimeOut`/`takeScreenshot`/`stopWhenFail`/`taskRegressionType`/`caseRegressionType`/`testpointRegressionTimes`/`name`）——导出渲染的权威源 |
| `global_params` | JSON | `{"sim": {wifiName: .., wifiPWD: .., number: .., googleAccount: ..}, "test_set_attrs": {name: .., TakeScreenshot: ..}, "test_package_ref": "<原文或 null>"}`——SIM 属性合并（`parse_global_params` 产出）+ **TestSet 根属性**（P1a 补：不带出则导出物丢 `TakeScreenshot`，等于给设备端换了个没见过的文件）+ TestPackage 参考块原文保留 |
| `source_sha256` | str | 导入时原始文件 sha（溯源） |
| `exported_sha256` | str 可空 | **导出产物（磁盘文件）sha**——磁盘漂移比对键；export-to-tool-dir 成功时计算存下。库内容变更**无需置空**（库漂移走 `exported_content_sha256` 计算检测，见 §2 总则） |
| `exported_content_sha256` | str 可空 | **导出时库内容规范化指纹**（`root_config` + `global_params` + 按 `ordinal` 有序 cases 的规范化 JSON sha，`content_fingerprint(suite)`）——库漂移比对键；export-to-tool-dir 成功时计算存下。门禁重算当前库指纹与之比对（§3.3 第 3 步）：**结构性检测**，任何新增变更路径都不可能漏 |
| `is_active` | bool | 停用语义（软删） |
| `created_at` / `updated_at` | | |

### 1.2 test_case

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | PK | |
| `suite_id` | FK test_suite ondelete cascade | |
| `name` | str | testpoint name（用例描述）；**suite 内唯一**（`unique(suite_id, name)`——runtask 校验 `TESTPOINT_NAME_DUPLICATE` 是 error） |
| `ordinal` | int | 顺序（导出按此排序） |
| `times` | int 默认 1 | testpoint times |
| `enabled` | bool 默认 true | 用例启停 |
| `exec_descs` | JSON | 1..N 个执行描述：`[{type, apk, package, class, method, runner, device, args:{}, times}]`（字段对齐 `TestcaseExec`；`times` 为 P1a 补——`<testcase times="N">` 不还原就丢字节同构） |

> **标识符原样保留**（研究 §5.2）：class/method 名含疑似拼写错误（`RelaibalityOreoTestTranssion`）在导入/导出全链路不得「修正」——库内就是设备端 APK 真实标识符。

### 1.3 迁移与审计

- alembic additive（ADR-0008）；`test_case_result` **不在 P1**（P2）。
- 审计资源类型 `test_suite` / `test_case`（ADR-0015）。

## 2. API 契约（13 端点定稿）

> 风格对齐现有 `/api/v1` + `ApiResponse`；`record_audit` 调用模式同 `hosts.py:276`。
> P0 的 `POST /api/v1/mtbf/runtask/validate`（文件输入）**保留**——外部 agent 上传校验不建库的场景；与套件 validate 分工：前者验文件、后者验库内数据。

| 端点 | 鉴权 | 说明 |
|------|------|------|
| `GET /api/v1/test-suites` | 登录 | 列表（`?project_key=&is_active=&q=`）；返回 id/name/display_name/project_key/apk_binding/case 数/exported_sha256 |
| `POST /api/v1/test-suites` | admin | 创建空套件（name/display_name/project_key?/export_dir?/apk_binding?/root_config?/global_params?）——用例走 import 或 cases 端点 |
| `GET /api/v1/test-suites/{id}` | 登录 | 详情（root_config/global_params/apk_binding + case 数 + 审计摘要计数） |
| `PUT /api/v1/test-suites/{id}` | admin | 更新元数据（root_config/global_params/project_id/export_dir/apk_binding）；name 唯一校验。**库内容变更无需置空**——库漂移由门禁对 `exported_content_sha256` 重算检测（§3.3 第 3 步），重导即恢复 |
| `DELETE /api/v1/test-suites/{id}` | admin | 软删（`is_active=false`）；有 ACTIVE（RUNNING/QUEUED/PRECHECK）PlanRun 引用时 409（同 export 守卫） |
| `GET /api/v1/test-suites/{id}/cases` | 登录 | 用例列表（分页 + `?enabled=&q=`） |
| `POST /api/v1/test-suites/{id}/cases` | admin | 单条创建（name/ordinal/times/enabled/exec_descs；schema 校验复用 `_validate_suite` 单条逻辑）——改产物但**无需置空**（§2 总则） |
| `PUT /api/v1/test-cases/{id}` | admin | 更新（name/ordinal/times/enabled/exec_descs 整覆盖）——任何字段变更都改渲染产物，**无需置空**（§2 总则） |
| `DELETE /api/v1/test-cases/{id}` | admin | 硬删（P1 无历史结果引用；P2 `test_case_result` 引入后复议）——改产物但**无需置空**（§2 总则） |
| `POST /api/v1/test-suites/{id}/import` | admin | multipart：`file`=runtask.xml + `global`=UiAutomatorTestData.xml（可选）；**upsert 按 name**；更新 `source_sha256`/`root_config`/`global_params`/cases（ordinal 按文件顺序）；审计——改产物但**无需置空**（§2 总则） |
| `GET /api/v1/test-suites/{id}/export` | 登录 | 返回**渲染的 runtask.xml 字节**（`?times=N` 覆盖，`N<=0` 用库内默认）；`GET /api/v1/test-suites/{id}/global` 返回渲染的 UiAutomatorTestData.xml（两个消费文件各一出口） |
| `POST /api/v1/test-suites/{id}/validate` | 登录 | 校验**库内数据**：构造 `RuntaskSuite` → 复用 `_validate_suite`（名唯一/class-method 非空/`@@var` 引用在 `global_params.sim` 有定义）→ 返回 issues（与 P0 validate 同格式） |
| `POST /api/v1/test-suites/{id}/export-to-tool-dir` | admin | 渲染两文件 → **atomic write**（临时文件 + `os.replace`）到 `{STP_AEE_NFS_ROOT}/mtbf/{export_dir}/`；成功后更新 `exported_sha256`；**ACTIVE 引用守卫**（P1b 起，409）；审计 |

**列类型（P1a 实施决定）**：`root_config` / `global_params` / `exec_descs` 用 `JSON` 而非 `JSONB`——JSONB 按「长度优先再字节序」**重排对象键**，`args` 的 `wifiPWD`(7) 会排到 `wifiName`(8) 前，导出物随即失去逐字节同构（渲染层的固定属性序救不了：arg 顺序是文档顺序）。这三列不可检索，不需要 JSONB 索引/操作符。`apk_binding` 是数组（JSONB 保序）仍用 JSONB。

**导出一致性总则（结构性检测，非端点纪律）**：渲染产物由 suite 表 + test_case 行决定，改变它的变更路径**无法穷举**（新增/删除/改任意字段/import 都改产物）。因此**任何端点都不做「置空」**，两类漂移各有计算检测器（§3.3 第 3/4 步）：

- **库漂移** = 门禁重算当前库内容规范化 sha（`content_fingerprint(suite)`：`root_config` + `global_params` + 按 `ordinal` 有序 cases 全量，JSON `sort_keys` 规范化）与 `exported_content_sha256` 比对——库改了没导出在此拦截，**算出来的而非记出来的**；
- **磁盘漂移** = 磁盘文件 sha 与 `exported_sha256` 比对——导出后磁盘被人动过在此拦截。

漏维护置空纪律导致「库改了门禁放行」结构上不可能；新增任何变更路径只需改库，检测自动跟上。

**错误语义**：未知 suite 404；`GET export` 在库漂移时响应头/`issues` 提示 `EXPORT_STALE`（导出内容 ≠ 当前库，重导后恢复）；跨项目访问套件？——套件 `project_id` 是**配置数据归属**（D3b 门禁在派发时），API 不按项目隔离读（登录即可看全部，同 ADR-0029 登记簿开放读）。

## 3. D2 绑定与派发门禁（P1b）

> **v1.3 重写**：绑定机制为 **`plan.suite_id` 可空外键**（ADR-0030 v1.4，
> NULL = P0 文件真源模式不加门禁）。§3 全节按外键方案表述；旧机制叙述只留在 §8。

### 3.1 Plan ↔ Suite 绑定

- **DB**：`plan.suite_id` 可空 FK → `test_suite.id`（alembic additive，ADR-0008）+ 索引。
  NULL = P0 文件真源模式（存量兼容，不加门禁）；非空 = 托管模式（门禁全开）。
- **API**：`PlanCreate` / `PlanUpdate` 接受 `suite_name`（套件对外键，同 `suites.py`
  「外部 agent 以 name 引用」口径；F2 风格——数字 id 只留 DB）；未知 name 404；
  update 显式 `null` = 解绑。`_plan_out` 暴露 `suite_name`。
- **修订理由**（详见 ADR-0030 v1.4）：WiFi 注入保持参数逻辑唯一例外；
  「一计划一专项」现状下按 step 绑定过度泛化；FK 获 DB 引用完整性 + precheck 直连 join。

### 3.2 快照冻结

prepare 冻结（与 #401 的 `project_id` / `build_version` 同一函数点一次写齐）：

```
run_context.dispatch_suite = {suite_id, suite_name, exported_sha256,
                              exported_content_sha256, apk_binding, export_dir}
```

托管模式下冻结的是**准入时刻的基线指纹**——同快照两次 run 结果不同可归因
「清单被改」（D5）；precheck 重校验以活表套件行 + 冻结基线双读（§3.3）。

### 3.3 precheck 五步门禁（fail-fast）

挂 admission 既有链（`admission_pump.py` script_verify_failed 同层），
查找键 = `plan.suite_id`（join，无 JSON 解析）：

1. suite 存在且 `is_active`（无 → `suite_verify_failed: missing`）；
2. 已导出：`exported_sha256`/`exported_content_sha256` 非空 **且** `{NFS}/mtbf/{export_dir}/runtask.xml` 磁盘存在（无 → `suite_verify_failed: not_exported`）；
3. **库漂移**：`content_fingerprint(suite)` == `exported_content_sha256`（不等 → `suite_verify_failed: content_changed`——「库改了没导出」在此拦截；**计算检测**，与任何端点置空纪律无关）；
4. **磁盘漂移**：磁盘文件 sha256 == `exported_sha256`（不等 → `suite_verify_failed: sha_mismatch`——「导出后磁盘被人动过」，恢复该状态的字面语义；脚本 setup 的 `suite_sha256` trace 与此闭环）；
5. **D3b**：`suite.project_id` 非空时与目标设备 `device.project_id` 匹配（不等 → `suite_verify_failed: project_mismatch`）；套件空 = 通用放行。

任一步失败 Plan 准入 `script_verify_failed` 同层失败，**禁止带病派发**（ADR-0030 D2）。修复路径：第 3 步 → 重导（export-to-tool-dir 更新两指纹）；第 4 步 → 重导或恢复磁盘文件。

**联动**：绑定落地后，#402 的在途守卫从宽匹配（`mtbf_%` 前缀 + force 逃生阀）
升级为按 `suite_id` 精确匹配——仅当 ACTIVE Run 引用**同一套件**时 409；
跨套件并发导出不再互相阻塞。

### 3.4 脚本侧消费（P1b）

- **注入不再以用户声明为前提**：dispatcher 对 lifecycle 中 action 含 `mtbf_` 的步骤，
  在 plan.suite_id 绑定存在时自动注入 `{expected_testpoint_count, project}` 到
  step params（经 `STP_STEP_PARAMS` 通道）——`expected_testpoint_count` =
  `test_case` 启用计数；`project` = 套件 `export_dir`（替代 host 手工 env）。
  **绑定对 mtbf 脚本为强制**（ADR-0030 v1.8 翻转）：未绑定 mtbf 计划在
  preview/prepare 即以 `SUITE_BINDING_REQUIRED` 拒绝；非 mtbf 计划不受影响。
- **env 预置退役**：`STP_MTBF_EXPECTED_TESTPOINT_COUNT` 已摘出 `_FLEET_ENV_KEYS`
  ——fleet 单值旋钮在第二套套件上线当天即系统性出错（正确性悬崖，评审
  #400 #404 论据）。hot-update 白名单摘除 + 运维说明见
  [mtbf-api.md §1.5](../operations/mtbf-api.md)。
- `mtbf_finish` v1.4.0 的 `suite_sha256` 已闭环（NFS 未 patch 的 runtask.xml）——与门禁第 4 步的 `exported_sha256` 比对同一文件（D6 冒烟已实证逐字节相等）。

## 4. CLI（P1c）

`tools/dev/mtbf-cases.py`（单文件 kebab-case，对齐 `backfill-test-project.py` 先例；**选定回写 ADR-0030 修订记录**）：

```
python tools/dev/mtbf-cases.py list [--project MTBF-MLD]
python tools/dev/mtbf-cases.py show --suite MTBF-legacy --case test_Reliability0141_CloseStoreWlan
python tools/dev/mtbf-cases.py import --suite MTBF-legacy --file runtask.xml [--global UiAutomatorTestData.xml]
python tools/dev/mtbf-cases.py export --suite MTBF-legacy --out runtask.xml [--times 100]
python tools/dev/mtbf-cases.py validate --suite MTBF-legacy
python tools/dev/mtbf-cases.py export-to-tool-dir --suite MTBF-legacy
```

凭据：`--base-url` / `--token`，默认仓库根 `.env.backend` 的 admin 凭据来源约定；**明文不进 log**（AGENTS.md 安全约束）。

## 5. 文档与状态传播（P1c 收尾）

- `docs/operations/mtbf-api.md` §2 定稿（13 端点 curl + 权限矩阵 + 常见操作，对齐 §1 体例）；
- `docs/design/05-data-model.md` 补 test_suite/test_case 表行（ADR-0030 影响项）；
- 状态传播（ADR-0030 修订记录 v1.1 ⑥ 挂靠位）：ADR 头部/修订记录、adr README 清单+里程碑行、DOC-MAP、CLAUDE.md 决策表——P1 验收时同步。

## 6. 里程碑拆分与验收

### P1a：实体 + 服务层 + CRUD/import/export/validate + 审计

- alembic migration（test_suite/test_case）+ models + schemas；
- `services/mtbf_suite.py` 增渲染器：`render_runtask(suite)` / `render_global(suite)`（库 → XML 字节；root_config 权威、global_params.sim 渲染 `<SIM/>`、`@@` 引用原样保留、times 覆盖参数）；
- 路由 12 个端点（§2 表中 CRUD/import/export/validate/global）+ 审计；
- 测试：migration + model + 端点（testcontainers）+ **渲染 golden**（导入生产 130 条快照 → 导出 → 与源文件**除属性序外逐字节一致，含 CRLF**）+ `content_fingerprint` 确定性（字段序/属性序无关）+ 审计断言；
- **验收**：curl 走通「导入 130 条 → 改 1 条 → 导出渲染 → validate 0 error」，audit_logs 可见全操作。

### P1b：D2/D3b 绑定 + 门禁 + 脚本侧注入

- `inject_suite_params` + 冻结 run_context；admission 五步门禁 + 状态；
- `mtbf_setup`/`mtbf_check` 读注入参数（expected 替代 env；project 注入）；
- 测试：注入 golden（含已有值优先）、门禁矩阵（missing/not_exported/**content_changed**/sha_mismatch/project_mismatch）、`content_fingerprint` 对「新增/删除/改任意字段」六条变更路径全部翻转（库漂移必检）、脚本侧解析 env 新键；
- **验收**：Plan 引用 suite 的 precheck 矩阵全绿 + 真机冒烟一轮（`suite_sha256` trace == 门禁比对 sha）。

### P1c：CLI + 文档 + 状态传播

- `tools/dev/mtbf-cases.py` + `docs/operations/mtbf-api.md` §2 + `05-data-model.md` + ADR-0030 修订记录（P1 验收 + CLI 位置回写）；
- **验收（ADR-0030 D6 P1 验收信号）**：外部 agent 仅凭 API/CLI 完成「导入既有 130 条 → 改 1 条 → 导出 → 派发」，全程有审计。

## 7. 开放问题（实施前逐项关闭）

| # | 问题 | 建议 |
|---|------|------|
| 1 | `X-Agent-Secret` 写权限 | 初版**只读**（ADR 初版保守）；import/export 是否对 agent 放开 P1a 评审定，定后回写 ADR 修订记录 |
| 2 | `apk_binding` 校验时机 | 初版仅 D3b 项目匹配；APK 文件存在性/sha 走脚本 setup 既有 `apk_sha256[]` 留痕（不扩 precheck） |
| 3 | `global_params` 编辑粒度 | 初版整 JSONB 覆盖（PUT suite）；字段级编辑留 P2 前端 |
| 4 | 用例级编辑粒度 | `exec_descs` 整覆盖（PUT case）；单条校验复用 `_validate_suite` |
| 5 | 通用套件导出目录 | `export_dir` 空 → `legacy`（兼容 P0 部署）；项目套件 → project key；明确后写入 operations 文档 |
| 6 | 渲染字节保真 | **已实施，零容差**：渲染器手工产字节——CRLF 行尾、`@` 保留 `&#64;` 写法、固定规范属性序、末尾无换行，与 P0 已验证的设备面输入逐字节同构。130 testpoint 生产快照 parse→render 全等（76791B），API 层 import→export 经完整 DB 往返仍全等。golden 判据 = **逐字节一致**（无容差项） |
| 7 | `name` 唯一冲突 | 导入同名套件 upsert（按 name 匹配更新）；跨项目同名是否允许——初版不允许（name 全局唯一），分化靠 project_id 区分导出而非套件分裂 |

## 8. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-24 | **v1.4（P1b 实施回写）**：§3 全部落地（#404 PR-C/D）——冻结/注入/五步门禁/#402 精确化见 [ADR-0030 v1.5](../adr/ADR-0030-multi-case-suite-management.md) 修订记录与 [Agent Note](../notes/feature/2026-08-24-suite-binding-gates.md)；实施取舍：门禁判定以活表+磁盘为基准、冻结块承担归因；注入在物化时点（WiFi 同点）而非快照；env 退役双层同批（摘 `_FLEET_ENV_KEYS` + `mtbf_check` v1.3.0 只读注入） |
| 2026-08-24 | **v1.3（绑定机制重写）**：§3 按 ADR-0030 v1.4 重写——`plan.suite_id` 可空外键替代 plan_step.default_params 注入特例；注入不再以用户声明为前提；#402 守卫升级路径（精确匹配）；env 预置退役与 `_FLEET_ENV_KEYS` 摘除同批约束 |
| 2026-08-20 | 初版：P0 验收后 P1 衔接梳理定稿（实体/端点/绑定/门禁/CLI/里程碑） |
| 2026-08-20 | v1.2（P1a 实施回写）：① `global_params` 补 `test_set_attrs`、`exec_descs` 补 `times`（不还原则导出物丢 TakeScreenshot / testcase times）；② 三个渲染源列定为 `JSON` 而非 `JSONB`（JSONB 重排对象键破坏字节同构）；③ §7 #6 属性序容差消除，golden 判据收紧为逐字节一致 |
| 2026-08-20 | v1.1（评审修订）：① 导出一致性改**结构性检测**——新增 `exported_content_sha256`（库内容规范化指纹 `content_fingerprint`），门禁第 3 步计算检测库漂移，删除全部端点「置空」纪律（枚举法漏 3 条变更路径的洞：POST cases / DELETE cases / PUT case 改非 name 字段）；sha_mismatch 恢复「磁盘漂移」字面语义，两类漂移各有检测器；② 渲染器定稿产出 CRLF（与 P0 设备面输入同构），golden 容差仅剩属性序 |
