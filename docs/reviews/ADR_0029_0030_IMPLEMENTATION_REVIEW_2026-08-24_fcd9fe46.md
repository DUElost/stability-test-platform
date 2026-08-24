# ADR-0029 / ADR-0030 实现综合评审（独立核验评分 + 再设计）

- **状态**：Living（2026-08-24 初版；结论随实现推进修订）
- **日期**：2026-08-24
- **性质**：**综合评审**（非 ADR、非 Agent Note）——两份 ADR 落地现状评估 + 评分 + 再设计建议
- **上游决策**：[ADR-0029](../adr/ADR-0029-project-taxonomy-and-param-layering.md)（项目分类域，Accepted v2.4）、[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md)（多用例平台化管理，Proposed）
- **背景分析**：[`PROJECT_TAXONOMY_REVIEW_2026-08-18.md`](./PROJECT_TAXONOMY_REVIEW_2026-08-18.md)、[`MTBF_MULTI_CASE_RESEARCH_2026-08-19.md`](./MTBF_MULTI_CASE_RESEARCH_2026-08-19.md)
- **产出会话**：resume 短 ID `fcd9fe46`（session `ses_fcd9fe46effeItZsqTz1NvTWMi`，文件名尾缀）
- **方法**：不采信任何在先评审结论——两份 ADR 正文过目后，对全部承重论断做独立代码核验（核验记录见附录），再独立形成评分与设计
- **关联评审**（同日其他会话产出）：[`..._245a4531.md`](./ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_245a4531.md)（8 / 6.5）、[`..._unattributed.md`](./ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_unattributed.md)（7.5 / 7）、[`..._317ef8ab.md`](./ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_317ef8ab.md)

---

## 0. 结论摘要（TL;DR）

| ADR | 决策质量 | 实现评分 | 一句话 |
|-----|----------|----------|--------|
| **ADR-0029**（项目分类域） | 9 / 10 | **7.5 / 10** | 建表/回填/读路径优秀；但**写路径两处断裂**——新 Plan 与新 Run 恒 NULL，登记簿从第一天起只看得见存量、看不见新数据，且缺口无任何跟踪 |
| **ADR-0030**（多用例管理） | — | **6.5 / 10** | P0 真机验收、P1a 工艺上乘，但 **P1b 门禁整段为零**——管理面引入第二事实源却零拦截，「能管不能保证」，漂移面反而大于纯文件时代 |

**核心判断**：两份 ADR 的失分全在执行序列而非决策文本——0029 把最便宜的收口（快照写入，一行改动）漏成永久缺口；0030 把承担风险的半段（门禁）排在了造能力的半段（管理面）之后。两个 ADR 的收口点其实是同一个函数（见 §4）。

---

## 1. ADR-0029 实现评分：7.5 / 10

### 1.1 分维度评价

| 维度 | 得分 | 证据（本会话独立核验） |
|------|------|------|
| 建表/迁移/回填 | 9.5 | 模型无越界（未建挂起的 `storage_key`/`variables` 列）；回填工具 dry-run + 幂等 + NULL 归零完成标准；生产 547 台归位 |
| 读路径 | 8.5 | `/projects` 列表 + 详情 + 批量归入 + 四页筛选齐全；`plans.py:724-729` 按 `project_key` join `plan.project_id` 过滤（未知 key 404） |
| **写路径** | **2** | 见 1.2 扣分 #1 |
| 治理面 | 4 | 见 1.2 扣分 #3/#4 |

### 1.2 扣分明细

| 扣分 | 项 | 核验证据 |
|------|-----|------|
| **-1.5** | **写路径两处断裂（致命）** | ① `create_plan`（`plans.py:505-519`）构造 `Plan` 不含 `project_id`/`specialty_id` → 新 Plan 恒 NULL；② dispatcher 建 Run（`plan_dispatcher_sync.py:538`）不含 `project_id`/`build_version` → 新 Run 恒 NULL。全后端非测试代码中 `project_id=`/`build_version=` 写入点为零（grep 核验）。后果：所有按快照过滤的读路径（结果页、PlanRun 列表、项目详情「最近运行」）对新数据永久失效。测试 `test_project_routes.py:92` 直接构造带 project 的 run 绕开派发链路，恰好掩盖此洞；缺口未被任何 note/issue 记录 |
| -0.3 | specialty 半死列 | 字典表有种子数据，但 routes 目录 grep `specialty` 零命中——D6 保留它的全部理由（Plan 列表分组高频使用）没有兑现，schema 成本白付 |
| -0.5 | D2 审计空转 | projects 无 update/archive 路由 → 「test_project 所有变更走 record_audit」无对象可审计 |
| -0.2 | `project_changed` 广播延期（已文档化） | 与 D5 门禁挂起叠加后是零拦截链：A 浏览器移出设备、B 浏览器陈旧缓存一路放行到派发成功 |

---

## 2. ADR-0030 实现评分：6.5 / 10

### 2.1 分阶段评价

| 阶段 | 得分 | 证据（本会话独立核验） |
|------|------|------|
| P0 脚本三件套 | 9.5 | 真机验收 #217/#218 在案；`suite_sha256` 留痕 / PROGRESS 打戳 / NFS 逐条 JSON 三链路齐 |
| P1a 实体+管理面 | 8.5 | `models/suite.py` 双漂移检测器（`exported_sha256` 磁盘比对键 + `exported_content_sha256` 库比对键，模型 docstring 明示分工）；渲染三列用 `JSON` 非 JSONB 保键序（注释附 wifiName/wifiPWD 互换实证）；原子写已实现 |
| **P1b 绑定门禁** | **0** | 全仓 grep `inject_suite_params`/`suite_key` 绑定/precheck 套件逻辑零命中；`suites.py:264` 注释自认「ACTIVE PlanRun 引用守卫在 P1b 随绑定字段落地」；`export_to_tool_dir`（`:513-545`）只有原子写，**可在以天计的长跑中途覆盖 runtask.xml 且无任何拦截** |
| P1c 传播/CLI | 2 | [`mtbf-api.md`](../operations/mtbf-api.md) §2 仍是占位（14 端点已上线）；`tools/**/mtbf*` 不存在；**ADR 头部仍标 Proposed**——它 v1.1 自己抄了 0029 v2.3.1 七挂靠位教训后第一个违反 |

### 2.2 结构性问题

P1a（能管）超前于 P1b（能保证）。管理面把 runtask.xml 的真源一分为二（DB + 磁盘文件），却没有门禁保证「派发消费的就是库里那份」——现状只有一个 advisory 的 `X-Export-Stale` 响应头。事后归因原语已存在（P0 init trace 记了 `suite_sha256`），事前拦截为零。**漂移面比纯文件时代（方案 A）更大了。**

---

## 3. 与同日其他三份评审的差异

核心结论一致（写路径断点 / P1b 缺失 / 排期反转 / 状态传播债），差异两点：

1. **对 0030 的「窄化复活 + 显式和解」评价更宽容**：245a4531 认为它「把简单问题复杂化」。我的看法：test_suite 是配置数据实体（与 Script catalog 行同级），显式和解条款是治理开销但不是设计错误——不动它。
2. **补充了一个双方都没展开的 P1b 前置论据**：fleet 单值旋钮的正确性悬崖（见 §4.2）——这使 P1b 从「性价比最高的补课」升级为「第二套套件上线的硬前置」。

---

## 4. 如果我来设计

先说不会翻案的四个核心判断：facet 不建层级树（MLD/ELA 同客户同平台同形态必须分两项目）；登记簿而非隔离（D5/D7/D8/D9 挂起 + 复议触发条件防兜圈）；用例集作配置层不进调度模型（唯一 action 不变量 + 版本不可变 + 整套循环语义三条约束下，130 个 PlanStep 展开明确更差）；P0 先行（真机验收已证明）。以下只讲会改的地方。

### 4.1 把「快照写入」定义为迁移不变量，而不是一次性回填（0029）

M-c 的完成标准「回填后 device.project_id 无 NULL」把回填当成了**一次性事件**——只要派发链路不写快照，NULL 会持续再生，标准当天就被违反。改法一行：prepare 时冻结 `PlanRun.project_id = plan.project_id`；`build_version` 语义 ADR 未定清（一次 Run 覆盖 N 台设备各有 build），建议存 `run_context` per-device map，列只在全体设备同版本时写值、分歧留 NULL。**验收信号改为不变量测试**：迁移窗口之后任何 PlanRun 行 project_id 为 NULL 即 CI 失败——而不是靠一次人工核对宣布完成。

### 4.2 P1b 前置的正确性悬崖论据（0030）

排期反转之外，注意一个被低估的事实：P0 的 `STP_MTBF_EXPECTED_TESTPOINT_COUNT` 是 **fleet 单值**（走 `_FLEET_ENV_KEYS` 全 fleet hot-update 同步），它隐含「全 fleet 只有一套清单」。相机 MTBF（0030 的立项动因）落地第一天，不同项目不同套件不同条数——这个旋钮就**系统性出错**，且错得无声（expected 只是校验基准）。也就是说 P0 权宜方案的正确性崩塌点与业务需求出现点是同一天。P1b 的注入替换不是「可以慢慢排的最后一段」，而是第二套套件上线的硬前置。

### 4.3 绑定放 `plan.suite_id` 可空外键，不放 `default_params.suite_key`

理由比「避免第二个注入特例」更硬：

- dispatcher 注释明示 WiFi 注入是参数逻辑的**唯一例外**，`suite_key` 进 default_params 直接侵蚀这条不变量；
- 「一计划一专项」已是 ADR-0029 D6 确认的现状——套件就是 Plan 的测试内容，按 step 绑定是过度泛化；
- 外键让 precheck 直接 join 校验，并获得 DB 层引用完整性；
- **可空外键天然给出双模式语义**：NULL = P0 文件真源模式（存量兼容，不加门禁），非 NULL = 托管模式（precheck 五步门禁 + run_context 冻结全开）。迁移不需要任何数据搬运；
- 不妨碍未来 D1 复议——从独立列迁往通用覆盖层，比从 JSON 注入特例里迁出来成本低。

### 4.4 导出按 sha 归档——版本化 20% 成本买 80% 收益

「套件版本化等触发条件出现再做」不同意：相机套件频繁分化 + APK↔项目严格对应，**「导出覆盖丢旧版」不是或然事件而是必然事件**。快照 sha 只能归因「被改过」、不能恢复「改之前是什么」。低成本做法：export-to-tool-dir 同时写 `{NFS}/mtbf/{suite}/{exported_sha256}/runtask.xml`——按 sha 命名天然去重，消费路径完全不变，不需要版本实体。

### 4.5 共同收口点

两个 ADR 的缺口是**同一个函数点**：`plan_dispatcher_sync.py:538` 建 PlanRun 时一次写齐 `project_id`（0029）+ `suite_id`/`exported_sha256`/`apk_binding` 冻结进 run_context（0030，托管模式下）。409 在途守卫可不等绑定字段先行：按步骤引用 mtbf 系脚本 + 同 export_dir 匹配 ACTIVE（RUNNING/QUEUED/PRECHECK）Run 即拒。

---

## 5. 落地顺序建议

| 优先级 | 事项 | 对应 |
|--------|------|------|
| **P0** | dispatcher 写快照（`project_id` + build_version per-device map）+ 不变量测试 | 0029 写路径 |
| **P0** | 409 在途守卫弱版（不依赖绑定字段） | 0030 P1b 先行段 |
| **P0** | ADR-0030 翻 Accepted + mtbf-api §2 写实（七挂靠位同步） | 0030 传播 |
| P1 | `plan.suite_id` 外键 + precheck 五步门禁 + 替换 expected_testpoint_count 注入 | 0030 P1b 主体 |
| P1 | create_plan 接 `project_id`/`specialty_id`；specialty 字典 API + 编辑器下拉（接线或删列，不留半死列） | 0029 |
| P2 | 项目 update/archive + `record_audit`；`project_changed` 广播；导出按 sha 归档 | 两 ADR 治理面 |
| 触发再议 | 项目拆分运维路径、正式套件版本化、D1 复议 | — |

---

## 附录：本会话独立核验记录（2026-08-24 代码直接验证）

| # | 论断 | 结果 |
|---|------|------|
| 1 | `plan_dispatcher_sync.py:538` 创建 PlanRun 无 `project_id`/`build_version` | ✅ 属实（逐参数核对构造调用） |
| 2 | `create_plan`（`plans.py:489` 起）不写 `project_id`/`specialty_id` | ✅ 属实（`Plan(...)` 构造无此二字段） |
| 3 | 全后端非测试代码无 `project_id=`/`build_version=` 写入点 | ✅ 属实（grep 排除 tests/schemas/models 后零命中） |
| 4 | specialty 无 API 读路径 | ✅ 属实（routes 目录 grep 零命中） |
| 5 | `models/suite.py` 双检测器 + `project_id`/`apk_binding` 列 | ✅ 属实 |
| 6 | `suites.py:264` 自认 ACTIVE 守卫延至 P1b；export_to_tool_dir 无 409 | ✅ 属实（`:513-545` 仅 `_atomic_write`） |
| 7 | 全仓无 `inject_suite_params`/套件 precheck 逻辑 | ✅ 属实（grep 零命中） |
| 8 | CLI `tools/**/mtbf*` 不存在 | ✅ 属实 |
| 9 | `mtbf-api.md` §2 占位；ADR-0030 头部仍 Proposed | ✅ 属实 |
| 10 | 前端项目页齐全（ProjectsPage / ProjectDetailPage / AssignProjectDialog / ProjectFilterSelect） | ✅ 属实（`frontend/src/pages/projects/` 等） |

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-24 | 初版：独立核验式综合评审（0029 实现 7.5 / 0030 实现 6.5）、写路径断裂与 P1b 缺失定位、fleet 单值旋钮正确性悬崖论据、`plan.suite_id` 外键双模式设计、导出 sha 归档主张 |
