# 项目分类域与参数分层审查

- **状态**：Living v1.0
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

---

## 2. 现状证据（生产实测）

### 2.1 设备侧已达生产规模，业务侧尚未铺开

| 对象 | 数量 |
|------|------|
| Device | **454** |
| Host | 34 |
| Plan | **4** |
| PlanStep | 23 |
| PlanRun | 91 |
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
| Z2581 | UNISOC | 125 | **Z258** |
| Z2582 | UNISOC | 21 + 1(platform 空) | **Z258** |
| DAM_M500 | MTK | 18 | DAM |
| ELA_LX2 | MTK | 15 + 1(platform 空) | **ELA** |
| ELA_LX3 | MTK | 4 | **ELA** |
| Infinix_X1102D | MTK | 3 | Infinix_X110 |
| （model 空） | UNKNOWN | 6 | 未识别 |

族内变体（`MLD_LX2`/`LX3`、`ELA_LX2`/`LX3`、`Z2581`/`2582`）即 R1 中「A57 / A57Pro」形态。

**平台分布**：MTK 300 / UNISOC 146 / UNKNOWN 6 / 空 2。**QCOM 生产 0 台**——高通维度目前为预判。

**已确认**：MLD_LX2 与 MLD_LX3 的 MTBF 用例 APK 为**同一个**。

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
| G1 | **步骤级参数覆盖** | **不存在**。`PlanStep` 无 params 列 | `backend/models/plan.py:81` |
| G2 | **参数唯一来源 = script.default_params** | dispatcher 直接 `deepcopy(default_params)`；WiFi 注入是代码注释明写的**唯一**例外 | `plan_dispatcher_core.py:187,253`、例外声明见 `:349` |
| G3 | **触发期参数覆盖** | 不存在。`PlanRunTrigger` 仅接受 `device_ids` / `note` / `wifi_pool_id` | `backend/api/routes/plans.py:165` |
| G4 | **项目归属** | 全库 `backend/models/` 无 project/tenant 任何字段 | `plan.py` / `host.py` / `script.py` / `plan_run.py` |
| G5 | **派发归属校验** | 拒因阶梯仅 not_found / no_host / device_offline / device_error / host_offline / active_lease / active_job | `plan_dispatcher_sync.py:58` |
| G6 | **列表查询归属过滤** | 23 个 route 模块、185 处 `select(` 无归属过滤 | `backend/api/routes/` |
| G7 | **专项（specialty）维度** | 不存在。`Script.category` 是脚本级分类，非 Plan 级 | `backend/models/script.py:15` |
| G8 | **Plan 列表可观测性** | 仅名称/描述文本搜索，无分组、无筛选 | `frontend/src/pages/orchestration/PlanListPage.tsx:28,50` |
| G9 | **存储路径项目段** | `devices/{plan_run_id}`、`dedup/{run_id}`、`jira/{run_id}`，无项目层 | `backend/agent/aee/paths.py:331`、`dedup_scan.py` |
| G10 | **扫描工具按项目区分** | 部署级进程 env，全局单值 | `dedup_scan.py:34` |
| G11 | **Agent 脚本同步范围** | 全量拉取所有活跃脚本 | `backend/agent/registry/script_registry.py:73` |
| G12 | **快照冻结脚本 sha** | `build_plan_snapshot` 冻结 step/params/nfs_path，**不含 sha**；sha 由 precheck 回查活表 | `plan_dispatcher_core.py:407`、`precheck/scripts.py:24` |
| G13 | **SocketIO 订阅授权** | `on_subscribe` 对任意 room 字符串直接 `enter_room`，无归属校验 | `backend/realtime/socketio_server.py:345` |
| G14 | **成员/角色** | `users.role` 仅 `admin` / `user` 两值 | `backend/models/user.py:16`、`routes/users.py:92` |

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

### 3.2 前端页面现状与改动面

路由表见 `frontend/src/router/index.tsx`，侧栏六个分组见 `frontend/src/layouts/Sidebar.tsx:49-89`。
按 ADR-0029 D8 的「业务视图 / 基础设施视图」二分标注改动面：

| 侧栏分组 | 页面 | 路由 | 上下文 | 改动 |
|----------|------|------|--------|------|
| （新增）**项目** | 项目列表 | `/projects` | — | **新建**：facet 列 + 设备/Plan/Run 统计 + 按 facet 分组筛选 |
| | 项目详情 | `/projects/:projectKey` | — | **新建**：概览 / Plan / 设备 / 变量 / 结果 五 tab |
| 概览 | 仪表盘 | `/` | 跟随 | 统计口径加项目过滤 |
| 测试编排 | Plan 管理 | `/orchestration/plans` | 跟随 | **改造最大**：现仅名称搜索（`PlanListPage.tsx:28,50`）→ 项目 × 专项二维分组 + Plan 复制 |
| | 执行 Plan | `/execution/plan-execute` | 跟随 + **强制** | 选机范围限定为项目归属设备；`project=all` 时禁用派发 |
| | 执行记录 | `/execution/plan-runs*` | 跟随 | 列表过滤；详情页展示项目与 `build_version` |
| 测试资产 | 脚本库 | `/script-management` | **不跟随** | 增 `applicable` 适用性标记展示 |
| | WiFi 资源池 | `/wifi` | 不跟随 | 无改动 |
| 主机与设备 | 主机集群 | `/hosts` | **不跟随** | 无改动 |
| | 物理设备 | `/devices` | **双模式** | 默认按项目过滤；admin 切「全部设备」做归属分配 |
| | 文件服务器 | `/storage` | **不跟随** | 无改动 |
| 分析报告 | 测试结果 | `/results` | 跟随 | 列表与聚合加项目过滤 |
| | 问题追踪 | `/issue-tracker` | 跟随 | 同上 |
| 运营配置 | 定时调度 | `/schedules` | 跟随 | 列表过滤；创建时校验 Plan 与设备同项目 |
| | 通知管理 | `/notifications` | 不跟随 | 无改动 |
| （用户菜单） | 用户 / 操作日志 / 系统设置 | — | 不跟随 | 操作日志可选加项目维度筛选 |

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
| **R3** 不同项目不同用例 APK | **结构性阻塞** | **G1 + G2 + G3** | **否** |

### 4.1 R3 的代价量化

在 G1/G2/G3 未解除时，让不同项目使用不同 APK 的唯一路径是**逐项目新建脚本版本**
（「版本即参数」：已存在版本的 `default_params` 422 不可变）。

按 §2.2 的族数与 R2/R3 涉及的专项数估算：**5–10 个项目 × 3–4 个专项脚本 ≈ 15–40 个新版本**，
相对当前 38 个版本的基线接近翻倍；且每个版本是磁盘上一份完整重复的脚本文件，
单点 bug 需修改 N 份。

**结论**：R3 的优先级高于 R1。参数分层是先决条件。

### 4.2 R1 的性质判定

需求表述为「较难进行管理，可观测性不好」，而非「不同客户数据不得互见」。
按**组织需求**处理，以下措施不纳入范围：

- 数据库行级安全（RLS）强制层
- 按项目独立挂载 / 文件 ACL / 容器执行用户
- Agent 按 Job 同步脚本与工具包

若后续升级为**安全需求**，需重新评估：同一 Agent 进程、同一 Linux 用户、同一中心存储挂载
构不成安全边界，`STP_SSH_LOG_ROOTS` 的 SSH 读日志通路与共享 HDD 目录均为横向通道。

---

## 5. 项目清单（待填）

按 ADR-0029 D3 的粒度判据（共用同一套用例 APK + 同一批物理设备的最小单位）逐条确认：

| 族 | 机型 | 台数 | 平台 | 客户 | 产品线 | 形态 | APK 共用 | 项目 key |
|----|------|------|------|------|--------|------|----------|----------|
| MLD | MLD_LX2 / MLD_LX3 | 260 | MTK | 待填 | 待填 | 待填 | ✅ 已确认 | 待填 |
| Z258 | Z2581 / Z2582 | 147 | UNISOC | 待填 | 待填 | 待填 | 待确认 | 待填 |
| ELA | ELA_LX2 / ELA_LX3 | 20 | MTK | 待填 | 待填 | 待填 | 待确认 | 待填 |
| DAM | DAM_M500 | 18 | MTK | 待填 | 待填 | 待填 | n/a（单机型） | 待填 |
| Infinix_X110 | Infinix_X1102D | 3 | MTK | 传音 | 待填 | 待填 | n/a（单机型） | 待填 |
| — | （model 空） | 6 | UNKNOWN | — | — | — | — | 未分配 |

填完可确定：项目总行数（预估 5–6）、各项目 `variables` 的 APK 路径、
以及是否存在跨平台族（若某族同时含 MTK 与 UNISOC 变体，`platform` facet 需置空，见 ADR-0029 D2）。

---

## 6. 落地顺序

| 阶段 | 内容 | 解除 | 依赖 |
|------|------|------|------|
| P0 | SocketIO room 订阅按 run 归属校验 | G13 | 无（可立即独立合入）；**必须先于 P5** |
| P1 | `plan_step.params_override` + 深合并 + `param_schema` 校验 | **G1/G2/G3** | 无（**可独立先行**） |
| P2 | `test_project` 表 + facet 列 + `plan.project_id/specialty` + `device.project_id`；4 个存量 Plan 回填 Legacy 项目 | G4/G7 | P1 |
| P3 | 派发门禁（含 NULL↔NULL 互斥语义）+ 复合外键 + `plan_run` 快照字段（`project_id` / `build_version` / 脚本 sha） | G5/G12 | P2 |
| P4 | `test_project.variables` + `${project.x}` 派发期解析 | R3 完整形态 | P1+P2 |
| P5 | 前端（见 §3.2）：顶栏项目选择器 + 「项目」导航与两条新路由 + 项目 × 专项矩阵 + `project_changed` 跨端同步 + Plan 复制 | G6/G8 | P2、**P0** |
| P6 | Script `applicable` 属性匹配 + Plan 编辑器按项目 facet 过滤（**参考门禁**：保存放行 / precheck 仅告警） | R2 防呆 | P2 |
| P7 | 存储命名空间 `projects/{storage_key}/…`（新产物写新路径，旧路径只读） | G9 | P2 |

P1–P3 均为 additive 迁移，可独立合入、可回滚。

### 6.1 阶段间冲突与前置校验

- **P2/P3 与在飞工作**：`feat/multiworker-b1-b4`（B3 Plan 并发防护）与 ADR-0026 准入队列均改
  dispatcher / plan_run 路径，需排期避让或先合入。
- **P7 外部依赖**：中心存储 merge 由**仓库外**工具 `start_log_scan.py` 执行
  （`-merge_files_list` 清单文件路径，见 AGENTS.md §scan/upload/merge 契约）。加 `projects/` 层级前
  必须先验证该工具对路径深度与命名无隐含假设。
- **G10/G11 不在本次范围**：扫描工具按项目区分、Agent 按需同步脚本，均待 R1 升级为安全需求或
  出现第二套报表工具后再议。

---

## 7. 验收标准

| # | 标准 | 对应阶段 |
|---|------|----------|
| A1 | 同一脚本版本可在不同项目下使用不同用例 APK，**不新建脚本版本** | P1 |
| A2 | 新增一个项目所需的脚本版本增量为 **0** | P1+P4 |
| A3 | 参数占位符解析失败在 **Plan 保存**与 **precheck** 两处均拦截，不进入 Agent 执行 | P1+P4 |
| A4 | `plan_snapshot` 内为解析后的**字面值**，改项目变量不改变已有 PlanRun 的 snapshot | P4 |
| A5 | Plan.project_id 与目标设备 project_id 不一致时，在创建 Job 前拒绝 | P3 |
| A6 | 跨项目 PlanRun 在 DB 层建不出来（复合外键） | P3 |
| A7 | **NULL↔NULL 放行、项目↔项目放行、任何混合组合被拒**；同一次派发的设备集合必须同域 | P3 |
| A8 | Plan 列表可按 项目 × 专项 二维分组与筛选 | P5 |
| A9 | 任一项目详情页可回答：本项目的 Plan / 在跑 Run / 归属设备 / 变量 / 最近结果 | P5 |
| A10 | 切换项目后**所有跟随页**同步改变；带 `?project=` 的链接在新会话中直接落到该项目 | P5 |
| A11 | `project=all` 时执行 Plan 页派发入口禁用，不依赖提交后报错 | P5 |
| A12 | 基础设施页（`/hosts`、`/storage`、`/script-management`）不随项目切换而过滤 | P5 |
| A13 | 业务页在项目无数据时显示空态，**不回落到全局池** | P5 |
| A14 | A 浏览器变更 `device.project_id` 后，B 浏览器的项目页与设备列表在无刷新的情况下同步 | P5 |
| A15 | 用户订阅非自身可见 run 的 SocketIO room 被拒 | P0 |
| A16 | 脚本 `applicable` 不匹配时：编辑器默认不列出、显式越过后**保存成功**、precheck 记 WARNING 且 PlanRun **照常准入** | P6 |
| A17 | 新产物路径包含不可变项目存储键；旧路径仍可读 | P7 |
| A18 | `storage_key` 无法经任何 API 路径被更新（反例测试断言被拒） | P2 |
| A19 | 列表 API 缺 `project_id` 时记 `project_scope_missing` 告警；P5 后返回 400 | P2 → P5 |
| A20 | 设备在 RUNNING 期间被转移项目：在途 Run **不中断**；同一设备的 QUEUED Run 在准入时被拒 | P3 |
| A21 | 资源池不参与归属校验——跨项目使用同一 WiFi 池不被拒绝 | P3 |
| A22 | `test_project` 的 create / archive / `variables` 变更均产生审计记录 | P2 |
| A23 | M-c 设备归属回填提供 `--dry-run`，且重跑不覆盖已确认归属 | P2 |

---

## 8. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-18 | 初版：R1–R3 需求、生产数据基准、G1–G14 缺口核对、P0–P7 落地顺序 |
| 2026-08-18 | 补 §3.2 前端页面现状与改动面；A7/A10–A14/A16 验收补齐（归属互斥、上下文同步、跨端广播、`applicable` 参考门禁语义） |
| 2026-08-18 | 补 A18–A23（`storage_key` 不可变、`ProjectScope` 缺省、在途转移、资源池豁免、项目审计、回填 dry-run），对应 ADR-0029 D9 与「迁移与回滚」小节 |
