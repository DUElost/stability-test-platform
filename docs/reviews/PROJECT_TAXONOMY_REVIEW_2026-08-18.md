# 项目分类域与参数分层审查

- **状态**：Living v2.0（2026-08-18 决策转向，见 §4.1 与 §9）
- **日期**：2026-08-18
- **决策**：[`ADR-0029`](../adr/ADR-0029-project-taxonomy-and-param-layering.md)
- **数据基准**：生产库只读快照（2026-08-18）

本文是 ADR-0029 的背景分析：记录需求、现状证据、缺口核对与落地顺序。
**决策本身不在本文**，见 ADR-0029。

---

## 1. 需求

### R1 — 多项目并存的管理与可观测性

同时在测的项目形态举例：

| 客户 | 项目 | 平台 | 形态 |
|------|------|------|------|
| 中兴 | A57 / A77 / A57Pro | MTK | 手机 |
| 传音 | X6851 / X1102 / X1103 / X1103B | MTK / 展锐 / 高通 | 手机 + 平板 |
| 荣耀 | V551A / V552AA / T615 | — | 手机 + 平板 |
| 华测（ODM） | 农机平板 | — | 平板 |

> 表中机型为**需求举例**（商用名/客户口述），与生产族名（MLD/ELA/Z258/…）的对应关系
> 以 §5 清单为准（2026-08-18 已确认：MLD、ELA 属荣耀；Z258 属中兴；Infinix_X1102D
> 属传音平板；DAM 属 ODM）。

各项目版本名不同、迭代节奏不同。全部 Plan 平铺在同一个测试计划界面时，管理与可观测性不足。

### R2 — 同一专项、不同项目使用不同工具与脚本

以**开关机专项**为例，同一专项在不同客户下的实现完全不同：

| 客户 | 实现 |
|------|------|
| 传音 | 客户自研 alarm 工具 |
| 中兴 | 自研 py 脚本，adb 定时触发重启 |
| 荣耀 | 其他部门研发的 apk 工具 |

### R3 — 同一工具、不同项目使用不同用例与 APK

**MTBF 专项**：工具路径统一为 `/mnt/automation-toolkit/android-tools/stability_MTBF-Test`，
但其中的**用例**与**适配后打包的自动化用例 APK 逐项目不同**——大部分是一个项目对应一个 APK。

### R4 — jira 问题提交需要项目关键字映射

结果收取阶段（一个计划对应一个专项）已按 run 组织，但向 jira 提交问题时需要把
「哪个项目」映射为 jira 项目关键字（如 MLD → `STABILITY-A`）。该映射是**外部系统信息，
adb 设备指纹读不到**，必须人工维护于平台（2026-08-18 决策者确认：唯一硬需求）。

---

## 2. 现状证据（生产实测）

### 2.1 设备侧已达生产规模，业务侧尚未铺开

| 对象 | 数量 |
|------|------|
| Device | **515** |
| Host | 34 |
| Plan | **4** |
| PlanStep | 23 |
| PlanRun | 93 |
| Script（活跃） | **17 个名 / 38 个版本** |

现存 4 个 Plan 均为验证/冒烟用途（`smoke-plan-001`、`Monkey专项-watcher-patrol`、
`验证-短时patrol-自然SUCCESS`、`dle-e2e-216-aee-trigger`）。

**含义**：引入项目模型的迁移成本目前近乎为零（4 个 Plan 回填）；同时真实使用形态尚未验证，
机制类设计应保持最小。

### 2.2 设备机型已呈「族 + 变体」结构

| 机型 | 平台 | 台数 | 族 |
|------|------|------|-----|
| MLD_LX2 | MTK | 228 | **MLD** |
| MLD_LX3 | MTK | 32 | **MLD** |
| Z2581 | UNISOC | 186 | **Z258** |
| Z2582 | UNISOC | 22 | **Z258** |
| DAM_M500 | MTK | 18 | DAM |
| ELA_LX2 | MTK | 16 | **ELA** |
| ELA_LX3 | MTK | 4 | **ELA** |
| Infinix_X1102D | MTK | 3 | Infinix_X110 |
| （model 空） | UNKNOWN | 6 | 未识别 |

族内变体（`MLD_LX2`/`LX3`、`ELA_LX2`/`LX3`、`Z2581`/`Z2582`）即 R1 中「A57 / A57Pro」式
**同项目多机型**形态；R1 的机型名为需求举例（商用名/客户口述），与生产族名的客户对应
以 §5 清单为准。

**平台分布**：MTK 301 / UNISOC 208 / UNKNOWN 6。**QCOM 生产 0 台**——高通维度目前为预判。

**已确认（决策者 2026-08-18）**：MLD_LX2 与 MLD_LX3、Z2581 与 Z2582、ELA_LX2 与 ELA_LX3
各自族内的 MTBF 用例 APK 均为**同一个**。**族间不共用**：MLD 与 ELA 虽同属荣耀客户，
但用例 APK 不同（MLD 的 APK 源码从 ELA 基础上适配而来），按 D3 判据分为两个项目。

### 2.3 分类不能依赖自觉维护的标签

`Device.tags`（`backend/models/host.py:60`，JSONB）全库仅 **2 台**设备有非空值（`["Monkey 测试"]`）。
无强制约束的标签字段在本团队实际不会被持续维护。

### 2.4 脚本版本膨胀基线

活跃脚本 17 个名 / 38 个版本，平均每名 2.2 个版本。ADR-0020 规定版本目录内容不可变
（CI 门禁 `tools/dev/check-script-version-immutability.py`），每个版本在磁盘上是一份**完整独立**的脚本文件。

---

## 3. 现状缺口核对

| # | 能力 | 现状 | 代码位置 |
|---|------|------|----------|
| G1 | **步骤级参数覆盖** | **不存在**。`PlanStep` 无 params 列——**v2 已由脚本端设备路由解除**（R3 不再走参数层） | `backend/models/plan.py:81` |
| G2 | **参数唯一来源 = script.default_params** | dispatcher 直接 `deepcopy(default_params)`；WiFi 注入是代码注释明写的**唯一**例外——同上，**不再构成阻塞** | `plan_dispatcher_core.py:187,253`、例外声明见 `:349` |
| G3 | **触发期参数覆盖** | 不存在。`PlanRunTrigger` 仅接受 `device_ids` / `note` / `wifi_pool_id`——同上 | `backend/api/routes/plans.py:165` |
| G4 | **项目归属** | 全库 `backend/models/` 无 project/tenant 任何字段 | `plan.py` / `host.py` / `script.py` / `plan_run.py` |
| G5 | **派发归属校验** | 拒因阶梯仅 not_found / no_host / device_offline / device_error / host_offline / active_lease / active_job | `plan_dispatcher_sync.py:58` |
| G6 | **列表查询归属过滤** | 23 个 route 模块、185 处 `select(` 无归属过滤 | `backend/api/routes/` |
| G7 | **专项（specialty）维度** | 不存在。`Script.category` 是脚本级分类，非 Plan 级 | `backend/models/script.py:15` |
| G8 | **Plan 列表可观测性** | 仅名称/描述文本搜索，无分组、无筛选 | `frontend/src/pages/orchestration/PlanListPage.tsx:28,50` |
| G9 | **存储路径项目段** | `devices/{plan_run_id}`、`dedup/{run_id}`、`jira/{run_id}`，无项目层 | `backend/agent/aee/paths.py:331`、`dedup_scan.py` |
| G10 | **扫描工具按项目区分** | 部署级进程 env，全局单值 | `dedup_scan.py:34` |
| G11 | **Agent 脚本同步范围** | 全量拉取所有活跃脚本 | `backend/agent/registry/script_registry.py:73` |
| G12 | **快照冻结脚本 sha** | `build_plan_snapshot` 冻结 step/params/nfs_path，**不含 sha**；sha 由 precheck 回查活表 | `plan_dispatcher_core.py:407`、`precheck/scripts.py:24` |
| G13 | **SocketIO 订阅授权** | `on_subscribe` 对任意 room 字符串直接 `enter_room`，无归属校验（独立鉴权洞，与项目模型无关，可单独修） | `backend/realtime/socketio_server.py:345` |
| G14 | **成员/角色** | `users.role` 仅 `admin` / `user` 两值 | `backend/models/user.py:16`、`routes/users.py:92` |
| G15 | **jira 项目关键字映射** | **不存在**（v2 新增）。提交 jira 时项目关键字靠人工记忆/查表 | 全库无 jira 相关字段 |

### 3.1 已具备、无需新建的能力

| 能力 | 现状 | 位置 |
|------|------|------|
| 工具包路径按步骤参数指定 | `cfg["aimonkey_dir"]`（step param）优先级**高于** env 与内置默认 | `backend/agent/aimonkey_paths.py:29` |
| 脚本版本内容不可变 | ADR-0020 版本目录 + `content_sha256` + CI 门禁 | `models/script.py:19` |
| Plan 快照冻结 | `plan_snapshot` 冻结 plan/steps/params/nfs_path | `plan_dispatcher_core.py:407` |
| 设备变体识别 | `Device.model` 已填充且区分度足够（见 §2.2） | `models/host.py:58` |
| 设备软件版本 | `Device.build_display_id` 由心跳上报 | `models/host.py:79` |
| 平台行为门禁 | AEE 采集按 `Device.platform` 分派，生产只扫 MTK | `backend/agent/aee/collector.py:42`（#220） |
| 变更审计 | 审计日志系统（ADR-0015） | `backend/models/audit.py` |

---

### 3.2 前端页面现状与改动面（v2 最小形态）

路由表见 `frontend/src/router/index.tsx`，侧栏六个分组见 `frontend/src/layouts/Sidebar.tsx:49-89`。
v2（2026-08-18 决策转向）后 D8 的全局选择器 / 跨页跟随取消，改为**项目登记簿页 +
页面级标签/筛选**：

| 侧栏分组 | 页面 | 路由 | 改动 |
|----------|------|------|------|
| （新增）**项目** | 项目列表 | `/projects` | **新建**：facet 卡片（客户/产品线/平台/形态）+ 设备数 + jira 关键字 + 状态；按任意 facet 筛选（筛选即「关系视图」：同客户项目同现） |
| | 项目详情 | `/projects/:projectKey` | **新建**：设备（归属列表 + 批量归入）/ 专项计划 / 最近结果 / jira 四块，**无变量 tab** |
| 测试编排 | Plan 管理 | `/orchestration/plans` | 项目标签 + 下拉筛选（可选按专项分组，D6 `specialty` 保留）；Plan 复制可后补 |
| | 执行记录 | `/execution/plan-runs*` | 列表项目标签 + 筛选；详情页展示项目 |
| 主机与设备 | 物理设备 | `/devices` | 项目列 + 勾选「批量归入项目」（归属分配入口） |
| 分析报告 | 测试结果 | `/results` | 项目标签 + 筛选 |
| | 问题追踪 | `/issue-tracker` | 提交入口带出项目 → `jira_project_key`；展示映射 |
| 其余页面 | — | — | **无改动**（不引入跟随/上下文体系） |

全局构件：

| 构件 | 位置 | 说明 |
|------|------|------|
| 项目选择器 | `layouts/AppShell.tsx` 顶栏 | 全局唯一；URL `?project=<key>` 为权威，localStorage 作默认值 |
| 跨端同步 | `hooks/useCrossClientSync.ts` | 现监听 `PLAN_CHANGED` 失效 `['plans']`/`['plan']`；增 `PROJECT_CHANGED` 失效 `['projects']`/`['project']`/`['devices']` |
| 事件常量 | `utils/socketEvents.ts` | 增 `projectChanged: 'project_changed'` / `PROJECT_CHANGED` |
| 类型 | `utils/api/types.ts` | 权威源，须与后端 Pydantic schema 同步 |

---

## 4. 需求 → 缺口映射

| 需求 | 性质 | 阻塞缺口 | 今天能否绕过 |
|------|------|----------|--------------|
| **R1** 多项目管理与可观测性 | **组织 + 可观测性**（非安全隔离） | G4 / G6 / G7 / G8 / G13 | 否——但不需要数据库级强制 |
| **R2** 不同项目不同工具脚本 | **组织维度缺失** | G7（可发现性）、无防呆 | **可以**——不同实现即不同 `script:<name>`，各自建 Plan |
| **R3** 不同项目不同用例 APK | **结构性阻塞（v1 判定）** | ~~G1 + G2 + G3~~ | **可以（v2，2026-08-18）**——脚本端设备指纹/能力路由吸收（见 §4.1） |
| **R4** jira 提交的项目关键字映射 | **外部系统信息** | G15（映射无处存放） | **否**——adb 指纹读不到，必须人工登记于平台 |

### 4.1 R3 的化解：脚本端设备路由（2026-08-18 决策转向）

v1 判定 R3 为「结构性阻塞」（G1/G2/G3 未解除时逐项目分化只能新建脚本版本）。v2 转向：
**APK 差异由脚本端路由吸收，不需要控制面参数分层**。

**既有先例**：`/mnt/automation-toolkit/android-tools/stability_PowerCycle-Test/test-config.properties`
的 `backend=auto`——单入口脚本按设备能力自行选择执行器（Z2582 无 REBOOT 时自动用 MSSV，
否则 AutoTestTool）。MTBF 专项工具按同一模式组织（一个专项目录一套入口脚本组）。

**版本膨胀基线仍然成立**（§2.4：monkey_setup 13 版本、38 个活跃版本），但那是
「迭代被迫复制」；逐族 APK 差异不再走版本复制路径——新族接入是**改路由表 + 新建版本**
（比复制整份脚本好一个数量级），不是逐项目整份脚本。

**路由方案的配套约定**（脚本目录契约，不涉平台协议）：

1. **未匹配 fail-fast**：入口脚本对不认识指纹/能力的设备立即报明确错误（如
   `no executor for model X in <专项> v<ver>`），不进假跑
2. **路由决策可观测**：step_trace 记录实际使用的执行器 / APK（同一快照不同设备跑
   不同内容，事后可追溯）
3. **登记簿是唯一人工维护点**：客户/关系/形态/jira 映射只登记一处（§5 清单即种子
   数据），脚本路由表与 jira 下拉都是它的投影，避免各处各写一份映射

**结论（v2）**：R3 的优先级与参数分层解均不再成立；项目模型的职责收窄为登记簿
（R1 组织 + R4 jira 映射）。

### 4.2 R1 的性质判定

需求表述为「较难进行管理，可观测性不好」，而非「不同客户数据不得互见」。
按**组织需求**处理，以下措施不纳入范围：

- 数据库行级安全（RLS）强制层
- 按项目独立挂载 / 文件 ACL / 容器执行用户
- Agent 按 Job 同步脚本与工具包

若后续升级为**安全需求**，需重新评估：同一 Agent 进程、同一 Linux 用户、同一中心存储挂载
构不成安全边界，`STP_SSH_LOG_ROOTS` 的 SSH 读日志通路与共享 HDD 目录均为横向通道。

---

## 5. 项目清单（已确认 2026-08-18）

按 ADR-0029 D3 的粒度判据（共用同一套用例 APK + 同一批物理设备的最小单位）逐条确认：

| 族 | 机型 | 台数 | 平台 | 客户 | 产品线 | 形态 | APK 共用 | 项目 key |
|----|------|------|------|------|--------|------|----------|----------|
| MLD | MLD_LX2 / MLD_LX3 | 260 | MTK | 荣耀 | —（待补） | 手机 | ✅ 同一个 | `HONOR-MLD` |
| ELA | ELA_LX2 / ELA_LX3 | 20 | MTK | 荣耀 | —（待补） | 手机 | ✅ 同一个 | `HONOR-ELA` |
| Z258 | Z2581 / Z2582 | 208 | UNISOC | 中兴 | —（待补） | 手机 | ✅ 同一个 | `ZTE-Z258` |
| DAM | DAM_M500 | 18 | MTK | ODM | —（待补） | 手机 | n/a（单机型） | `ODM-DAM` |
| Infinix_X110 | Infinix_X1102D | 3 | MTK | 传音 | —（待补） | **平板** | n/a（单机型） | `TRANSSION-X110` |
| — | （model 空） | 6 | UNKNOWN | — | — | — | — | `LEGACY`（未分配） |

**结论**：

- **项目总行数 = 5 个真实项目 + 1 个 Legacy**（`LEGACY` 承载 6 台未识别设备与存量 Plan
  回填，见迁移 M-b/M-c）。「预估 5–6」得到确认。
- **无跨平台族**：五族均单平台（MLD/ELA/DAM/Infinix 为 MTK，Z258 为 UNISOC），
  `platform` facet 全部可填，无需置空（D2）。
- **同客户 ≠ 同项目**：MLD 与 ELA 同属荣耀、同 MTK、同手机形态，但用例 APK 不同
  （MLD 的 APK 从 ELA 基础上适配）→ 按 D3 判据分两个项目。客户/平台/形态只做
  facet 分组，不合并项目。
- `product_line` facet（客户或我方内部的产品系列组织，如「荣耀 X 系列」）未提供，
  留空待补（facet 可空，D2，v2 后无 `variables` 联动）；各项目 APK 路径**暂未处理**
  ——v2 后 APK 路径是**脚本路由的输入**（脚本内映射），P4 实施时提供。

**APK 路径（已知实例）**：MLD 的 MTBF 用例 APK 位于
`/mnt/automation-toolkit/android-tools/stability_MTBF-Test/apk/` 下的
`ReliabilityUiautomatorTest.apk` 与 `ReliabilityUiautomatorTestTest.apk`
（两者共同构成 MLD 的 MTBF 用例，是脚本路由表的示例值）。

**客户归属的辅助手段（决策者建议，已核实）**：「读设备 brand 值可获知大部分客户」——
当前**不可用**：`device` 表无 brand 列、`extra` JSON 亦无 brand 值（2026-08-18 只读核查）。
归属仍按本表人工确认（M-c「不自动推断」原则）；如需 brand 辅助，须先由 Agent 心跳采集
`ro.product.brand` 入库，作为 M-c **候选预填 + 人工确认**的增强，属未来可选。

---

## 6. 落地顺序

| 阶段 | 内容（v2 最小形态） | 解除 | 依赖 |
|------|------|------|------|
| P1 | `test_project` 表（含 `jira_project_key`，不含 `variables`/`storage_key`）+ 专项字典表 + `plan.project_id/specialty` + `device.project_id` + `plan_run.project_id/build_version`；4 个存量 Plan / 93 PlanRun / 515 设备回填 Legacy | G4 / G7 / G15 | 无 |
| P2 | 前端（见 §3.2 v2）：项目登记簿页（列表卡片 + facet 筛选；详情 = 设备 / 计划 / 结果 / jira）+ 设备批量归入 + Plan / PlanRun / 结果页项目标签与筛选 | G6 / G8 | P1 |
| P3 | jira 提交自动带 `jira_project_key`（问题追踪页提交入口 + 映射展示） | G15 | P1 |
| —（并行，不属本 ADR 表结构） | 脚本路由约定：入口脚本按设备能力路由 + step_trace 记录路由决策 + 未匹配 fail-fast（`backend=auto` 模式规范化） | R3 | 无 |
| —（独立前置） | SocketIO room 订阅按 run 归属校验（既有鉴权洞，与项目模型无关） | G13 | 无（可立即独立合入） |

P1–P3 均为 additive 迁移，可独立合入、可回滚。

### 6.1 阶段间冲突与前置校验

- **P1 与在飞工作**：`feat/multiworker-b1-b4`（B3 Plan 并发防护）与 ADR-0026 准入队列均改
  dispatcher / plan_run 路径。v2 后本方案**不再改 dispatcher**（无门禁），冲突面大幅缩小；
  仅 `plan_run` 加列需与在飞迁移避让。
- **脚本路由与既有脚本**：`monkey_*` 系是单一入口、不分族，保持现状；路由约定只约束
  新接入的专项入口脚本（MTBF / 开关机 / GPU），按专项逐个人工确认。
- **G10/G11 不在本次范围**：扫描工具按项目区分、Agent 按需同步脚本，均待 R1 升级为安全需求或
  出现第二套报表工具后再议。

---

## 7. 验收标准

| # | 标准（v2 最小形态） | 对应阶段 |
|---|------|----------|
| B1 | 新增一个项目所需的脚本版本增量为 **0**（APK 差异进脚本路由，不新建版本） | 并行（脚本路由） |
| B2 | 入口脚本未匹配设备执行器时 **fail-fast**：明确错误码 + step_trace 记录实际路由到的执行器 / APK | 并行（脚本路由） |
| B3 | 项目登记簿：facet（客户 / 产品线 / 平台 / 形态）+ `jira_project_key` 一次表单可建可改，全字段可空 | P1 |
| B4 | 设备可按项目**批量归入**（一次人工确认）；M-c 提供 `--dry-run`，重跑不覆盖已确认归属 | P1 |
| B5 | 项目列表页可按任意 facet 筛选；同客户项目（MLD / ELA）经筛选同现（关系视图） | P2 |
| B6 | 项目详情页可回答：本项目的归属设备 / 专项计划 / 最近结果 / jira 关键字 | P2 |
| B7 | Plan / PlanRun / 结果页带项目标签并可按项目筛选 | P2 |
| B8 | 问题追踪页提交 jira 时**自动带出** `jira_project_key`，无需人工记忆 | P3 |
| B9 | `device.project_id` 变更后项目页与设备页缓存失效（B 浏览器可见，弱化版 `project_changed`） | P2 |
| B10 | 存量 Plan / PlanRun / 设备回填 Legacy（4 / 93 / 515），未分配（NULL）行为不变；**迁移完成标准 = `device.project_id` 无 NULL**（NULL 仅迁移窗口瞬态，非「公共池」域） | P1 |
| B11 | `test_project` 的 create / archive / facet / jira 变更均产生审计记录 | P1 |

---

## 8. D1–D9 评审结论（2026-08-18 多轮评审定稿）

评审历经：初始方案评审（范围与实体形态）→ R1–R3 需求确认 → 项目粒度试探 →
前端缺口评审 → 机制审查（ResourcePool / storage_key / ProjectScope / 迁移回滚）→
D9 方法论挑战 → 最终修订与一致性扫描。
**D1–D9 全部采纳（或修订后维持），无否决项**；ADR-0029 是否由 Proposed 推进为
Accepted 由决策者另行确认。

> **2026-08-18 决策转向（v2）覆盖了「全部采纳」**：评审定稿后，决策者提出「脚本端按设备
> 指纹自行路由能否弱化项目属性」，经核实（§4.1）确认转向——R3 由脚本路由承担，
> 项目模型回归登记簿。D1–D9 的逐条评审结论**保留为 v1 历史**；v2 处置为：
> **D1 / D4 / D5 / D7 / D8 / D9 挂起、D6 保留 `specialty` 挂起 `applicable`、D2 / D3 保留
> （D2 新增 `jira_project_key`）**。逐条状态与复议触发条件以 ADR-0029 修订记录为准，
> 禁止在未触发复议条件时重新提出已挂起机制（防兜圈子）。

### D1 — 步骤级参数覆盖 `params_override` — **采纳**

- **评审点**：R3 是结构性阻塞还是可绕过？判定为阻塞——「版本即参数」下逐项目分化
  只能新建脚本版本，§4.1 量化 15–40 个新版本 vs 38 个现有基线（近翻倍，
  ADR-0020 不可变目录使每份版本完整重复）。
- **边界确认**：`default_params` 不可变约定不破（脚本级默认仍由它承载）；
  WiFi 注入（`plan_dispatcher_core.py:349`）是合并之后的既有例外；校验点定在
  Plan 保存 + precheck 两处，不延后到 Agent 执行期暴露。
- **落点**：ADR D1 + §4.1 结论（R3 优先级高于 R1）；验收 **A1–A3**。P1 独立先行。

### D2 — 项目实体（单层身份 + 正交 facet）— **采纳**

- **评审点**：层级树试探（产品线 → 平台 → 形态 → 项目）被三条理由驳回：
  ①平台与产品线正交——中兴与 ODM 产品线下都同时存在 MTK 与展锐机型，树会重复节点、
  「查所有 MTK 项目」退化为跨树遍历；②粒度天然不一致——「中兴产品线」下还有多个
  项目而「华测农机平板」本身即一个项目，树强制每层有节点；③视图层级需可变——
  facet 可任意切换分组顺序，树定序即改表。
- **附加要求（机制审查）**：`test_project` 全部变更（create / archive / facet /
  `variables` / 设备归属转移）走 `record_audit`——`variables` 直接决定下次派发的
  用例 APK 路径，「谁在何时换了 APK」必须可追溯。
- **落点**：ADR D2 三条理由 + 审计段；验收 **A22**。

### D3 — 项目粒度判据 — **采纳**

- **评审点**：粒度试探「机型 vs 产品线」用生产证据裁定——MLD_LX2(228) +
  MLD_LX3(32) 已确认共用同一 MTBF 用例 APK（决策者确认），拆开成两个项目即产生
  大量差异极小的项目，正是本 ADR 要避免的形态 → 粒度 = 族。
- **推论确认**：软件版本不进项目（归 `plan_run.build_version`）；族内变体定向执行是
  设备筛选问题（`PlanRunTrigger.device_ids` 形态不变）；`device_family` 不作 facet
  （与项目一一对应，冗余）。
- **落点**：ADR D3 + §2.2「已确认」+ §5 清单（2026-08-18 已填：5 真实项目 + Legacy）。

### D4 — 三层参数解析 — **采纳**

- **评审点**：备选 #8 多级继承链（facet → profile → 项目 → 步骤）评审——facet
  **字段**是数据，第一天就加零成本；继承**机制**一旦进入 dispatcher，所有参数问题
  的排查都沿链走且极难移除。真出现配置高度重合时走「创建时按 facet 匹配模板、
  值拷贝进 `variables`」（编辑期继承），而非派发期多跳。
- **边界确认**：解析时机 = 派发期；快照冻结解析后**字面值**；仅一跳（`${project.x}`
  直接取 `variables`，不支持嵌套引用）。
- **落点**：ADR D4 + 备选 #8；验收 **A4**。

### D5 — 派发门禁与 PlanRun 快照 — **采纳**（两轮修订）

- **第一轮（前端缺口评审）**：Legacy 互斥语义必须显式——NULL↔NULL 放行、
  项目↔项目放行、**混合一律拒绝**；`NULL` 不是通配符（否则未分配设备成为绕过
  归属门禁的旁路）。落 **A7**。
- **第二轮（机制审查）**：① `ResourcePool` 显式声明**全局共享、不参与归属门禁**——
  `host_group` 与项目正交、不可互相推导，不得发明「池必须属于当前项目或标记为共享」
  的第 6 条校验（落 **A21**）；② 在途归属转移语义——RUNNING 不受影响 /
  QUEUED 准入时按**当前**归属重校验 / 已 PRECHECK 以快照为准；转移不被在途 Run
  阻塞（天级长跑 Run 不能永久锁死归属变更，落 **A20**）；③ 快照补脚本
  `content_sha256`（G12——precheck 回查活表 + `force_rebaseline` 会让在途期望值漂移）。
- **落点**：ADR D5 + 在途转移语义表；验收 **A5–A7 / A20 / A21**。

### D6 — 可观测性维度 + `applicable` 参考门禁 — **采纳**

- **评审点**：`applicable` 是人工维护的元数据，滞后于新机型/新脚本是常态——
  硬阻断会在第一次元数据滞后时卡住真实业务，随后必然被整体关闭（备选 #10 放弃）。
  定为**参考门禁**：编辑器默认过滤（可显式越过）/ 保存放行 / precheck 仅 WARNING
  不阻断，与仓库 `code-rabbit-gate` best-effort 语义一致。
- **范围确认**：R2 不引入新机制（不同实现即不同 `script:<name>`，各自建 Plan）；
  不建 `project_script_binding` 绑定表（逐项目绑定在快速迭代下不可持续）。
- **落点**：ADR D6 门禁表 + 备选 #10；验收 **A8 / A16**。

### D7 — 存储命名空间 — **采纳**

- **评审点（机制审查）**：`storage_key` 不可变需要机制保证——**DB unique 只解决
  唯一性、不解决可变性**。定三层：① DB UNIQUE；② 结构性——`storage_key` 不出现在
  任何 update schema 中 + `extra="forbid"`（迁移脚本同），不依赖实现者记得；
  ③ 反例回归测试（构造更新尝试断言被拒，防后续重构把字段加回 update schema）。
- **边界确认**：DB trigger 是更强第四层，但仓库 63 个迁移零 trigger 先例——
  ADR 明示「若评审认为需要，作为独立决策处理」，评审结论：**不纳入本 ADR**。
- **落点**：ADR D7 三层保证；验收 **A17 / A18**；P7 前置校验
  （`start_log_scan.py` 对路径深度与命名无隐含假设）。

### D8 — 前端信息架构 — **采纳**（两轮修订）

- **第一轮（前端缺口评审）**：ADR 原先未覆盖 UI 改动——补 D8 全部内容（顶栏全局
  选择器 / URL `?project=` 为权威 / 业务与基础设施页面二分 / `project=all` 禁派发 /
  新增「项目」导航与两条路由）；复用 #268 B2 机制新增 `project_changed` 事件，
  **`device.project_id` 变更必须广播**（不广播则 B 浏览器仍显示已移出设备并可选中派发，
  直到 D5 在提交时拒绝）；评审建议的 P0（`on_subscribe` 订阅授权前置）一并立项，
  须独立 Agent Note。
- **第二轮（一致性扫描）**：`?project=all` 与「全部项目」视图的措辞从「供管理员使用/
  管理员视图」改为**不按角色门禁**——数据视野对所有角色开放，按角色收窄的只是
  管理动作（如设备归属分配）。
- **落点**：ADR D8 + §3.2 页面改动面；验收 **A9–A14**。

### D9 — API `ProjectScope` 缺省语义 — **维持**（方法论挑战后修订）

- **评审点（方法论挑战）**：服务端会话项目（缺省取当前会话选中的项目）的两个前提
  被逐一证伪——①「一人一次只看一个项目」：权限范围 ≠ 操作范围，user 与 admin 都
  可能同时跨项目（实测 tester 触发 run 后仍查看其他项目数据），豁免任一角色等于
  承认方案不完整；②「不开多 tab」：同账号**多端登录已在生产实测发生**（一台终端挂
  patrol 页、另一台并发触发 run），功能上等价双 tab，且多 tab 是浏览器默认行为。
  叠加可追溯性损失（会话状态记不了「操作员当时以为自己在哪个项目」），方案无重议价值。
- **修订确认**：D9 反面理由重构（所有角色都可能跨项目，不限于 admin）；备选 #11
  证伪表两前提「现状」列改写为不成立/已被违反并附实测证据；结论句「豁免任一角色
  等于承认方案不完整」。
- **落点**：ADR D9 + 备选 #11；验收 **A19**（缺省告警期 P2–P4 → P5 后 400）。

### 评审遗留项

| 项 | 状态 |
|----|------|
| ~~§5 项目清单填写~~ | ✅ 已完成（2026-08-18）：5 真实项目 + Legacy；剩余 `product_line` / 各项目 APK 路径为实施期补充（v2 后 APK 路径是**脚本路由输入**，不阻塞设计） |
| ~~v1 方案定稿~~ | ✅ 已被 **v2 决策转向**覆盖（2026-08-18）：D1/D4/D5/D7/D8/D9 挂起、D6 保留 specialty、D2 新增 `jira_project_key`；v1 评审结论保留为历史（§8） |
| ADR-0029 状态 Proposed → Accepted | 待决策者确认（本记录 + ADR 修订记录为 v2 依据） |
| DB trigger（storage_key 第四层） | 独立决策，不在本 ADR 范围（ADR D7 明示） |
| ResourcePool 池归属 | 独立决策，本 ADR 显式不引入（ADR D5 明示） |

---

## 9. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-18 | 初版：R1–R3 需求、生产数据基准、G1–G14 缺口核对、P0–P7 落地顺序 |
| 2026-08-18 | 补 §3.2 前端页面现状与改动面；A7/A10–A14/A16 验收补齐（归属互斥、上下文同步、跨端广播、`applicable` 参考门禁语义） |
| 2026-08-18 | 补 A18–A23（`storage_key` 不可变、`ProjectScope` 缺省、在途转移、资源池豁免、项目审计、回填 dry-run），对应 ADR-0029 D9 与「迁移与回滚」小节 |
| 2026-08-18 | 增 §8 D1–D9 评审结论（历轮评审定稿：全部采纳、无否决；遗留项 §5 清单填写与 ADR 状态推进） |
| 2026-08-18 | §5 清单填写完成（决策者确认）：5 个真实项目 + Legacy；Z258/ELA 族内 APK 共用确认；Z258 形态=手机；数据基准刷新（Device 454→515、PlanRun 91→93，8-15 网段迁移后新增）；「读 brand 辅助归属」核实为当前不可用；各项目 APK 路径暂未处理 |
| 2026-08-18 | **v2 决策转向（定稿）**：决策者质疑「脚本端按设备指纹路由能否弱化项目属性」→ 确认开关机 `backend=auto` 先例，APK 差异改由脚本路由吸收（§4.1 配套三条：fail-fast / step_trace 记录路由决策 / 登记簿唯一人工维护点）；新增 R4（jira 关键字映射，唯一硬需求）+ G15；§3.2 前端改动面重写为登记簿最小形态；§6 落地顺序收敛为 P1–P3 + 并行脚本路由 + 独立前置；§7 验收 A1–A23 → B1–B11；ADR D1/D4/D5/D7/D8/D9 挂起、D6 保留 specialty、D2 新增 `jira_project_key`（防兜圈子：挂起项的复议触发条件以 ADR 修订记录为准） |
| 2026-08-18 | v2.1（审查确认 F1–F6）：LEGACY 为真实项目行、NULL 降级迁移瞬态（M-c 完成标准 = 无 NULL；删「公共池」）；全链路统一 `project_key`；D2 facet 理由替换为 MLD/ELA 已核实论据；D4 挂起注记补 APK 数组类型；`applicable` 示例改真实组合；`storage_key` 挂起期间不建、复议时由 `project_key` 派生。详见 ADR 修订记录 v2.1 |
