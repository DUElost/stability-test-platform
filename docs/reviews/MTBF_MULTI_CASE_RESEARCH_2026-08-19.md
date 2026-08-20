# MTBF 多用例平台化研究（runtask.xml → 平台形态雏形）

- 日期：2026-08-19
- 性质：**背景研究（非 ADR、非设计定稿）**。回答「MTBF 专项的多条自动化测试用例（多用例）在平台上以什么形态呈现与管理」，供后续 ADR/设计引用。
- 研究对象：`/mnt/automation-toolkit/android-tools/stability_MTBF-Test/config/runtask.xml`（130 testpoint / 137 testcase）与平台现状（ADR-0020 / ADR-0029 / Plan-Step-Job 模型）。
- 相关文档：[ADR-0020](../adr/ADR-0020-plan-step-one-shot-migration.md)、[ADR-0029](../adr/ADR-0029-project-taxonomy-and-param-layering.md)、[PROJECT_TAXONOMY_REVIEW_2026-08-18](./PROJECT_TAXONOMY_REVIEW_2026-08-18.md)、[00-system-overview](../design/00-system-overview.md)、[05-data-model](../design/05-data-model.md)、[07-execution-protocol](../design/07-execution-protocol.md)。

## 0. 结论摘要（TL;DR）

1. `runtask.xml` 是**设备端用例清单**：一套「模拟老化+MTBF」用例集，130 个 testpoint（用户视角的「用例」）、137 个 testcase（执行描述），全部 uiautomator2，引用 3 个测试类共 137 个方法；由设备端 `OfflineScriptManager` APK 读取执行，结果落在 `/sdcard/results/realresult/*.xml`（逐 testpoint 的 PASS/FAILURE/ERROR/INCOMPLETE）。
2. 平台**目前没有「用例」实体**：`Script`（可执行脚本）与 `Plan/PlanStep`（编排）之间没有「用例清单/用例」这一配置层；「多用例」的落点应是**用例元数据层**，而不是把 130 个用例展开成 130 个 PlanStep（MTBF 的语义是「整套循环 N 圈」，不是逐条编排，且唯一 action 类型 `script:<name>` 是不变量）。
3. **推荐雏形 = 分阶段演进**：
   - **P0（让 MTBF 先跑起来）**：脚本三件套 `mtbf_setup` / `mtbf_check` / `mtbf_finish`（deploy/start、轮询 + PROGRESS、stop/pull + 解析 realresult → stdout JSON 摘要），runtask.xml 维持文件真源，平台加**只读预览 API**（解析工具目录 runtask.xml 返回结构化用例表），step_trace 记用例清单 sha256（沿用 ADR-0029 v2.2 的补偿机制）。
   - **P1（多用例主体，外部管理面）**：新建 `test_suite` / `test_case` 表（用例粒度 = testpoint，内含 1..N 个 testcase 执行描述），CRUD + `import`（从 runtask.xml 导入）+ `export`（渲染回 runtask.xml）+ `validate` API，全部走审计；配套 CLI 薄封装（`tools/` 下单文件脚本）；导出产物落工具目录供脚本消费。
   - **P2（体验完善）**：前端用例管理页 + PlanRun 详情页「逐条用例结果」表（realresult 解析入库 `test_case_result`）。
4. **外部管理面**：复用控制面 8000 端口 REST（FastAPI 自带 `/docs` Swagger + `/openapi.json` 即机器可读接口文档），**不新增端口**（Agent `:8900` 已按 ADR-0025 取消暴露）；鉴权复用现有双通道（用户 Bearer/Cookie + `X-Agent-Secret`）；CLI 作为便捷层走同一 REST。

---

## 1. 探究对象：runtask.xml

### 1.1 位置与角色

```
/mnt/automation-toolkit/android-tools/stability_MTBF-Test/
├── apk/            # OfflineScriptManager.apk + 2 个用例 APK（ReliabilityUiautomatorTest*.apk）
├── config/
│   ├── runtask.xml            # ← 本研究的对象：离线任务用例清单
│   ├── UiAutomatorTestData.xml# 全局参数（WiFi/账号/SIM）+ TestPackage 参考块
│   └── uiautomatorconfig      # 占位符（参考用）
├── scripts/        # deploy/run/stop(.bat/.ps1) + lib.ps1（adb 编排）
└── test-config.properties     # task.times / tester.name / auto.start / auto.resume
```

`runtask.xml` 由 `deploy.bat/ps1` 经 `Set-RuntaskTimes`（可选 patch `times`）后 push 到 `/sdcard/runtask.xml`，设备端 `OfflineScriptManager`（system uid）读取执行。README 明示「从平台任务 XML 转换，或从 `apps/OfflineScriptManager` 同步」——即它本来就是**从在线 MTBF 平台任务 XML 转换而来**的离线形态，天然适合「平台侧生成、设备端消费」。

### 1.2 结构统计（实测解析）

| 维度 | 值 |
|------|-----|
| 根元素 | `<runtask name="模拟老化(仿RM老化+MTBF)_Trassion_2023_8_23" times="1000" testTimeOut="259200000" takeScreenshot="true" stopWhenFail="false" taskRegressionType="0" caseRegressionType="1" testpointRegressionTimes="1">` |
| testpoint | **130**（用户视角的用例；均 `times="1"`） |
| testcase | **137**（其中 5 个 testpoint 含 2~4 个 testcase，如「开关飞行模式/移动数据/手电筒/蓝牙/GPS/屏幕旋转」聚合测试点 4 个 case） |
| type | 100% `uiautomator2` |
| apk / runner | 100% `ReliabilityUiautomatorTestTest.apk` / `androidx.test.runner.AndroidJUnitRunner` |
| class | 3 个：`ImitationRM_AgeingTest`(78)、`ReliabilityOreoTest`(52)、`RelaibalityOreoTestTranssion`(7) |
| method | 137 个互异：129 个 `test_ReliabilityNNNN_xxx` 编号用例 + 8 个命名用例（`test_Photos`、`test_AI_Gallery`、`test_Notebook`、`CreateNewContactInTheDialingInterface` 等） |
| 参数 | 仅 `wifiName`/`wifiPWD`（9 个用例），值形如 `@@gWifiName`（引用 `UiAutomatorTestData.xml` 全局变量） |

testpoint 名称即用例描述（中文），如「关闭软件商店的Wian自动更新消息提醒开关」「文件创建删除压力测试(100W次)反复创建和删除1k文件」「重复打开Photos，传音项目」。

### 1.3 语义要点

- **分层**：`testpoint` = 用例（名称、次数、可含多个 `testcase`）；`testcase` = 执行描述（device/apk/package/class/method/runner/attribute 参数）。设备端结果 XML 也以 testpoint 为粒度（`<testpoint id name tests status>` + `<testcase .../>` 子元素）——**平台「用例」实体粒度应对齐 testpoint**。
- **全局变量**：`@@gWifiName` 等由 `UiAutomatorTestData.xml` 提供（SIM/WiFi/账号），即「用例参数外置」的既有先例，平台化时应保留这一层（suite 级 global_params）。
- **根属性即套件级配置**：`times`（整套循环次数，默认 1000，部署时可由 `task.times` 覆盖）、`testTimeOut`（259200000ms = 72h 整套超时）、`takeScreenshot`、`stopWhenFail`（false = 失败继续）、`taskRegressionType` / `caseRegressionType` / `testpointRegressionTimes`（回归/重跑语义，具体取值含义待与设备端代码核对）。
- **执行链路**：设备端逐 testpoint 顺序执行 → 结果写 `/sdcard/results/realresult/*.xml`（`<testpoints taskname>` 根，`PASS/FAILURE/ERROR/INCOMPLETE` 状态；代码见 `apps/OfflineScriptManager/apk_sources/.../utils/l/f.java`、`b/b/a/a/b/d.java`）→ `adb pull` 收取。
- **循环语义**：MTBF 是「整套用例反复循环」长跑（默认 100 轮 ≈ 7 天），**不是**把 130 条用例当 130 个可独立派发步骤。

### 1.4 对平台化的三个事实

1. runtask.xml 是**生成物友好**的（结构规整、可解析、可渲染），且 README 已声明其来源是平台任务 XML 转换。
2. 「用例清单」与「执行脚本」是两层：清单变（增删用例/改参数）≠ 脚本变。把清单塞进脚本 `default_params` 或脚本文件都不合适（130 条 × 多项目会触发 ADR-0020 版本膨胀问题）。
3. 逐项目差异（ADR-0029 R3，**2026-08-19 决策者确认**）：用例 APK 与项目**严格对应**（某版本 APK 只能跑对应项目）；现有 MTBF 的 `runtask.xml` 用例内容**大部分情况稳定不变**；但**后续新增的相机 MTBF 用例集将随项目变化较频繁**。设计结论：`test_suite.project_id` 可空 = 通用套件（现 MTBF 现状），项目套件（相机 MTBF 等）必填 `project_id` 并在套件级声明用例 APK 绑定，派发时校验项目匹配。

---

## 2. 平台侧现状与约束

### 2.1 执行模型（ADR-0020 / ADR-0025）

- `Plan`（一个专项）→ `PlanStep`（init/patrol/teardown 三阶段）→ 派发时组装 `lifecycle`（`build_lifecycle_from_steps`，`plan_dispatcher_core.py:166`）；唯一 action 类型 `script:<name>`。
- **每设备一个 Job**（`job_instance.device_id` 非空，`uq_job_active_per_device` 部分唯一索引），Job 承载整条 lifecycle。
- 脚本契约：`STP_DEVICE_SERIAL` / `STP_STEP_PARAMS`（JSON，源自 `script.default_params`，dispatcher `deepcopy` 后下发）环境变量；stdout 输出一行 JSON `{"success", "error_message", "metrics"}` → step_trace（output 全量保留）。
- 步骤参数唯一来源 `default_params`（`plan_dispatcher_core.py:187`），**版本即参数**（已存在版本 `default_params` 422 不可变）；WiFi 注入是唯一注入特例。
- 长跑支持：patrol 阶段按 `patrol_interval_seconds` 循环执行 patrol steps；`PROGRESS` 打戳（stderr `PROGRESS {...}` 行）驱动停滞钟（#115，`capabilities.json: ["progress_stamps"]` 登记）。

### 2.2 结果载体

| 载体 | 用途 | 现状 |
|------|------|------|
| `step_trace`（output / exit_code / step_metadata） | 步骤级结果，前端全程透传 | 有 |
| `report_json`（job_instance） | Job 级报告（post_completion 填充） | 有 |
| `JobArtifact` | 文件产物下载 | **白名单仅 `aee_crash` / `vendor_aee_crash` / `bugreport`**（`agent_api.py:2448`）——MTBF 结果 XML/汇总文件需扩白名单或走 report_json |
| `job_log_signal` / `device_log_event` | 崩溃/ANR 观测 | 与用例结果正交，MTBF 长跑可顺带受益 |

### 2.3 项目域（ADR-0029，已 Accepted）

- `test_project`（登记簿）+ facet；`plan.project_id` / `device.project_id` 归属列。
- **R3 已决策**：APK 差异由**脚本端设备指纹路由**吸收；路由表住**工具目录**（`test-config.properties` 等，随专项工具），不受 ADR-0020 约束（无版本/sha 审计留痕），补偿 = step_trace 记路由表文件 sha256，两次 run 结果不同可归因「映射被改」。
- MTBF 在 ADR-0029 中的定位：`script.default_params` 示例即 `{"tool_dir": "/mnt/automation-toolkit/android-tools/stability_MTBF-Test"}`；用例 APK 逐项目不同（MLD/ELA 同族内共用）。

### 2.4 缺口清单（「多用例」要补的）

| # | 缺口 | 现状 |
|---|------|------|
| G1 | 用例清单的**结构化呈现** | 无：平台上既看不到 MTBF 跑的 130 条用例，也看不到逐条结果 |
| G2 | 用例清单的**管理（增删改/导入导出/校验）** | 无：只能直接改工具目录 XML 文件，无校验、无审计 |
| G3 | 用例级**结果回填** | 无：realresult XML 依赖人工 adb pull 查看 |
| G4 | 逐项目**用例分化** | 无：runtask.xml 单文件，无项目维度（跟随 ADR-0029 的项目域落地后可挂 `project_id`） |
| G5 | **外部管理面**（接口/CLI/文档） | 脚本/Plan 已有 REST（`/api/v1/scripts`、`/api/v1/plans`）+ OpenAPI；用例域为零 |

---

## 3. 「多用例」需求分解

### 3.1 展现需求（平台上如何呈现）

- **静态**：套件视图——一次 MTBF 跑的是哪套用例集（名称/版本/根配置/全局参数），130 条用例的表格式清单（名称、class、method、参数、次数、启用态）。
- **动态**：运行结果——某次 PlanRun 里逐条用例的 PASS/FAIL/ERROR/INCOMPLETE、失败详情、截图/日志入口；与现有 step_trace、PlanRun 风险评级（log_signal 聚合）并行展示。
- **按项目**：项目视角下该项目的用例集（ADR-0029 项目页可挂「用例」tab）。

### 3.2 维护需求（可维护性）

- 用例增删、顺序调整、参数修改、套件级配置（times/tester/截图开关）修改。
- 导入（既有 runtask.xml 一键入库）与导出（库 → runtask.xml 渲染，供设备端消费）。
- 校验（XML schema、重复 method、`@@变量` 引用完整性；APK 内 class/method 存在性需 aapt/运行时校验，离线只能尽力）。
- 变更可追溯（审计，参照 ADR-0029 D2 对 `variables` 的审计要求）；运行可复现（快照/哈希留痕）。

### 3.3 外部管理需求（外部 agent 的运维面）

候选形式评估：

| 形式 | 现状基础 | 评估 |
|------|----------|------|
| **REST API（推荐主通道）** | 控制面 8000 已有 `/api/v1/*` + 鉴权（用户 Bearer/Cookie）+ `X-Agent-Secret` 通道 + 审计骨架；FastAPI 自动生成 `/docs`（Swagger UI）与 `/openapi.json`（机器可读） | 外部 agent（AI 编码 agent、CI、运维脚本）用 token 直接调；OpenAPI 即接口文档，无需另写 |
| **CLI 薄封装（推荐便捷层）** | 仓库 `tools/dev/` 已有单文件 Python 工具先例 | 封装 CRUD/import/export/validate，走同一 REST，兼作人工运维入口 |
| 新开端口 | — | **不推荐**：无新服务可开；Agent `:8900` 已按 ADR-0025 取消暴露；8000 为控制面唯一 API 面，隔离需求走反向代理/网关 |
| 独立接口文档页 | `docs/operations/`、`docs/design/` | 补一篇**管理接口说明**（curl 示例 + 鉴权），但以 OpenAPI 为真源 |

### 3.4 明确非目标

- **不**把 130 个用例建模为 130 个 PlanStep / Job（破坏「整套循环」语义，且每设备每轮 130 条 step_trace 无意义）。
- **不**引入新 action 类型（`script:<name>` 唯一 action 是不变量）。
- **不**把用例清单塞进 `script.default_params`（版本膨胀 + 参数不可变约束）。
- **不**在 P0 做用例级结果入库（P2 再做 `test_case_result`）。

---

## 4. 候选形态（雏形方案）

### 方案 A：清单留工具目录（文件即真源，平台只读）

- 现状延续：runtask.xml 留在工具目录，脚本引用（`tool_dir` 参数）。
- 平台只加：只读预览 API（解析 XML → 结构化用例表）+ 校验 API + step_trace 记 sha256。
- 优点：改动最小、迁移最快、完全符合 ADR-0029 v2.2「路由表住工具目录」先例。
- 缺点：无审计、无版本、无 UI 编辑、外部 agent 写路径仍是「改共享盘文件」（无校验、无留痕）——不满足「在平台上进行管理」的诉求，只算过渡。

### 方案 B：用例一等实体（test_suite / test_case + 生成器）

- 新表 + CRUD API + import/export/validate + 审计；前端管理页；用例集按项目分化（`project_id` 可空）；runtask.xml 变成**生成物**（导出渲染）。
- 执行链：`mtbf_*` 脚本组运行时读工具目录的导出文件（或平台下发）→ push 设备。
- 优点：可维护性/审计/外部管理面完整，是「多用例」的终局形态。
- 缺点：建表 + 迁移 + API + 页面 + 权限，工作量最大；需要与「配置住脚本」哲学对齐（用例库是**配置数据**，不是执行实体，不进 STP_SCRIPT_ROOT）。

### 方案 C：混合分阶段（推荐雏形）

```
P0  [让 MTBF 先跑]  mtbf_setup/mtbf_check/mtbf_finish 脚本组 + realresult 解析 + 只读预览/校验 API + sha256 留痕
P1  [多用例主体]    test_suite/test_case 表 + CRUD/import/export/validate + 审计 + CLI + 导出落工具目录
P2  [体验完善]      前端用例管理页 + PlanRun 逐条用例结果表（test_case_result）
```

- P0 与 P1 之间无缝：P1 的 import 就是把 P0 的「文件真源」一次性搬进 DB，导出后回到文件形态，脚本消费路径不变（**管理面从文件升级为 API，消费面不变**）。
- 每阶段独立可用、可回退；P1 若遇阻（如项目分化需求不清），P0 + 文件编辑仍能跑。

**推荐理由**：多用例的诉求（展现 + 管理 + 外部 agent 接口）指向 B 的终局；但平台「脚本烟囱化」教训与 ADR-0020/0029 的既有决策要求先让专项在平台上跑起来、再谈机制（参照 ADR-0025「先确保专项执行闭环可稳定无人值守运行」的优先级排序）。C 恰好把「能跑」与「能管」解耦。

---

## 5. 推荐形态设计草图（供后续 ADR 展开）

### 5.1 数据模型（草案）

```
test_suite                          # 用例集（≈ 一个 runtask.xml）
  id, name(unique), display_name
  project_id            FK test_project, nullable   # 按项目分化：NULL=通用套件（现 MTBF），
                                                    # 必填=项目套件（相机 MTBF 等，2026-08-19 确认）
  apk_binding           JSONB, nullable             # 用例 APK 绑定（文件名/路径，随项目严格对应）；
                                                    # 派发时校验 suite 与目标设备项目一致
  version               # 管理版本（v1 先做「快照留痕」，不做 copy-on-write 版本库）
  root_config           JSONB   # times / testTimeOut / takeScreenshot / stopWhenFail / regression*
  global_params         JSONB   # UiAutomatorTestData.xml 等价物（wifiName/wifiPWD/账号/SIM…）
  source_sha256         # 导入时记录文件哈希（溯源 + 留痕）
  is_active, created_at, updated_at

test_case                           # 用例（粒度 = testpoint）
  id, suite_id          FK
  name                  # testpoint name（用例描述）
  ordinal               # 顺序
  times                 # testpoint times（默认 1）
  enabled
  exec_descs            JSONB   # 1..N 个 testcase 执行描述：
                              # [{type, apk, package, class, method, runner, args:{wifiName:...}}]

test_case_result        # P2：运行结果
  id, plan_run_id, job_id, suite_id, case_id
  status                # PASS / FAILURE / ERROR / INCOMPLETE
  detail, artifact_uri, created_at
```

> 版本策略：P1 先以「派发快照 + sha256 留痕」满足可复现（ADR-0029 v2.2 同款补偿机制）；正式版本化（如套件间 diff/回滚）等真实分化需求出现再引入，避免过早机制化。

> **Plan ↔ Suite 绑定**（precheck 闭环前提，ADR-0030 D2 同款）：`plan_step.default_params` 显式声明
> `suite_key`（初版机制，与 WiFi 注入并列的注入特例；若 ADR-0029 挂起 D1 复议通过再改走 `params_override`）；
> 派发快照 `plan_snapshot` / `plan_run.run_context` 冻结 **`suite_id` / `exported_sha256` / `apk_binding`** 三字段；
> precheck 校验链：suite 存在且激活 → 已导出 → 磁盘文件 sha256 与库内一致 → 套件项目与目标设备项目匹配（D3b）→ 否则 fail-fast。

### 5.2 导入/导出/校验

- **导入**：解析 runtask.xml → `test_suite` + `test_case` 行；已有记录走 upsert（按 suite name）；记录 `source_sha256` 与审计。
- **导出**：DB → runtask.xml 渲染（含 `times` 覆盖参数、`@@全局变量` 引用保留）；可选直接写入工具目录 `config/runtask.xml`（管理员动作，见 §5.5 并发安全）。
- **校验**：XML 良构、testpoint/testcase schema、重复 method、`@@var` 引用完整性、testpoint 名唯一性；APK 内 class/method 存在性离线不可验 → 记录为「运行时校验」项（脚本执行前可 `aapt dump` 或 `adb shell pm` 检查 apk 是否存在，fail-fast）。
- **global_params 与 UiAutomatorTestData.xml 策略（P1 定）**：`global_params` 与套件同库管理（SIM / wifiName / wifiPWD / 账号 等价物）；
  runtask.xml 的 `@@var` 引用**原样保留**（库内不展开为字面值），导出时原样还原——两条链路都不做值展开；
  validate 校验「runtask.xml 引用的每个 `@@var` 在 `global_params` 中都有定义」，未定义 fail 并列出缺失项。
  `UiAutomatorTestData.xml` 的 TestPackage 参考块（非执行依据）不建模，随 `global_params` 导出为等价文件即可。
- **标识符原样保留**：class/method 名（含疑似拼写错误，如 `RelaibalityOreoTestTranssion`）在解析/导入/导出
  全链路**原样保留、不得「修正」**——它们是设备端 APK 内的真实标识符，任何「修正」都会制造跑不起来的用例。

### 5.3 执行链（对齐现有契约）

```
Plan: MTBF 专项（specialty=MTBF, project_id=...）
  init     → script:mtbf_setup   params: {tool_dir, suite 导出文件路径, task_times, tester}
  patrol   → script:mtbf_check   patrol_interval_seconds: 300   # 轮询设备端进度 + PROGRESS 打戳
  teardown → script:mtbf_finish  # stop + adb pull realresult → 解析 → stdout JSON 摘要
```

- 三个脚本按 monkey 先例拆分（`monkey_setup/launch/test/teardown` 同构，本专项为 setup/check/finish）；
  「单入口 + 阶段参数」备选已否决（命名统一见 ADR-0030 D1，两种形态不允许并存）。
- `mtbf_check` 必须接入 `PROGRESS` 打戳并配 `stall_seconds`（7 天长跑，停滞判据必须有业务进度戳；对应 #115 阶段 2 的接入条件）。设备端进度来源：`/sdcard/results/realresult/` 文件变化 / logcat `TestRunner` 行 / 已跑轮次计数。
- `mtbf_finish` stdout JSON：`{"success": true, "metrics": {rounds, total, passed, failed, error, incomplete}, "detail": "<结果文件路径>"}`。**结果落库主路径（P0 定）**：解析后的用例级 JSON 写 `job_instance.report_json`（既有列）+ step_trace metrics 摘要——P0 **无需扩 JobArtifact 白名单**；白名单扩展报告类型（如 `report`）留待 P2 大文件/下载场景（走既有幂等/白名单机制）。
- **派发门禁**：precheck 校验链见 §5.1「Plan ↔ Suite 绑定」（suite 存在 → 已导出 → 磁盘 sha256 与库一致 → 项目匹配 D3b）；step_trace 记录清单 sha256（ADR-0029 v2.2 补偿机制同款）。
- Patrol 期间 AEE/ANR 采集（watcher/log_signal）天然并行，不冲突。

### 5.4 平台展现（P2）

- 新页面：用例集管理（列表 → 详情：用例表格 + 导入/导出/校验按钮 + 审计入口），挂 `frontend/src/pages/`（候选 `orchestration/` 或独立 `suites/`）。
- PlanRun 详情页：新增「用例结果」区块（P2 读 `test_case_result`，P0/P1 期间至少能看到 `mtbf_finish` 的 step_trace output 摘要 JSON）。
- 项目页（ADR-0029 落地后）：项目维度看用例集。

### 5.5 外部管理面（API + CLI + 文档）

**REST（草案，风格对齐现有 `/api/v1` + `ApiResponse`）**：

```
GET    /api/v1/test-suites                    列表（?project_key=&is_active=）
POST   /api/v1/test-suites                    创建（admin）
GET    /api/v1/test-suites/{id}               详情（含 root_config/global_params）
PUT    /api/v1/test-suites/{id}               更新元数据（admin）
DELETE /api/v1/test-suites/{id}               停用（is_active=false，admin）
GET    /api/v1/test-suites/{id}/cases         用例列表（分页）
POST   /api/v1/test-suites/{id}/cases         新增用例（admin）
PUT    /api/v1/test-cases/{id}                更新（name/ordinal/times/enabled/exec_descs）
DELETE /api/v1/test-cases/{id}                删除
POST   /api/v1/test-suites/{id}/import        从 runtask.xml 导入（multipart，upsert）
GET    /api/v1/test-suites/{id}/export        导出 runtask.xml（?times= 覆盖）
POST   /api/v1/test-suites/{id}/validate      校验（返回问题清单）
POST   /api/v1/test-suites/{id}/export-to-tool-dir  写入工具目录（admin，写前校验）
```

- 鉴权：读 = 登录用户；写 = admin（或 owner，参照 `_require_plan_owner_or_admin`）；外部 agent 可用 `X-Agent-Secret`（已有先例 `scripts.py:_try_verify_agent`）或用户 token；**初版写 = admin，`X-Agent-Secret` 只读或限定 import/export**（P1 评审定）；全部写操作 `record_audit`（ADR-0015，参照 ADR-0029 D2 对 variables 的审计要求）。
- **P0 预览/校验 API 的输入源语义（P0 写死）**：支持两种输入——① multipart 上传文件（**主路径**，不依赖工具目录可达性）；② 控制面可达路径读取（`tool_dir` 在控制面可见时）。Agent 侧不可达工具目录**不影响**预览能力；预览只承诺「控制面能拿到的文件」，P1 起以库内数据为准。
- **export-to-tool-dir 并发安全**：落盘采用 **atomic write**（临时文件 + rename）；存在引用该套件的 ACTIVE（RUNNING / QUEUED / PRECHECK）PlanRun 时拒绝覆盖（409，参照 scripts scan `force_rebaseline` 在途守卫），或先写 staging 目录再显式切换。
- **CLI**：`tools/mtbf_cases.py`（或 `stpctl cases`），参数示例：

```
python tools/mtbf_cases.py list --project MTBF-MLD
python tools/mtbf_cases.py import --suite MTBF-MLD --file runtask.xml --mode upsert
python tools/mtbf_cases.py export --suite MTBF-MLD --out runtask.xml --times 100
python tools/mtbf_cases.py validate --file runtask.xml
python tools/mtbf_cases.py show --suite MTBF-MLD --case test_Reliability0141_CloseStoreWlan
```

（CLI 读 `--base-url`/`--token`，默认取仓库根 `.env.backend` 的 admin 凭据来源约定，明文不进 log。
CLI 位置与命名实施时在仓库先例内二选一：`tools/dev/` 单文件 kebab-case vs 独立 `tools/stpctl/`，选定后回写 ADR-0030 修订记录。）

- **接口文档**：OpenAPI（`/docs`）为真源；另在 `docs/operations/` 补一篇「MTBF 用例管理接口说明」（curl 示例 + 权限 + 常见操作），供外部 agent/人工快速上手。

---

## 6. 可维护性与治理要点

1. **审计**：suite/case 的 create/update/delete/import/export/export-to-tool-dir 全部 `record_audit`——「谁在何时把哪条用例改成了什么」可追溯（这是「文件真源」模式最缺、P1 补上的核心价值）。
2. **可复现**：PlanRun 快照（`build_lifecycle_from_snapshot` 既有机制）+ 清单 sha256 留痕；同快照两次 run 结果不同可归因「清单被改」。
3. **版本边界**：用例库是配置数据，**不**进 `STP_SCRIPT_ROOT`（那是可执行脚本的版本化目录）；脚本版本（`mtbf_setup`/`mtbf_check`/`mtbf_finish` 等）照常走 ADR-0020 扫描/不可变；渲染器作为脚本辅助模块（`_` 前缀）住脚本版本目录。
4. **按项目分化**：`test_suite.project_id` 可空 = 通用套件；分化后按项目过滤（跟随 ADR-0029 项目域；是否真的需要逐项目分化见开放问题 1）。
5. **不重复造轮子**：校验/导入导出只在控制面服务层实现一次（`services/test_suite.py`），API/CLI/前端/脚本共用。

---

## 7. 风险与开放问题

| # | 问题 | 影响 | 建议 |
|---|------|------|------|
| 1 | 逐项目 runtask.xml 是否分化（MLD/ELA 的 class/method 是否一致）？ | 决定 `project_id` 是否必须、套件是 1 份还是 N 份 | **已确认（2026-08-19）**：现 MTBF 清单大部分稳定 → 通用套件（project_id 可空）；相机 MTBF 将随项目频繁变化 + APK↔项目严格对应 → 项目套件（必填）+ 套件级 apk_binding，已入 §5.1 模型草案 |
| 2 | 设备端 realresult XML 的精确 schema（含回归/重跑字段语义：`caseRegressionType=1` 等） | 解析器正确性；PROGRESS 指标依赖 | **已定稿**（2026-08-19 读透反编译代码，见 [P0 设计 §2](../design/2026-08-mtbf-p0-runner-design.md)）；真机采样复核留 P0 实施冒烟（§6 验证计划） |
| 3 | 工具目录在 **Agent 执行机**的可达性（`/mnt/automation-toolkit` 若仅控制面可见，脚本运行时读不到） | 专项接入前置条件（PowerCycle 同问） | **方案已定**（P0 设计 §4 推荐）：清单/全局参数走中心存储 `{STP_AEE_NFS_ROOT}/mtbf/{project}/`，APK 走 Agent resources 目录（aimonkey 先例），逐条结果写回 `mtbf/{project}/results/`；与 PowerCycle 统一，实施 PR 对齐 |
| 4 | `times` 覆盖链：runtask 默认 1000 vs `task.times=100` vs 平台参数 | 平台参数应成为唯一旋钮 | **已定**：`task_times` 仅影响 export/deploy（渲染/部署时的覆盖参数）；库内 `root_config.times` 为套件默认值 |
| 5 | JobArtifact 白名单（aee_crash/vendor_aee_crash/bugreport）不含报告类型 | 结果文件无法入库下载 | **已定**：P0 优先 `report_json` + step_trace metrics（既有列）；P2 大文件/下载场景再扩展白名单加 `report` 类 |
| 6 | 外部 agent 写权限模型：`X-Agent-Secret` 是否放开写、还是专用 API token | 安全边界 | **已定初版**：写 = admin；`X-Agent-Secret` 只读或限定 import/export（P1 评审确认） |
| 7 | 用例数膨胀（130 条 × 多项目 × 版本）后是否需要正式套件版本化 | 维护成本 | 触发条件出现再引入（同 ADR-0029 复议模式），暂不机制化 |

---

## 8. 落地路线（建议排期）

| 阶段 | 内容 | 依赖 | 验收信号 |
|------|------|------|----------|
| **P0** | `mtbf_setup`/`mtbf_check`/`mtbf_finish` 脚本组（deploy/start、轮询 + PROGRESS 打戳 + stall_seconds、stop/pull + realresult 解析）；只读预览/校验 API（输入源语义见 §5.5）；清单 sha256 留痕 | 见 [P0 设计 §6](../design/2026-08-mtbf-p0-runner-design.md)（原阻塞项 2/3 已关闭） | 一个 Plan 在真机上跑通 MTBF 一轮，PlanRun 详情可见用例摘要 JSON |
| **P1** | `test_suite`/`test_case` 表 + CRUD/import/export/validate + 审计 + CLI + 导出落工具目录 | P0 | 外部 agent 仅凭 API/CLI 完成「导入既有 130 条 → 改 1 条 → 导出 → 派发」，全程有审计 |
| **P2** | 前端用例管理页 + PlanRun 逐条用例结果表（`test_case_result`） | P1 + 开放问题 2 | 平台页面上可浏览用例集与逐条结果，无需 adb |

每阶段独立可交付；P1 是「多用例」主体（用户诉求的展现/维护/外部管理面），P0/P2 是它的前后置。

---

## 9. 参考资料

- 研究对象：`/mnt/automation-toolkit/android-tools/stability_MTBF-Test/`（README、`config/runtask.xml`、`config/UiAutomatorTestData.xml`、`test-config.properties`、`scripts/*.ps1`）
- 设备端执行器：`/mnt/automation-toolkit/android-tools/apps/OfflineScriptManager/apk_sources/`（`utils/l/f.java` 结果线程、`utils/m/*.java` testpoint 解析、`b/b/a/a/b/d.java` 结果 XML 序列化）
- 平台：ADR-0020（脚本目录契约）、ADR-0025（方案 C 存储）、ADR-0029（项目分类域）、ADR-0015（审计）、#115（PROGRESS 停滞钟）、`docs/design/00-system-overview.md`、`docs/design/07-execution-protocol.md`
