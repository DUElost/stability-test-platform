# ADR-0030: 多用例平台化管理（test_suite / test_case + 外部管理面）

- 状态：**Accepted**（2026-08-24 推进；P0 真机验收 + P1a 实体/管理面已合入，P1b 绑定门禁与 P2 见修订记录 v1.3）
- 优先级：**P0（专项接入主线，可先行独立交付）+ P1（多用例实体与管理面）**——见 D6
- 目标里程碑：M7
- 日期：2026-08-19
- 决策者：平台研发组
- 标签：多用例, 用例管理, MTBF, 项目分化, 外部管理接口
- 背景分析：[`MTBF_MULTI_CASE_RESEARCH_2026-08-19.md`](../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md)（runtask.xml 实测结构 / 平台缺口 / 候选形态对比）

## 修订记录

| 日期 | 版本 | 内容 |
|------|------|------|
| 2026-08-19 | v1.1（评审修正） | 按评审六项必改修正：① 新增「与 ADR-0029 的关系（显式和解）」——test_suite 是 ADR-0029 非目标 ExecutionProfile 实体族的**例外子集**，不复活挂起决策；② D3 套件项目匹配改为本 ADR 自有门禁 **D3b**，不再引用挂起 D5；③ 脚本命名统一为 `mtbf_setup`/`mtbf_check`/`mtbf_finish` 三件套（monkey 先例），禁止两种命名并存；④ 补 Plan↔Suite↔plan_snapshot↔precheck 绑定说明（D2）；⑤ 优先级改双轨 P0+P1；⑥ 状态传播补齐 DOC-MAP / CLAUDE.md / 05-data-model（实施时）。**状态传播挂靠位**：ADR 头部状态行 / 正文修订记录 / reviews 对应节 / adr README 清单行 / adr README 里程碑行 / DOC-MAP Living 表 / CLAUDE.md 决策表（对齐 ADR-0029 v2.3.1 教训） |
| 2026-08-20 | v1.2（P0 真机验收） | **D6 P0 验收信号达成**（PlanRun #217/#218，设备 395，abort→teardown→finish 协议）：init `suite_sha256` ✓、PROGRESS + patrol-heartbeat ✓、NFS JSON 落盘 + §6 复核 0 不一致 ✓。验收记录见 [Agent Note](../notes/feature/2026-08-20-mtbf-p0-scripts-and-validate.md)。ADR 整体仍为 Proposed（P1 实体/管理面未实施）。 |
| 2026-08-24 | v1.3（状态推进 + P1a 记账） | **状态 Proposed → Accepted**：P0 已验收（v1.2）+ P1 实体/管理面已合入 main（`test_suite`/`test_case` 表、14 端点 CRUD/import/export/validate/export-to-tool-dir、全量 `record_audit`；增补双漂移检测器 `content_fingerprint` + `exported_content_sha256`——区分「库改了没导出」与「导出物被手改」，超出本文 D5 原文，属实现层增强；渲染三列用 JSON 保键序逐字节同构；原子写落地）。同批补记两项缺口修复：#401（dispatcher 冻结 `project_id`/`build_version` 快照）、#402（export-to-tool-dir 在途守卫弱版 409 + `force` 审计留痕——D2 的可先行半段，精确匹配待绑定字段）。**仍未做**：D2/D3b 绑定与 precheck 五步门禁（#404）、CLI（P1c）、P2 前端与 `test_case_result`。七挂靠位同步：本行 / 头部 / adr README 清单行 / adr README M7 行 / CLAUDE.md 决策表 / DOC-MAP / [mtbf-api.md §2](../operations/mtbf-api.md) 定稿 |
| 2026-08-24 | v1.4（D2 绑定机制修订） | **绑定从 `plan_step.default_params.suite_key` 注入特例上移为 `plan.suite_id` 可空外键**。理由：① dispatcher 注释明示 WiFi 注入是参数逻辑**唯一例外**，`suite_key` 走 default_params 直接侵蚀该不变量；② 「一计划一专项」是 ADR-0029 D6 确认的现状，套件即 Plan 的测试内容，按 step 绑定属过度泛化；③ 外键让 precheck 直接 join 校验并获得 DB 层引用完整性；④ 可空外键天然给出双模式语义（NULL = P0 文件真源模式不加门禁 / 非空 = 托管模式五步门禁），零数据迁移；⑤ 未来 D1 复议走 params_override 的路径不被堵死（从独立列迁移比从 JSON 特例迁移成本低）。API 面以套件对外键 `name` 引用（PlanCreate/PlanUpdate 接受 `suite_name`），数字 id 只留 DB。设计文档 [P1 设计 §3](../design/2026-08-mtbf-p1-suite-management.md) 同步重写。放弃的备选：维持注入特例（侵蚀不变量）、等 D1 复议后走 params_override（相机 MTBF 前等不起，且 D1 复议条件未全触发） |

## 背景

MTBF 专项的用例清单 `runtask.xml`（`/mnt/automation-toolkit/android-tools/stability_MTBF-Test/config/`）实测为
**130 testpoint（用例粒度）/ 137 testcase（执行描述）**，由设备端 `OfflineScriptManager` 整套循环执行、
结果落 `/sdcard/results/realresult/*.xml`。平台目前**没有「用例」实体**——`Script`（可执行脚本）与
`Plan/PlanStep`（编排）之间不存在用例清单这一配置层，既看不到 MTBF 跑的是哪 130 条用例，也看不到逐条结果。

三条既有约束决定「多用例」不能走简单路线：

1. **唯一 action 类型 `script:<name>` 是不变量**（CLAUDE.md 架构不变量），用例不是执行单元；
2. **`default_params` 版本内不可变 + 版本目录不可变**（ADR-0020）——把 130 条清单塞进脚本参数/文件会触发版本膨胀
   （ADR-0029 背景已估算：逐项目分化需新增 15–40 个版本）；
3. **MTBF 的语义是「整套循环 N 圈」长跑**——把用例展开成 130 个 PlanStep 既违背语义，也制造 130×设备 的无意义 step_trace。

已确认事实（决策者 2026-08-19）：现有 MTBF 的 `runtask.xml` 用例内容**大部分情况稳定不变**；
**后续新增的相机 MTBF 用例集将随项目变化较频繁**；**用例 APK 与项目严格对应**（某版本 APK 只能跑对应项目）。
这与 ADR-0029 R3 的「用例 APK 逐项目不同」一致，且把分化从「仅 APK」扩展到「清单本身（相机 MTBF）」。

## 决策

### D1：用例集/用例建模为配置层实体，不进调度模型

新增 `test_suite`（用例集 ≈ 一个 runtask.xml）与 `test_case`（用例，**粒度 = testpoint**，
内含 1..N 个 testcase 执行描述 `exec_descs`）。草案字段见背景分析 §5.1（含 `root_config`、
`global_params`、`apk_binding`、`source_sha256`）。

边界（与既有机制的分工）：

- 用例库是**配置数据**，**不进 `STP_SCRIPT_ROOT`**（那是可执行脚本的版本化目录）；渲染器作为脚本辅助模块（`_` 前缀）住脚本版本目录。
- **不**把用例展开成 PlanStep；**不**引入新 action 类型；**不**把清单塞进 `default_params`。
- 执行粒度不变：init / patrol / teardown 三阶段**各绑一个脚本**（`script:mtbf_setup` / `script:mtbf_check` /
  `script:mtbf_finish`，monkey 拆分先例同构），每设备一个 Job。实施时若改选「单入口 + 阶段参数」形态，
  须同步修订本文与研究 §5.3——两种命名**不允许并存**。

### D2：文件 ↔ 库双向通道（runtask.xml 变生成物）

- **import**：解析 runtask.xml → 库（upsert，记 `source_sha256` 与审计）。
- **export**：库 → runtask.xml 渲染（支持 `times` 覆盖、`@@全局变量` 引用保留），可选直接写入工具目录 `config/runtask.xml`（admin）。
- **validate**：XML schema / 重复 method / `@@var` 引用完整性；APK 内 class/method 存在性离线不可验，留待运行时校验（fail-fast）。
- 执行链**消费面不变**：脚本仍从工具目录读文件 push 设备。「管理面从改共享盘文件升级为 API/CLI，消费面不变」是平滑迁移的关键。
- **Plan ↔ Suite 绑定**（precheck 闭环前提）：~~`plan_step.default_params` 显式声明 `suite_key`
  （初版机制——与 WiFi 注入并列的注入特例；若 ADR-0029 挂起 D1 复议通过，再改走 `params_override`）~~
  **v1.4 修订：绑定上移为 `plan.suite_id` 可空外键**（NULL = P0 文件真源模式不加门禁；
  非空 = 托管模式全门禁）——理由见修订记录；派发快照 / `plan_run.run_context` 冻结
  **`suite_id` / `exported_sha256` / `apk_binding`** 三字段。
- **派发门禁（precheck 校验链）**：① suite 存在且 `is_active`；② 已导出（工具目录文件存在）；③ 磁盘文件
  sha256 与库内一致（= 库未改或已重导，「库改了没导出」在此拦截）；④ 套件 `project_id` 与目标设备项目匹配
  （D3b）。任一项失败 **fail-fast**，禁止带病派发。
- **export-to-tool-dir 并发安全**：长跑中覆盖 `config/runtask.xml` 会与在跑 Job 冲突。落盘采用 **atomic write**
  （临时文件 + rename）；且存在引用该套件的 ACTIVE（RUNNING / QUEUED / PRECHECK）PlanRun 时**拒绝覆盖**（409，
  参照 scripts scan `force_rebaseline` 的在途守卫），或先写 staging 目录再显式切换。

### D3：项目分化（project_id 可空 + 套件级 APK 绑定）

- `test_suite.project_id` **可空 = 通用套件**：现 MTBF 清单大部分稳定，一份套件多项目共用（沿用 ADR-0029「族内共用同一用例 APK」事实）。
- **必填 = 项目套件**：相机 MTBF 等随项目频繁变化的清单；套件级 `apk_binding` 声明用例 APK（APK↔项目严格对应）。

**D3b：套件项目匹配门禁（本 ADR 自有，不复活 ADR-0029 D5）**。派发/准入时校验
「套件归属项目 == 目标设备项目」，跨项目拒绝。动机是**配置误配防护**（把 A 项目的套件跑到 B 项目设备上），
与挂起 D5 的动机（多客户权限/容量隔离）不同——D5 复议触发条件未触发前，本门禁不构成 D5 的复活或变体。

- 与 ADR-0029 的关系：套件 `project_id` 是**配置数据的项目归属**，不复活 ADR-0029 挂起的 D4（参数注入机制）；
  执行期 APK 差异仍由脚本端设备指纹路由吸收（`backend=auto` 先例）。若项目 class/method 名与通用套件不同 → 建项目套件（引用同一 `mtbf_*` 脚本）。

### D4：外部管理面（接口/CLI/文档）

- **REST 为主通道**：`/api/v1/test-suites` + `/api/v1/test-cases` 的 CRUD / import / export / validate /
  export-to-tool-dir（端点草案见背景分析 §5.5）。复用控制面 8000 端口，**不新增端口**（Agent `:8900` 已按 ADR-0025 取消暴露）。
- **鉴权**：读 = 登录用户；写 = admin（参照 `_require_plan_owner_or_admin` 的 owner 模式可选）；外部 agent 双通道
  （用户 token / `X-Agent-Secret`，后者写权限在实施评审时定，初版保守）。
- **全量审计**（ADR-0015）：suite/case 的 create/update/delete/import/export/export-to-tool-dir 全部 `record_audit`。
- **CLI 便捷层**：`tools/mtbf_cases.py`（list/show/import/export/validate），走同一 REST，凭据取自仓库根 `.env.backend` 约定，明文不进 log。**位置与命名实施时在仓库先例内二选一**（`tools/dev/` 单文件 kebab-case vs 独立 `tools/stpctl/`），选定后回写本 ADR 修订记录。
- **接口文档**：OpenAPI（`/docs` + `/openapi.json`）为真源；`docs/operations/` 补「MTBF 用例管理接口说明」（curl 示例 + 权限），**文档先行**。

### D5：可复现与留痕

- 派发快照记录套件引用（suite id/版本）+ 清单 sha256（沿用 ADR-0029 v2.2 对工具目录文件的补偿机制）；
  同快照两次 run 结果不同可归因「清单被改」。
- 版本策略：P1 先做「快照留痕」，**不做** copy-on-write 版本库；套件版本化等真实分化需求出现再引入（触发条件见下）。

### D6：分阶段落地（本 ADR 仅定决策方向，暂不实施）

> **P0 属专项接入主线，可独立于 P1 先行交付**（不依赖 `test_suite` 表）；优先级标 P0+P1 双轨即为此意，
> 避免「标 P1 却先做 P0 工作量」的排期误导。

| 阶段 | 内容 | 验收信号 |
|------|------|----------|
| **P0** | `mtbf_setup`/`mtbf_check`/`mtbf_finish` 脚本组（deploy/start、轮询 + PROGRESS 打戳 + stall_seconds、stop/pull + realresult 解析）；只读预览/校验 API（输入源语义见研究 §5.5）；清单 sha256 留痕 | **✅ 已验收（2026-08-20）**：PlanRun #218（abort 收尾）+ #217（整链 init→patrol→设备端 130 条）；init trace `suite_sha256`、NFS `mtbf/legacy/results/{run_dir}.json`、§6 XML↔JSON 复核通过。详见 Agent Note §冒烟收尾记录 |
| **P1** | `test_suite`/`test_case` 表 + CRUD/import/export/validate + 审计 + CLI + 导出落工具目录 + D2 绑定与 D3b 门禁 | 外部 agent 仅凭 API/CLI 完成「导入既有 130 条 → 改 1 条 → 导出 → 派发」，全程有审计 |
| **P2** | 前端用例管理页 + PlanRun 逐条用例结果表（`test_case_result`） | 平台页面可浏览用例集与逐条结果，无需 adb |

## 与 ADR-0029 的关系（显式和解）

ADR-0029 非目标明确放弃版本化 ExecutionProfile 实体族（5 张表：工具包发布 / 允许脚本 / **套件** / 策略 /
报表 Profile，见其「备选方案与权衡」§3，放弃理由：ADR-0020 版本目录不可变 + `content_sha256` + `plan_snapshot`
三层 pin 已覆盖核心诉求）。本 ADR 引入 `test_suite` / `test_case` 属**窄化复活**，边界如下：

- **例外子集范围**：仅限 runtask.xml 等价配置（用例清单 + 全局参数）+ import/export/validate 双向通道；
  **不做** `execution_profile_version` / `tool_bundle_release` / `allowed_script_release` / `report_profile` 全家桶。
- **不复活挂起决策**：ADR-0029 挂起的 D1（步骤参数覆盖）/ D4（三层参数）/ D5（派发门禁）/ D7（存储命名空间）/
  D8/D9（全局项目上下文）**保持挂起**；本 ADR 不引用、不重启其复议条件（D3b 是本 ADR 自有门禁，见 D3）。
- **演化边界**：若 test_suite 演化为通用 ExecutionProfile（跨专项的套件/策略/报表 Profile 机制），
  必须**另开新 ADR** 复议 ADR-0029 §3 的放弃理由，不得在本 ADR 框架内扩权。
- **依赖**：套件项目归属依赖 ADR-0029 的 `test_project` / `plan.project_id`（已 Accepted，P1 建表回填中）。

两份 Accepted ADR 的判定合一：**「要不要 test_suite 表」不冲突——0029 拒绝的是通用执行配置实体族，
0030 批准的是 MTBF 多用例配置层的例外子集**；任何一方越过此边界即触发对方复议。

## 备选方案与权衡

| 方案 | 内容 | 结论 |
|------|------|------|
| A 文件真源 | runtask.xml 留工具目录，平台只读预览/校验 + sha256 留痕 | 仅作 P0 过渡：无审计、无 UI 编辑、外部写路径无校验，不满足「在平台上管理」 |
| B 实体终局 | 表 + CRUD API + import/export + 前端页，一步到位 | 终局形态，但重；相机 MTBF 分化需求未到，过早机制化（对照 ADR-0025「先跑通再谈机制」排序） |
| **C 混合分阶段（采纳）** | P0 过渡 → P1 实体与外部管理面 → P2 体验 | 每阶段独立可交付、可回退；P1 的 import 即把 P0 文件真源搬进库，消费面不变 |

明确拒绝的路线：130 个 PlanStep 展开；新 action 类型；`default_params` 承载用例清单；
用例库进 `STP_SCRIPT_ROOT`。

## 影响

- **DB**：新增 `test_suite` / `test_case`（P2 加 `test_case_result`），additive migration（ADR-0008）。
- **API**：新增约 13 个端点（草案见研究 §5.5）。**结果落库主路径（P0 定，与 P0 设计 §5.3 一致）**：
  摘要 metrics + `suite_sha256` 走 step_trace（stdout JSON，规避 64KiB 截断）；**逐条结果写中心存储**
  `{STP_AEE_NFS_ROOT}/mtbf/{project}/results/{run_dir}.json`（`report_json` 为控制面合成（`report_service`），脚本不写）；
  `JobArtifact` 白名单扩展报告类型（如 `report`）**留待 P2** 大文件/下载场景。
- **审计**：`record_audit` 覆盖新资源类型（ADR-0015）。
- **前端**（P2）：用例管理页 + PlanRun 用例结果区块。
- **前置条件**：配置/产物通道见 [P0 设计 §4](../design/2026-08-mtbf-p0-runner-design.md)（方案已定：清单/全局参数走中心存储
  `{STP_AEE_NFS_ROOT}/mtbf/{project}/`，APK 走 Agent resources；与 PowerCycle 对齐，P0 实施 PR 登记存储角色表）。
- **文档**：研究文档（已入库）+ 本 ADR + [`docs/operations/mtbf-api.md`](../operations/mtbf-api.md)（§1 P0 validate 定稿 /
  §2 P1 已定稿）+ `docs/design/05-data-model.md`（**实施 P1 时更新**，加 test_suite/test_case 表行）。

## 落地与后续动作

开放问题（详见研究 §7，实施前逐项关闭；评审已定调的项目直接采纳）：

1. 设备端 realresult XML 精确 schema——**已定稿 + 真机复核关闭**（反编译定稿见 [P0 设计 §2](../design/2026-08-mtbf-p0-runner-design.md)；PlanRun #218 NFS JSON vs 设备端 XML **38/38 0 不一致**，见 Agent Note §冒烟收尾记录）。
2. 工具目录 Agent 可达性——**方案已定**（P0 设计 §4 推荐：清单/全局参数走中心存储 `{STP_AEE_NFS_ROOT}/mtbf/{project}/`，APK 走 Agent resources 目录，逐条结果写回 `mtbf/{project}/results/`）；与 PowerCycle 统一，实施 PR 对齐目录约定。
3. 外部写权限模型：**初版写 = admin**；`X-Agent-Secret` 只读或限定 import/export，P1 评审定。
4. `times` 覆盖链定稿：**`task_times` 仅影响 export/deploy**（渲染/部署时的覆盖参数），库内 `root_config.times` 为套件默认值。
5. 结果落库：**已定稿**（与「影响」段一致）：摘要 metrics + `suite_sha256` 走 step_trace；逐条写中心存储
   `mtbf/{project}/results/{run_dir}.json`；`report_json` 为控制面合成（`report_service`），脚本不写；P2 大文件再走 artifact 白名单扩展。
6. 套件版本化：触发复议条件足够（见下），暂不机制化。

**触发复议条件**（防兜圈子，未触发前不得重提）：

- 套件版本化：项目分化导致「同套件多版本并行维护」成为实际问题；
- D3 粒度：相机 MTBF 需要逐 testcase 的 APK/项目绑定；
- 用例级结果入库（`test_case_result`）需求成熟（P2 引入）。

## 关联实现/文档

- 背景分析：[`docs/reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md`](../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md)
- **P0 设计**：[`docs/design/2026-08-mtbf-p0-runner-design.md`](../design/2026-08-mtbf-p0-runner-design.md)（脚本三件套契约 / realresult schema 实测 / 配置与产物通道）
- **P0 验收记录**：[`docs/notes/feature/2026-08-20-mtbf-p0-scripts-and-validate.md`](../notes/feature/2026-08-20-mtbf-p0-scripts-and-validate.md)（PlanRun #214–#218、fleet 收口、closure 判据）
- **接口运维**：[`docs/operations/mtbf-api.md`](../operations/mtbf-api.md)（§1 P0 validate / §2 P1 管理面）
- [ADR-0020](../adr/ADR-0020-plan-step-one-shot-migration.md)（脚本目录契约 / default_params 不可变）
- [ADR-0029](../adr/ADR-0029-project-taxonomy-and-param-layering.md)（项目登记簿 / 脚本路由 / sha256 留痕补偿机制）
- [ADR-0015](../adr/ADR-0015-audit-log-system.md)（审计）、[ADR-0025](../adr/ADR-0025-phase4-architecture-alignment.md)（方案 C 存储，8900 取消暴露）
- #115 步骤停滞钟（PROGRESS 打戳，MTBF 长跑 patrol 前置）
