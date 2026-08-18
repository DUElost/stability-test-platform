# ADR-0029: 项目分类域（TestProject 登记簿 + facet 分类）

- 状态：Proposed（2026-08-18 **决策转向**，见修订记录）
- 优先级：P1
- 目标里程碑：M7
- 日期：2026-08-18
- 决策者：平台研发组
- 标签：项目域, 多项目并存, jira 映射, 脚本路由
- 背景分析：[`PROJECT_TAXONOMY_REVIEW_2026-08-18.md`](../reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md)

## 修订记录

| 日期 | 版本 | 内容 |
|------|------|------|
| 2026-08-18 | v1 | 初版：D1 步骤参数覆盖 + D4 三层参数 + D5 派发门禁 + D7 存储命名空间 + D8 全局项目上下文；D1–D9 经评审全部采纳 |
| 2026-08-18 | **v2（决策转向）** | 决策者提出「脚本端按设备指纹自行路由到执行器能否弱化项目属性」，核实后确认：开关机专项的 `backend=auto`（`/mnt/automation-toolkit/android-tools/stability_PowerCycle-Test/test-config.properties`：Z2582 无 REBOOT 时自动用 MSSV）即**单入口 + 设备能力路由**的既有先例 → **APK 差异由脚本路由吸收，R3 的「结构性阻塞」解除**。项目模型回归**登记簿**定位（知识层：客户 / 项目关系 / 形态 / jira 映射——adb 指纹读不出的部分，见背景分析 §4.1）；执行差异归脚本（路由 + step_trace 记录路由决策 + 未匹配 fail-fast）。**D1 / D4 / D5 / D7 / D8 / D9 挂起**，D6 保留 `specialty`、挂起 `applicable`；D2 新增 `jira_project_key` |
| 2026-08-18 | v2.1（审查确认 F1–F6） | ①**LEGACY vs NULL 二义**（F1）：定稿 LEGACY 为真实项目行，NULL 降级为**迁移期瞬态**——M-c 完成标准 = 回填后 `device.project_id` 无 NULL（509 台入 5 项目 + 6 台入 LEGACY），D2 删「公共池」表述；②**参数名统一**（F2）：URL / API / 日志 / 审计全链路 `project_key`，数字 id 只留 DB 外键（可追溯性论证只有 key 成立）；③**D2 facet 理由重写**（F3）：原理由 1、2（「中兴/ODM 均同时存在 MTK 与展锐」「产品线下多项目」）被 §5 证伪，替换为已核实论据——MLD/ELA 同客户、同平台、同形态但 APK 不同必须分两项目，树在第一层分不开；④**variables 值类型**（F4）：挂起注记补真实 APK 数组值与「按原类型代入」语义；⑤**applicable 示例**（F5）改真实组合 `{"platform":["MTK"],"customer":["荣耀"]}`；⑥**key 双列**（F6）：`storage_key` 挂起期间不建，复议 D7 时由 `project_key` 派生（字符集 `[a-z0-9-]`），`project_key` 一经对外使用即不可变 |

**挂起语义**：被挂起决策的原文**保留不删**（记录论证历史，防兜圈子——未触发复议条件时不得重新提出已挂起机制）。复议触发条件：

- 族数 > ~10，或用例 APK 适配随 build 版本漂移（脚本内路由表维护成本失控）→ 复议 D1/D4
- 多客户权限/容量隔离升级为硬需求 → 复议 D5
- 产物跨项目串扰成为实际问题 → 复议 D7
- 项目数 > ~20，或需要跨页保持项目上下文的真实场景 → 复议 D8/D9

## 背景

平台需同时承载多客户、多平台、多形态的稳定性测试项目（中兴 / 传音 / 荣耀 / ODM，
MTK / 展锐 / 高通，手机 / 平板）。三类需求（详见背景分析 §1）落到当前模型上暴露两个层面的问题：

**领域模型缺少项目维度**：`Plan` / `Device` / `PlanRun` / `Script` 均无归属字段，
23 个 route 模块的 185 处查询无归属过滤，派发校验的拒因阶梯不含归属校验
（`plan_dispatcher_sync.py:58`）。全部 Plan 平铺在同一界面，无分组、无筛选
（`PlanListPage.tsx:28`）。

**参数不可按项目分化**：`PlanStep` 无 params 列（`models/plan.py:81`），dispatcher 的参数
唯一来源是 `deepcopy(script.default_params)`（`plan_dispatcher_core.py:187,253`），
代码注释明确 WiFi 注入是其**唯一**例外（`:349`），触发接口也不接受参数覆盖
（`routes/plans.py:165`）。叠加「版本即参数」（已存在版本的 `default_params` 不可变），
「同一 MTBF 工具、逐项目不同用例 APK」只能靠**逐项目新建脚本版本**实现——
按现有族数与专项数估算需新增 15–40 个版本，而当前活跃版本共 38 个，
且 ADR-0020 规定版本目录内容不可变，每个版本是磁盘上一份完整重复的脚本文件。

**参数分层是先决条件**：项目模型不解决参数分化，参数分化不依赖项目模型。二者的先后顺序
由此确定（D1 可独立先行）。

> **2026-08-18 决策转向后此论断失效**（见修订记录 v2）：APK 差异改由脚本端设备指纹路由吸收，
> 参数分层不再是 R3 的解。背景中的版本膨胀估算（15–40 个新版本）仍是「参数不可分化」的
> 真实代价基线，但化解路径已改为脚本路由。

生产现状为引入时机提供了窗口：Device 515 台、Host 34 台已达生产规模，
而 Plan 仅 4 个且均为验证用途——迁移成本近乎为零。同时真实使用形态尚未验证，
机制类设计保持最小（见「非目标」）。

## 决策

### D1：引入步骤级参数覆盖 `plan_step.params_override`

> **状态：挂起（2026-08-18）**。APK 差异已由脚本内设备指纹路由吸收（修订记录 v2）；
> 步骤级参数覆盖的通用机制仍可能有用（非 APK 参数的计划级分化），但当前无真实使用场景，
> 不做。触发复议：路由表维护成本失控或出现计划级参数分化需求。

新增 `plan_step.params_override`（JSONB，默认 `{}`），dispatcher 按
`script.default_params` → `params_override` 深合并生成步骤 params。

- **`script.default_params` 的不可变约定不变**：它继续承载「该脚本版本的脚本级默认」，
  已存在版本仍 422 拒改。覆盖层解决的是**同一版本在不同项目下的取值差异**，两者不冲突。
- 合并结果必须通过该脚本版本的 `param_schema` 校验，校验点在 **Plan 保存**与 **precheck**
  两处，不允许非法参数进入 Agent 执行。
- WiFi 注入（`plan_dispatcher_core.py:349`）保持现状，作用于合并之后。

**D1 不依赖 D2–D7，可独立设计、独立合入。** 单独落地即解除脚本版本膨胀。

### D2：项目实体 `test_project` — 单层身份 + 正交 facet

新增 `test_project` 表。**身份单层，无父子关系**；产品线 / 客户 / 平台 / 形态
作为**正交标签（facet）**建模为项目自身的列，不建为层级实体。

```
test_project
  id            PK
  project_key   unique, 不可变        HONOR-MLD / TRANSSION-X110 / ODM-DAM
  display_name                        荣耀 MLD 系列
  jira_project_key                    提交 jira 时自动带出的项目关键字（唯一硬需求，v2 新增）
  storage_key   unique, 不可变        产物路径用（见 D7，v2 挂起后暂不建列）

  -- facet：正交、可空、可枚举、可组合筛选
  product_line                        荣耀产品线 / ODM产品线（可空后补）
  customer                            中兴 / 传音 / 荣耀 / ODM
  platform                            MTK / UNISOC / QCOM
  form_factor                         PHONE / TABLET

  status        ACTIVE / ARCHIVED
  variables     JSONB                 项目级参数（见 D4，v2 挂起后暂不建列）
```

新增归属列：`plan.project_id`、`device.project_id`（可空，NULL = **迁移期瞬态**，
迁移完成后归零——不存在「公共池」域，未识别设备归 `LEGACY` 项目，见 M-c 完成标准）、
`plan_run.project_id`。设备归属变更走审计日志（ADR-0015），不建独立归属历史表。

**`test_project` 的所有变更均走 `record_audit`**（`backend/core/audit.py:35`，与 `routes/users.py` 同形）：
create / archive / facet 修改 / **`variables` 变更** / 设备归属转移。
`variables` 尤其不可省——它直接决定下次派发的实际执行内容（用例 APK 路径），
不审计则「谁在什么时候把 APK 换了」无从追溯，而这正是跨项目误配最可能的来源。

**facet 而非层级树的两条理由**（v2 修订：原理由 1、2 基于「中兴/ODM 均同时存在
MTK 与展锐、产品线下多项目」的假设，被 §5 定稿证伪——生产无跨平台族、中兴仅
Z258 一个项目。替换为已核实事实）：

1. **客户 / 平台 / 形态都不能作为树的顶层节点**（§5 已确认）：MLD 与 ELA 同属荣耀、
   同 MTK、同手机形态，但用例 APK 不同（MLD 的 APK 从 ELA 基础上适配）→ **必须分两个
   项目**。树要求每层节点互斥归属，第一层无论按客户、平台还是形态都分不开这两个项目；
   facet 天然可以——两行、facet 全同、`project_key` 不同。而平台相关行为（#220：
   生产只扫 MTK，展锐/高通走 stub）是全局按平台生效的，不从属于任何产品线。
2. **视图层级需可变**。facet 正交时，前端可任意切换分组顺序（产品线×平台、平台×形态、
   客户×专项）而数据模型不动；树一旦定序，换视图即改表。

**`test_project.platform` 只用于筛选、分组与展示，不用于行为判定。**
平台行为的权威来源是 `device.platform`（AEE 采集分派即读设备侧，`aee/collector.py:42`）。
若某族同时存在跨平台变体，项目的 `platform` 置空，按设备侧判定。

### D3：项目粒度判据

> **项目 = 「共用同一套用例 APK + 同一批物理设备」的最小单位。**

按此判据，生产设备的族即项目粒度：`MLD_LX2`(228) 与 `MLD_LX3`(32) 的 MTBF 用例 APK
已确认为同一个 → 合为一个项目（**MLD**），族内变体由既有的 `device.model` 区分。
生产已确认 **5 个真实项目 + 1 个 Legacy**（`LEGACY` 承载 6 台未识别设备与存量 Plan），
逐条确认表见背景分析 §5（2026-08-18 已填）。

推论：

- **软件版本不进项目**。同一机型的版本持续滚动，版本入项目会使项目表随迭代无限膨胀、
  每出一版就要重建 Plan。版本是**运行期属性**：`plan_run.build_version`，
  可由 `device.build_display_id`（心跳上报）取值。
- **族内变体的定向执行是设备筛选问题，不是模型问题**。只跑 `MLD_LX3` 通过选机时按
  `device.model` 过滤实现，`PlanRunTrigger.device_ids` 的既有形态不变。
- `device_family` 不作为 facet 列——项目本身即族，该列与项目一一对应，冗余。

### D4：三层参数解析

> **状态：挂起（2026-08-18）**。`${project.x}` 的主要动机（用例 APK 按项目注入）已被
> 脚本内设备指纹路由替代；`variables` 列不建。触发复议：路由表维护成本失控或出现
> 项目级非 APK 参数需求。**F4 注**：示例中 `MLD_cases_v3.apk` 为起草占位符，§5 已核实
> 真实值为**两个 APK**（`ReliabilityUiautomatorTest.apk` + `ReliabilityUiautomatorTestTest.apk`）——
> 复议时 `${project.x}` 须按原类型代入（值支持标量与数组，不做字符串拼接）。

```
script.default_params        脚本级默认，版本内不可变        {"tool_dir": "/mnt/automation-toolkit/android-tools/stability_MTBF-Test"}
test_project.variables       项目级取值                      {"mtbf_case_apk": "/mnt/.../MLD_cases_v3.apk"}
plan_step.params_override    步骤级覆盖，可含 ${project.x}    {"case_apk": "${project.mtbf_case_apk}"}
        │
        ▼  派发期解析（一跳）
plan_snapshot                字面值，冻结                    {"tool_dir": "...", "case_apk": "/mnt/.../MLD_cases_v3.apk"}
```

- **解析时机 = 派发期**。项目 APK 随版本滚动更新，改一处即对下次运行生效。
- **快照存解析后的字面值**，`plan_snapshot` 的不可变语义与可追溯性不受影响；
  修改项目变量不改变任何已存在 PlanRun 的 snapshot。
- **解析失败在 Plan 保存与 precheck 两处拦截**，不允许延后到 Agent 执行期暴露。
- **仅一跳**：`${project.x}` 直接取 `test_project.variables`，不支持嵌套引用或多级回退。

### D5：派发门禁与 PlanRun 快照

> **状态：挂起（2026-08-18）**。项目不再承载「设备池隔离」语义——定向执行靠既有选机
> 筛选（型号 / 软件版本 / 设备标签），无需同域校验。`plan_run` 快照仅保留 `project_id`
> 与 `build_version`（登记/报表维度），脚本 sha 冻结（原 D5 第三段）仍建议补入快照，
> 与项目模型无关，可独立做。触发复议：多客户权限/容量隔离升级为硬需求。

派发链路（手动 / 定时 / 链式派发，以及 QUEUED → PRECHECK 再校验）统一校验：

1. 目标设备与 Plan 的归属**同域**。归属域取 `project_id` 值本身，`NULL` 是一个独立的域（Legacy / 未分配），不是通配：
   - `NULL` Plan ↔ `NULL` 设备：放行（迁移期与公共池的既有行为）
   - 项目 X Plan ↔ 项目 X 设备：放行
   - **混合一律拒绝**——`NULL` Plan ↔ 项目 X 设备、项目 X Plan ↔ `NULL` 设备、项目 X ↔ 项目 Y 均拒。
     `NULL` 不得被解释为「任意项目可用」，否则未分配设备会成为绕过归属门禁的旁路。
2. 同一次派发的设备集合必须同域（不允许一半 `NULL` 一半项目 X）
3. `plan.next_plan_id` 指向的 Plan 属于同一项目
4. 定时任务引用的 Plan 与设备属于同一项目
5. 步骤引用的脚本通过 D6 的适用性检查

应用层校验之外，DB 层加复合外键使跨项目 `plan_run` **建不出来**。

**`ResourcePool` 全局共享，不参与归属门禁。** WiFi 资源池按 `host_group` 而非项目划分
（`backend/models/resource_pool.py:26`），本 ADR 不引入池归属——不得据此发明
「池必须属于当前项目或标记为共享」的第 6 条校验。`host_group` 是主机分组维度，与项目正交，
两者不可互相推导。资源池归属若确有需要，另开决策。

**在途归属转移语义**（设备在 PlanRun 生命周期中被转移到其他项目）：

| PlanRun 状态 | 行为 |
|--------------|------|
| RUNNING / 已派发 | **不受影响**，不中断、不改快照。`plan_run.project_id` 在 prepare 时已冻结（D5 快照字段） |
| QUEUED → PRECHECK | 准入时按**当前** `device.project_id` 重校验；不同域则**拒绝准入**，不静默放行 |
| 已 PRECHECK 未派发 | 以快照为准，不回查活表 |

**归属转移动作本身不被在途 Run 阻塞。** 设备存在 ACTIVE lease 或在途 Job 时仍允许转移，
转移即时生效于**新派发**。反之（转移前必须等 Run 结束）会让长跑 Plan 永久锁死归属变更——
稳定性测试的 Run 时长以天计，这个代价不可接受。

`plan_run` 增加不可变快照字段：`project_id`、`project_storage_key`、`build_version`，
并将脚本 `content_sha256` 补入 `plan_snapshot`——当前 sha 由 precheck 回查活表
（`precheck/scripts.py:24`），逃生阀 `force_rebaseline` 改写活表 sha 时，在途 PlanRun 的
期望值会随之漂移。

### D6：可观测性维度 `Plan.specialty`

> **状态：保留 `specialty`，挂起 `applicable`（2026-08-18）**。「一计划一专项」已是现状，
> `specialty` 列（低成本、Plan 列表分组高频使用）保留；`applicable` 属性匹配（新机型
> 滞后维护 + 脚本路由已承担设备差异）挂起，不建。

`plan` 增加 `specialty` 列（MTBF / 开关机 / MONKEY / …，配套字典表供下拉与聚合）。
`Script.category`（脚本级分类）保持不变，两者是不同层级的维度。

- Plan 列表按 **项目 × 专项** 二维分组与筛选；
- 项目详情页回答「本项目的 Plan / 在跑 Run / 归属设备 / 最近结果」；
- 前端信息架构见 D8。

**R2（同一专项、不同项目用不同工具脚本）不引入新机制**：不同实现即不同 `script:<name>`，
各自建 Plan，由 `specialty` 提供跨项目聚合视图。防呆通过**属性匹配**实现——
Script 增加 `applicable` 元数据（如 `{"platform": ["MTK"], "customer": ["荣耀"]}`——§5
已核实的真实组合，MLD/ELA 均为 MTK+荣耀），
Plan 编辑器按项目 facet 自动过滤候选脚本。
不建 `project_script_binding` 绑定表：新增脚本需逐项目绑定的维护成本，在快速迭代下不可持续。

**`applicable` 是参考门禁，不是阻断门禁**（与 `code-rabbit-gate` 的 best-effort 语义一致）：

| 环节 | 行为 |
|------|------|
| Plan 编辑器 | 默认只列出匹配项目 facet 的脚本；提供「显示全部」显式越过 |
| Plan 保存 | **允许**。越过项写入 `plan_step`，不拒绝 |
| precheck | **仅告警不阻断**：记 WARNING 级 `script_applicable_mismatch`，PlanRun 照常准入 |
| PlanRun 详情 | 展示告警条目，标明越过的步骤与不匹配的 facet |

理由：`applicable` 是人工维护的元数据，滞后于新机型/新脚本是常态；
把它做成硬阻断，第一次「元数据没跟上」就会卡住真实业务，随后必然被整体关闭。
真正的硬阻断留给 D5 的归属门禁与既有的脚本 sha 校验——那两者的事实来源是 DB 与磁盘，不会滞后。

### D7：存储命名空间

> **状态：挂起（2026-08-18）**。产物按 run 组织（`devices/{plan_run_id}` 等）当前够用，
> 无跨项目串扰问题。触发复议：产物跨项目串扰成为实际问题。

新产物统一写入项目命名空间，路径由统一入口构造（`backend/agent/aee/paths.py` /
`backend/core/storage_root.py`，#172 既有约定），禁止各模块自行拼接：

```
{root}/projects/{storage_key}/devices/{plan_run_id}/
{root}/projects/{storage_key}/dedup/{plan_run_id}/
{root}/projects/{storage_key}/jira/{plan_run_id}/
{root}/projects/{storage_key}/jobs/{job_id}/
```

`storage_key` 一经创建即不可变——它已写入历史产物路径，改名会使历史产物失联。
**v2 补充（F6）**：`project_key` 同时是 URL 路径段与 UI 展示标识，一经对外使用即不可变
（改展示名走 `display_name`，不换 key）；`storage_key` 挂起期间不建列，复议 D7 时由
`project_key` 派生一次（字符集限 `[a-z0-9-]`，进文件路径与跨 Windows 扫描工具）。
**不可变由三层保证，DB unique 只解决唯一性、不解决可变性**：

1. **DB**：`UNIQUE` 约束（防重复，非防改）
2. **结构性**：`storage_key` 不出现在任何 update schema 中，配合 `ConfigDict(extra="forbid")`
   （仓库既有写法，见 `PlanRunTrigger`）——字段无法从 API 进入更新路径，不依赖实现者记得。
   迁移脚本同样不含更新该列的路径
3. **回归测试**：构造反例（尝试更新 `storage_key`）断言被拒，防止后续重构把字段加回 update schema

DB trigger 是更强的第四层，但仓库 63 个迁移**零 trigger 先例**，引入属新机制——
若评审认为需要，作为独立决策处理，不在本 ADR 默认采纳。

旧路径保持**只读兼容**，不迁移历史数据。

落地前置：中心存储 merge 由仓库外工具 `start_log_scan.py` 执行，需先验证其对路径深度与命名无隐含假设。

### D8：前端信息架构 — 项目是全局上下文，不是页面筛选器

> **状态：取消（2026-08-18）**。5 个项目的规模下，全局选择器 + 8 页跟随体系是过度设计；
> 改为「**项目登记簿页 + 页面级标签/筛选**」的最小形态（见背景分析 §3.2 的替换方案）：
> 一级导航「项目」（列表 = 卡片 + facet 筛选，详情 = 设备/计划/结果/jira），其余页面
> 加项目标签与下拉筛选，不做跨页上下文。触发复议：项目数 > ~20 或需要跨页保持
> 项目上下文的真实场景。下文的全局选择器设计作为该场景的实现蓝图保留。

**项目选择器常驻顶栏（`AppShell` header），全局唯一。** 不在各页面各放一个项目下拉——
项目是「当前在看哪个项目」的上下文，切换应同时改变所有页面，而非逐页重设。

**上下文状态的权威源是 URL query `?project=<project_key>`，localStorage 仅作默认值。**

- URL 优先于 localStorage：链接可分享、可收藏（「把 MLD 的执行记录发同事」需自带项目上下文）
- 无 `?project=` 时读 localStorage 上次选择并**重写进 URL**，保证任意时刻 URL 自洽
- `全部项目` 是合法值（`?project=all`），**对所有角色开放**——数据视野不按角色收窄，
  跨项目跟踪 run 是 user 与 admin 的共同需要；按角色收窄的是**管理动作**（如设备归属分配）

**`全部项目` 下禁止派发。** 执行 Plan 页在 `project=all` 时禁用派发按钮并提示先选项目——
否则 D5 的归属门禁只能在提交后报错，把可预防的配置错误推迟到失败反馈。

**页面按「业务视图 / 基础设施视图」二分，不做自动回落。**

| 页面 | 路由 | 项目上下文 |
|------|------|-----------|
| 仪表盘 | `/` | 跟随（项目摘要） |
| Plan 管理 | `/orchestration/plans` | 跟随 + **项目 × 专项二维分组** |
| 执行 Plan | `/execution/plan-execute` | 跟随 + **强制**（选机范围限定为项目归属设备） |
| 执行记录 / 详情 / 日志 | `/execution/plan-runs*` | 跟随 |
| 测试结果 | `/results` | 跟随 |
| 问题追踪 | `/issue-tracker` | 跟随 |
| 定时调度 | `/schedules` | 跟随 |
| 物理设备 | `/devices` | **双模式**：默认按项目过滤；admin 可切「全部设备」做归属分配 |
| 脚本库 | `/script-management` | **不跟随**（全局资产），按 `applicable` 显示适用性标记 |
| 主机集群 / 文件服务器 | `/hosts`、`/storage` | **不跟随**（基础设施） |
| WiFi 资源池 | `/wifi` | 不跟随（资源池归属不在本 ADR 范围） |
| 用户 / 操作日志 / 通知 / 系统设置 | — | 不跟随（系统管理） |

业务视图不因「项目下无数据」回落到全局池——空态就是空态，回落会让隔离在视觉上失效。

**新增一级导航分组「项目」，置于「概览」与「测试编排」之间**（现有六个分组见 `layouts/Sidebar.tsx`）。
它是所有编排动作的上下文入口，位置需先于被它约束的页面。

| 新增路由 | 内容 |
|----------|------|
| `/projects` | 项目列表：facet 列（产品线 / 客户 / 平台 / 形态）、设备数、在跑 Run 数、状态；按任意 facet 分组与筛选 |
| `/projects/:projectKey` | 项目详情，五个 tab：**概览**（facet + 设备/Plan/Run 统计）、**Plan**（按专项分组）、**设备**（归属列表 + 分配/移出）、**变量**（`variables` 编辑，含用例 APK 路径）、**结果**（最近 Run 与风险等级趋势） |

`/projects/:projectKey` 用 `project_key` 而非数字 id 作路径段——它不可变（D2），链接可长期有效。

**跨端同步复用既有机制。** `plan_changed`（#268 B2，`useCrossClientSync.ts` 挂载于 AppShell）
新增同类事件 `project_changed`，携带 `{project_id, action}`；前端失效
`['projects']` / `['project']` / `['devices']` 三个查询键。

**`device.project_id` 变更必须发此事件**——归属分配是多人协作动作，
不广播会导致 A 浏览器把设备移出项目后，B 浏览器的项目页仍显示该设备并允许选中派发，
直到 D5 门禁在提交时拒绝。设备列表缓存也必须一并失效，仅失效 `['projects']` 不够。

### D9：API 侧 `ProjectScope` 缺省语义

> **状态：挂起（2026-08-18）**。随 D8 取消，API 层不做 `ProjectScope` 强制；新路由
> 带 `project_key` 可选过滤参数即可（key 进 URL / 日志 / 审计可读，数字 id 只留
> DB 外键——可追溯性论证只有 key 成立）。触发复议：与 D8 相同。

D8 定义的是**前端**上下文；API 层缺 `project_id` 时的行为需独立定义，
否则新增路由会静默退化为全量查询。

**入参三态，全部显式：**

| 入参 | 行为 |
|------|------|
| `project_id=<id>` | 限定该项目 |
| `project_id=all` | 跨项目查询（**显式**）。**不按角色门禁**——数据视野对所有角色开放，收窄的是管理动作 |
| 缺省 | 见下方分阶段语义 |

**缺省行为分阶段翻转**（避免一次性打断 185 处查询与既有调用方）：

| 阶段 | 缺省语义 |
|------|----------|
| P2–P4 | 等价 `all`，**同时记 WARNING 级 `project_scope_missing`**（携带路由名与调用者） |
| P5 完成后 | 翻转为 **400**，强制显式传参 |

告警期的日志直接产出「尚未接入项目上下文的调用点清单」，替代人工扫描 23 个 route 模块。
翻转的前置条件是该告警在一个完整运行周期内归零。

**项目上下文必须留在请求里，不得转为服务端状态。** 两条理由，一正一反：

**正面（可追溯性）**：`?project=<key>` 是请求的一部分，进 access log 与审计记录——
「操作员以为自己看的是 A 项目，实际在 B 项目上派发」这类错误可被追溯与复现。
上下文一旦沉为服务端会话状态，审计只能记录「哪个账号从哪个 IP 做了什么」，
记录不了「他当时以为自己在哪个项目」，该类错误既不可追溯也不可重现。
这与 ADR-0015 / #268 B1 的审计方向一致（`record_audit` 已携带 IP 与操作者）。

**反面（并发上下文互覆盖）**：服务端会话项目按账号存储，同一账号的任意两个并发上下文
会互相覆盖。触发形态不限于多 tab——**同账号多端登录已在生产实测发生**（一台终端挂 patrol 页、
另一台并发触发新 run），功能上等价于双 tab。此外**所有角色都可能跨项目**：user 同样需要
同时跟踪多个项目的 run（权限只限制管理动作，不限制数据视野），admin 更天然跨项目
（建项目、分设备、跨项目对比，D8 已定义 `project=all` 视图）——任何残留的「当前项目」
语义在角色切换项目后都会错位；豁免任一角色都等于承认方案不完整。

因此项目上下文的唯一权威是请求参数（前端由 URL 提供，见 D8）。

**无 HTTP 上下文的链路不适用本规则。** SAQ 任务（scan / upload / merge / extract）、
APScheduler sweep、reconciler 等从 `plan_run` 快照读 `project_id` / `project_storage_key`，
不查活表、不依赖请求参数——这也是 D5 要求把项目信息冻结进 PlanRun 快照的原因之一。

## 非目标

R1 的性质是**组织与可观测性**（需求表述为「较难管理、可观测性不好」），不是安全隔离。
以下不纳入本 ADR：

| 不做 | 理由 |
|------|------|
| PostgreSQL 行级安全（RLS）强制层 | 组织需求不需要数据库级强制；`SET LOCAL` + 连接池的泄漏风险与调试成本不对等 |
| `/api/v1/projects/{key}/…` 全路径重构 | 23 route × 185 查询 × 294 测试的代价；路径前缀是约定而非强制，且管不到无 HTTP 上下文的 SAQ / APScheduler 链路。改用扁平路径 + `?project_id=` + 统一 `ProjectScope` 依赖 |
| 按项目独立挂载 / 文件 ACL / 容器执行用户 / 项目专用 Host | 同上；若 R1 升级为安全需求需重新评估——同一 Agent 进程、同一 Linux 用户、同一中心存储挂载构不成安全边界 |
| Agent 按 Job 同步脚本与工具包 | 组织需求下同 Host 跨项目目录互读无害；改动牵连 self-heal 推送、`script_catalog_version` 协商、precheck sha 校验，风险最高 |
| 扫描工具按项目区分（`STP_DEDUP_SCAN_*` 仍为部署级） | 待出现第二套报表工具后再议 |
| `execution_profile_version` / `tool_bundle_release` / `allowed_script_release` / `test_suite` / `report_profile` 实体族 | 见「备选方案与权衡」§3 |
| 配置继承机制（facet → 项目的自动归纳） | 见「备选方案与权衡」§8 |
| `project_member` 与项目级角色（viewer/operator/maintainer） | 现有 `users.role`（admin/user）够用；等出现真实权限诉求再加 |

## 备选方案与权衡

**1. 层级树：产品线 → 平台 → 形态 → 项目。**
放弃。平台与产品线正交而非从属，树会重复节点并使跨产品线的平台查询退化为遍历；
各客户的实际粒度不一致（产品线级 vs 单项目级），树强制每层有节点；
视图分组顺序被焊死在数据模型里。详见 D2 三条理由。

**2. 不引入参数层，逐项目新建脚本版本。**
放弃。15–40 个新版本对 38 个版本的基线接近翻倍，且 ADR-0020 版本目录不可变意味着每个版本
是一份完整重复的脚本文件，单点 bug 需修改 N 份。

**3. 版本化 ExecutionProfile 实体族（5 张表：工具包发布 / 允许脚本 / 套件 / 策略 / 报表 Profile）。**
放弃。仓库已有三层 pin 覆盖其核心诉求——ADR-0020 版本目录不可变（含 CI 门禁）、
`content_sha256`、`plan_snapshot` 冻结；工具包路径已可由步骤参数指定且优先级最高
（`aimonkey_paths.py:29`，`cfg["aimonkey_dir"]` > env > 内置默认）。
`execution_profile_version` 与「一个 Plan = 一个完整专项」语义高度重叠，等于给 Plan 再套一层壳。
在业务侧仅 4 个 Plan、真实形态未验证时建 5 张表属过度建模。

**4. 项目粒度到机型（每个 `device.model` 一个项目）。**
放弃。MLD_LX2 与 MLD_LX3 已确认共用 MTBF APK，拆开会产生大量差异极小的项目，
正是本 ADR 要避免的形态。族级粒度下防误派发的能力不受损——族内变体本就是同一测试目标。

**5. 用 `Device.tags` 表达项目归属。**
放弃。`tags` 全库仅 2 台设备有非空值，无强制约束的标签字段实际不会被持续维护；
且标签无法在 DB 层约束派发一致性。

**6. PostgreSQL RLS 作为强制过滤层。**
放弃（当前）。它确实优于路径前缀——漏写过滤时返回空集而非全量，且覆盖无 HTTP 上下文的后台链路。
但在组织需求下收益不足以抵消代价：表 owner 需 `FORCE ROW LEVEL SECURITY`、
必须 `SET LOCAL` 否则连接池跨请求泄漏、Alembic 需 BYPASSRLS 角色、
policy 静默失效需专门反例用例证伪。若 R1 升级为安全需求，这是首选复议项。

**7. 一个项目一个软件版本。**
放弃。项目表将随版本迭代无限膨胀，每出一版需重建 Plan 与设备归属。版本归入 `plan_run.build_version`。

**8. 项目变量的多级继承链（facet → profile → 项目 → 步骤，运行期逐级解析）。**
放弃（当前）。facet **字段**是数据，加了不用零成本，第一天就加；继承**机制**一旦进入 dispatcher，
所有参数问题的排查都要沿链走且极难移除。真出现「多个项目配置高度重合、改一处要改多遍」时再抽，
且优先做成**创建项目时按 facet 匹配模板、把值拷贝进 `variables`**（编辑期继承，项目内始终是字面值），
而非派发期多跳解析。D4 的 `${project.x}` 是**单跳**，与此不同。

**9. 项目上下文用每页独立筛选器，不设全局选择器。**
放弃。逐页重设上下文在 6 个项目（5 真实 + Legacy）× 8 个跟随页面下即失效；且各页筛选状态互不相通时，
「从 Plan 列表点进执行记录」会丢失项目，用户看到的是全量数据——隔离在体感上不成立。

**10. `applicable` 做成硬阻断（不匹配即拒绝保存/派发）。**
放弃。`applicable` 是人工维护的元数据，新机型/新脚本上线时滞后是常态；
硬阻断会在第一次元数据滞后时卡住真实业务，随后被整体关闭，最终等于没有。
定为参考门禁（保存放行 / precheck 仅告警），与仓库既有的 `code-rabbit-gate` best-effort 语义一致。详见 D6。

**11. 服务端会话项目（缺省时取当前会话选中的项目，存 `users` 表或服务端会话）。**
放弃。它成立需要两个前提同时满足，而两者**当前均不成立**：

| 前提 | 现状 | 结构上 |
|------|------|--------|
| 一人一次只看一个项目 | **不成立**——user 与 admin 都可能同时操作多个项目（权限范围 ≠ 操作范围；实测 tester 触发 run 后仍查看其他项目数据） | **不成立**——admin 天然跨项目（建项目/分设备/跨项目对比，D8 已定义 `project=all`）；账号分工增加后跨项目成为常态 |
| 不开多 tab | **已被违反**——同账号多端登录已在生产实测发生，功能上等价双 tab | **不成立**——多 tab 是浏览器默认行为，产品无机制约束 |

方案的失败不依赖多 tab：任意两个并发的会话上下文（多端、多 tab、第二个登录窗口）都会互相覆盖，
多 tab 只是最易想象的触发形态。豁免任一角色（如给 admin 开例外）等于承认方案不完整。
叠加可追溯性损失（见 D9 正面理由），本项无重议价值——除非产品引入
「单会话强制互斥登录」，届时前提①仍不成立。

## 影响

| 面 | 影响（v2 最小形态） |
|----|------|
| Schema | 新增 `test_project`（含 `jira_project_key`，不含 `variables` / `storage_key`）+ 专项字典表；`plan` 加 `project_id` / `specialty`；`device` 加 `project_id`；`plan_run` 加 `project_id` / `build_version`。全部 additive |
| 数据迁移 | 建 Legacy 默认项目，回填 4 个存量 Plan、93 个 PlanRun、515 台设备。设备归属需按背景分析 §5 的清单人工确认（M-c 分批 + dry-run 不变） |
| 派发路径 | **无改动**——不增加归属门禁（D5 挂起）。`plan_run` 快照建议补脚本 `content_sha256` 冻结（原 D5 第三段，独立于项目模型，可与 P2 并行） |
| Agent | **无变更**。APK 差异由脚本侧设备路由承担（`backend=auto` 模式规范化 + step_trace 记录路由决策 + 未匹配 fail-fast），属脚本目录约定，不涉平台协议 |
| 前端 | `types.ts` 同步；新增「项目」一级导航 + `/projects` `/projects/:projectKey` 两条路由（列表 = facet 卡片筛选，详情 = 设备 / 计划 / 结果 / jira 四块）；设备页加「批量归入项目」；Plan / PlanRun / 结果页加项目标签与下拉筛选。**无全局选择器、无跨页跟随**（D8 挂起） |
| 实时 | `device.project_id` 归属变更广播（弱化版 `project_changed`，仅设备页与项目页缓存失效） |
| 存储 | **无**（D7 挂起） |
| 兼容性 | `project_id` 全部可空起步，未分配项目的既有行为不变 |

### 迁移与回滚

**分段执行，每段独立可合入、独立可回滚：**

| 段 | 内容 | 数据风险 |
|----|------|----------|
| M-a | 建表 + 建列（全部 nullable / 有 server_default），无回填、无读路径 | 无 |
| M-b | 建 Legacy 默认项目，回填 `plan.project_id` / `plan_run.project_id`（4 + 93 行） | 低，可重跑 |
| M-c | `device.project_id` 按背景分析 §5 清单**逐批**回填（515 行，分族）；**完成标准：回填后无 NULL**（509 台入 5 个真实项目 + 6 台未识别设备入 LEGACY） | 需人工确认，**不自动推断** |
| M-d | 打开读路径与门禁（feature flag） | 行为变更点 |

**约束：**

- **幂等可重跑**：回填以「目标列为 NULL」为条件，重跑不覆盖已确认的归属；
  Legacy 项目按 `project_key` 幂等 upsert
- **dry-run 必备**：M-c 提供 `--dry-run` 输出「将把哪些设备划入哪个项目」的清单，
  确认后再执行。515 台设备的归属错划需要逐台人工纠正，成本远高于一次预演
- **不自动推断归属**：不得按 `device.model` 前缀或 `platform` 自动分配项目。
  族与项目的映射由 §5 清单人工确认——`MLD_LX2`/`MLD_LX3` 共用 APK 是**业务事实**，
  不是能从机型字符串推导的规律

**回滚分两个窗口，不能一概「删新列」：**

| 窗口 | 条件 | 回滚方式 |
|------|------|----------|
| A | M-a/M-b/M-c 完成，**业务尚未写入**新列 | 删新列，无损 |
| B | M-d 之后，`params_override` 已有业务值、PlanRun 快照已含解析后字面值 | **关 feature flag 停读路径，保留列与数据**。删列会丢失步骤参数覆盖与快照溯源 |

窗口 B 下删列不可逆——`plan_step.params_override` 是 D1 的唯一参数分化载体，
删除等于把已配置项目的 APK 路径全部丢失。

## 落地与后续动作

阶段划分、依赖关系与验收标准见背景分析 [§6](../reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md#6-落地顺序)
与 [§7](../reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md#7-验收标准)。摘要：

| 阶段 | 内容（v2 最小形态） | 对应决策 |
|------|------|----------|
| P1 | `test_project`（含 `jira_project_key`）+ 专项字典表 + 归属列 + Legacy 回填（M-a/M-b/M-c） | D2 / D3 / D6 |
| P2 | 前端：项目登记簿页（列表卡片 + facet 筛选；详情 = 设备 / 计划 / 结果 / jira）+ 设备批量归入 + Plan / PlanRun / 结果页项目标签与筛选 | D8 最小形态 / D6 |
| P3 | jira 提交自动带 `jira_project_key`（提交入口 + 映射展示） | v2 |
| —（并行，不属本 ADR 表结构） | 脚本路由约定：入口脚本按设备能力路由 + step_trace 记录路由决策 + 未匹配 fail-fast（`backend=auto` 模式规范化） | v2 |
| —（独立前置） | SocketIO `on_subscribe` 按 run 归属校验（既有鉴权洞，见下，与项目模型无关） | — |

**独立前置项 P0（不属本 ADR 的决策范围，但必须先于 P5）**：`socketio_server.py:345` 的
`on_subscribe` 对任意 room 字符串直接 `enter_room`，无归属校验
（本仓 `test_dashboard_auth.py:8` 明确记录该 P0 只覆盖 `on_connect`）。
前端做了项目上下文过滤而实时事件仍全量推送时，项目登记簿的可观测性收益会被打穿——
用户在 A 项目页面上会收到 B 项目 run 的 `step_log`（且该洞不依赖项目模型：任意
room 字符串均可订阅，v2 后依旧成立）。

该项**须单独写一条 Agent Note**（`docs/notes/bug-fix/`，Class: bug-fix）记录
「订阅侧与连接侧鉴权分离」的成因与修复边界，防止后续复议时重新论证一遍。

**待定项（已收窄，2026-08-18）**：背景分析 §5 的项目清单已填写（5 个真实项目 + 1 个
Legacy）；无跨平台族（五族均单平台：MLD/ELA/DAM/Infinix 为 MTK、Z258 为 UNISOC），
`platform` facet 全可填。剩余实施期补充：各项目用例 APK 路径——v2 后是**脚本路由表的
输入**（入口脚本内映射，不在控制面），专项接入时提供；`product_line` facet 值建表后
可为 NULL 后补。清单填写不阻塞 D2 的设计（v2 后 D1 已挂起）。

**本 ADR 只定前端信息架构（D8），不定视觉与组件细节。** 具体布局、组件选型与交互稿
在 P5 实施时进入 `docs/design/`（参照 `2026-07-plan-execute-page-improvements.md` 的既有形态）。

**重议触发条件**：R1 由组织需求升级为安全需求（不同客户数据不得互见）时，
复议「非目标」全部条目，优先复议备选方案 §6（RLS）。

## 关联实现/文档

- 背景分析：[`reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md`](../reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md)
- [ADR-0020](./ADR-0020-plan-step-one-shot-migration.md)：Plan-Step 模型与 lifecycle 唯一事实源
- [ADR-0023](./ADR-0023-script-traceability.md)：脚本溯源与 sha256 契约（D5 补 sha 入快照的依据）
- [ADR-0021](./ADR-0021-script-content-alignment-gate.md)：派发门禁（D5 在其上扩展）
- [ADR-0019](./ADR-0019-android-device-lease-and-capacity-scheduling.md)：设备租约——运行期排他，与项目归属（行政边界）正交
- [ADR-0015](./ADR-0015-audit-log-system.md)：设备归属变更的审计载体
- [ADR-0025](./ADR-0025-phase4-architecture-alignment.md)：方案 C 存储布局（D7 在其上加项目段）
- [ADR-0026](./ADR-0026-plan-execution-scaling.md)：准入队列——D5 的 QUEUED → PRECHECK 再校验挂载点
