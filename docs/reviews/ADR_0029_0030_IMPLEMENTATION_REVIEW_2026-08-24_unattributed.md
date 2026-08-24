# ADR-0029 / ADR-0030 实现独立评审（快照缺口 + 排期反转 + 绑定上移）

- **状态**：Living（2026-08-24 初版；结论随实现推进修订）
- **日期**：2026-08-24
- **性质**：**综合评审**（非 ADR、非 Agent Note）——两份 ADR 落地现状独立评估 + 再设计建议
- **上游决策**：[ADR-0029](../adr/ADR-0029-project-taxonomy-and-param-layering.md)（项目分类域，Accepted）、[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md)（多用例平台化管理，Proposed）
- **背景分析**：[`PROJECT_TAXONOMY_REVIEW_2026-08-18.md`](./PROJECT_TAXONOMY_REVIEW_2026-08-18.md)、[`MTBF_MULTI_CASE_RESEARCH_2026-08-19.md`](./MTBF_MULTI_CASE_RESEARCH_2026-08-19.md)
- **产出会话**：用户提供的外部 agent 分析文本（**未署名，resume ID 未知**；原文件名尾缀 `4a7c2d91` 为整理时占位、无真实会话，2026-08-24 更正为 `unattributed`）
- **方法**：两份 ADR 的文本、迁移、模型、路由、前端、脚本与 Agent Notes 全部过目；关键缺口亲自到代码验证（验证结果见「附录：关键论断核验」）
- **与其他评审的关系**：[`ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_245a4531.md`](./ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_245a4531.md)（同主题第一份独立评审：评分 8 / 6.5，证据矩阵详尽）——本文件聚焦差异点与排期主张

---

## 0. 结论摘要（TL;DR）

| ADR | 决策文本质量 | 实现完成度 | 一句话结论 |
|-----|--------------|------------|------------|
| **ADR-0029**（项目分类域） | 9.5 / 10 | **7.5 / 10** | P1 建表回填做得极好，但**派发链路不写 `plan_run.project_id` 快照**，新 Run 永远是 NULL——登记簿的报表价值从第一天起就在漏新数据，且该缺口未被任何 note/issue 跟踪 |
| **ADR-0030**（多用例管理） | 9 / 10 | **7 / 10** | P0 已真机验收、P1a 管理面超预期，但 **P1b（precheck 门禁 / 快照冻结 / 409 在途守卫）整段未做**——恰好是承担风险的那一半；ADR 状态还停在 Proposed 没传播 |

**最高优先级主张**：派发快照与建表回填**同一个 PR** 落地（0029）；**P1b 与 P1a 同批甚至先行**（0030）。

---

## 1. ADR-0029 实现评分：7.5 / 10

### 1.1 做得好的部分（证据核过）

- **模型与迁移严格贴合 v2 最小形态**：`backend/models/project.py:28-61` 只有 `project_key` / `jira_project_key` / 四 facet 列 / `status`，**没有越界建 `storage_key`/`variables`**；挂起的 D1/D4/D5/D7/D8/D9 **零越界实现**——纪律性满分，这在 ADR 演进频繁的仓库里不多见。
- **回填是全 ADR 实现最强的部分**：`tools/dev/backfill-test-project.py` 完整实现 ADR 全部约束——dry-run 清单、幂等（`WHERE project_id IS NULL`）、清单外设备拒绝执行、`device.project_id` NULL 归零否则 exit 2。生产执行记录（[`docs/notes/feature/2026-08-19-adr-0029-p1-project-backfill.md`](../notes/feature/2026-08-19-adr-0029-p1-project-backfill.md)）显示 **545 台全部归位**（dry-run 修正了 ADR 快照的 515）。
- **独立前置项兑现**：`backend/realtime/socketio_server.py:319-400` 的 `on_subscribe` 已加 room 格式白名单 + 实体存在性 fail-closed 校验，配套 bug-fix note 的定性也按 v2.3 纠正口径写的。
- **前端 P2 大体落地**：`/projects` 列表（facet 组合筛选）+ `/projects/:projectKey` 详情、设备页批量归入（admin-only、每台一条审计）、Plan/PlanRun/结果页项目标签与筛选、`types.ts` 同步。

### 1.2 扣分点

| 扣分 | 项 | 证据 |
|------|-----|------|
| **-1.5** | **致命：派发不写项目快照** | `plan_dispatcher_sync.py:538` 创建 `PlanRun` 时无 `project_id`/`build_version`，且全后端 `build_version` **没有任何写入点**。后果：派发产生的新 Run 在项目详情「最近运行」、结果页项目筛选、PlanRun 列表项目筛选里**全部不可见**（这些都按 `plan_run.project_id` 快照过滤，`results.py:162,179,238` 已确认）。更讽刺的是 P2 前端 note 把「快照语义」当交付物写进 UI 文案（`notes/feature/2026-08-20-project-registry-frontend-p2.md:20,29`），而 `test_project_routes.py:92` 的快照测试是**直接构造带 project 的 run 绕过派发链路**，恰好掩盖了这个洞。ADR-0029 D5 挂起时明确保留了「plan_run 快照仅留 project_id 与 build_version（登记/报表维度）」——**这一条被实现丢了** |
| **-0.5** | D2 审计要求空转 | ADR 要求 `test_project` 所有变更走 `record_audit`，但 `routes/projects.py` 只有 5 个 GET + 建项目 POST + map 系，**无 update/archive/PATCH 入口**——建项目/归档只能跑脚本，「审计」无对象可审计（全仓无 `update_project`/`archive_project` action）。设备归入那条线反而做得对（`devices.py:118-181` 逐台审计） |
| **-0.3** | specialty 半死不活 | 字典表、种子、`plan.specialty_id` 列都在，但 **API 不暴露、前端零引用**——D6 当初保留它的理由就是「Plan 列表分组高频使用」，使用面没落地，等于白付 schema 成本 |
| **-0.2** | `project_changed` 广播延期（已文档化） | P2 note 以「独立小 PR 评审」延期（`notes/feature/2026-08-20-project-registry-frontend-p2.md:38,54`）。但注意**叠加效应**：D5 门禁挂起 + 无广播 = A 浏览器把设备移出项目后，B 浏览器陈旧缓存一路放行到派发成功，没有任何一层会拦。ADR 自己论证过这正是广播存在的理由，实现代价约 20 行 |

---

## 2. ADR-0030 实现评分：7 / 10

### 2.1 做得好的部分

- **P0 完整且真机验收**：三件套脚本版本化演进到 setup v1.3.0 / check v1.2.0 / finish v1.4.0，`suite_sha256` 留痕、PROGRESS 打戳（#115 停滞钟）、NFS 逐条结果 JSON 落盘三链路齐全，25+32 条脚本测试 + PlanRun #217/#218 验收记录在案。这一段无可挑剔。
- **P1a 超出 ADR 文本**：14 个端点全量落地（写=admin、全量 `record_audit`）；`mtbf_suite.py:523` 的 `content_fingerprint` + `exported_content_sha256` **双检测器是 ADR 没有的增补**，能区分「库改了没导出」和「导出物被手改」两种漂移；`root_config` 用 `sa.JSON` 而非 JSONB 以保键序、导出物逐字节同构——细节意识很好。
- **原子写已实现**：`suites.py:574-588` 的 `_atomic_write`（mkstemp + fsync + os.replace）。

### 2.2 扣分点

| 扣分 | 项 | 证据 |
|------|-----|------|
| **-2** | **P1b 整段缺失——缺的恰是承担风险的半** | 现状：管理面可以改库、可以 export-to-tool-dir，但**派发链路消费的仍是工具目录文件，没有任何校验**。「库改了没导出」目前只有一个 advisory 的 `X-Export-Stale` 响应头；export 覆盖 `config/runtask.xml` 时**没有在途 PlanRun 守卫**——MTBF 一跑就是以天计的长跑，管理面现在具备**在跑中途换清单**的能力且无人拦截。事后归因是有的（P0 的 init trace 记了 `suite_sha256`），但**事前拦截为零**。管理面本身还引入了第二事实源（DB），漂移面比纯文件时代（方案 A）反而更大了 |
| **-0.5** | 状态传播债 | ADR-0030 v1.1 专门抄了 ADR-0029 v2.3.1 的「七挂靠位传播清单」教训，然后**自己的状态行还停在 Proposed**——P0 已 ✅ 验收、P1a 已合入（commit `e4cde10`），按它自己的规则早该翻 Accepted。`docs/operations/mtbf-api.md` §2 也还是占位，尽管 14 个端点已经上线。**「制定了规则的人自己第一个违反」** |
| **-0.3** | CLI（P1c）未做 | `tools/mtbf_cases.py` 不存在；D4 的 `X-Agent-Secret` 双通道也没做（这条 ADR 自己说「初版保守」，可接受） |
| — | P2 未动 | **不扣分**——分阶段本来就是决策，触发条件未到 |

---

## 3. 如果我来设计

### 3.1 先同意什么（不值得翻案的核心判断）

- **facet 而非层级树**（MLD/ELA 同客户同平台同形态必须分两项目，第一层无论按哪个维度都分不开——论证扎实）；
- **5 项目规模下登记簿而非强制隔离**（D5/D7/D8/D9 挂起 + 明确复议触发条件，防止机制早熟）；
- **用例集作为配置层实体不进调度模型**（`script:<name>` 不变量、版本不可变、整套循环语义三条约束下，130 个 PlanStep 展开和 `default_params` 承载清单都是明确更差的路线）；
- **P0 先行**（已被真机验收证明）。

以下只讲会改的地方。

### 3.2 项目域（0029）：把「快照」从可延期项提为 P1 阻塞项

排期原则：**登记簿在这个规模下的唯一持久价值是报表/可观测性**，所以「新数据能不能按项目看见」必须是第一验收标准，而不是最后一项。

1. **派发快照与建表回填同一个 PR 落地。** 改法很小：`plan_dispatcher_sync.py` 建 `PlanRun` 时从 `plan.project_id` 冻结；PRECHECK 准入时（仓库已有 QUEUED→PRECHECK 再校验的哲学和挂载点）顺手补 `build_version`。
2. **`build_version` 的语义 ADR 其实没定清楚**——一次 Run 覆盖 N 台设备、各有 build。建议：存 `run_context` 里的 **per-device build map**，`plan_run.build_version` 列只在全部设备同版本时写值、分歧时留 NULL。实现选择了「不实现」而不是「实现错」，可以理解，但**没记录这是缺口**，导致前端拿「快照语义」当已交付能力写了文案。
3. **specialty 要么接线要么不建列。** 接线很便宜（plans API 输出 + Plan 列表一维分组），而 D6 保留它的全部理由就在使用面。**半死列是最差的状态。**
4. **项目 CRUD 提前。** 5 个项目、创建一次，脚本建项短期可忍，但 D2 的审计语义要求变更入口存在——一个带 `record_audit` 的 POST/PATCH 半天工作量，不必等 P2.5c。
5. **`project_changed` 广播现在就补。**「独立小 PR 评审」是流程理由不是技术理由，而它的缺失与 D5 挂起叠加后是**零拦截链**。

### 3.3 多用例（0030）：排期反转 + 绑定上移到 Plan 级

1. **P1b 与 P1a 同批甚至先行。** 理由：P0 已经把 `suite_sha256` 记进 init trace——**归因原语已存在**，缺的只是把「事后归因」升级为「事前比对」，这是全链路里性价比最高的一段，却排在了最后。退一步说，即使 suite 绑定字段还没定，**409 守卫也有弱版可先做**：export-to-tool-dir 时检查同 `export_dir` 下是否存在 ACTIVE（RUNNING/QUEUED/PRECHECK）且步骤引用 mtbf 系脚本的 PlanRun，有就拒。**没有门禁的管理面等于给长跑运行时装了一个无人看守的换弹夹扳机。**
2. **绑定位置不放 `plan_step.default_params`，放 `plan.suite_id` 可空外键。** 理由有三：
   - ADR-0029 D6 自己确认「一计划一专项」已是现状——套件就是 Plan 的测试内容，按 step 绑是过度泛化；
   - dispatcher 注释里 WiFi 注入被称为参数逻辑的「唯一例外」，`suite_key` 进 default_params 会造出**第二个特例**，侵蚀这个不变量；
   - 外键列让 precheck 直接 join 校验，不用解析 JSON，且**天然获得 DB 层引用完整性**。ADR 说「若 D1 复议通过再改走 params_override」——独立列同样不妨碍未来这条路，迁移成本比从 JSON 特例里迁出来更低。
3. **快照冻结字段与 0029 的 `project_id` 快照在同一个函数点一次写齐**（`suite_id` / `exported_sha256` / `apk_binding`）——**两个 ADR 的收口点其实是同一个**。
4. **状态传播先于下一行代码**：ADR-0030 翻 Accepted、`mtbf-api.md` §2 写实——这份 ADR 自己抄了 0029 的传播教训清单，没理由成为下一个反面案例。

---

## 4. 落地顺序建议

| 优先级 | 事项 | 对应主张 |
|--------|------|----------|
| **P0** | 派发快照（`plan_dispatcher_sync.py` 冻结 `project_id` + PRECHECK 补 `build_version`，per-device map 语义） | 0029 快照 |
| **P0** | P1b 门禁（precheck 五步 + run_context 冻结 + 409 在途守卫弱版先行） | 0030 P1b |
| **P0** | ADR-0030 状态传播（翻 Accepted + mtbf-api §2 写实） | 0030 传播 |
| P1 | specialty 接线（plans API 输出 + Plan 列表分组）或删列 | 0029 |
| P1 | 项目 CRUD + `record_audit`（POST/PATCH，半天工作量） | 0029 审计 |
| P1 | `project_changed` 广播（约 20 行） | 0029 广播 |
| P2 | `plan.suite_id` 外键绑定（与 0029 快照同一函数点） | 0030 绑定 |

---

## 附录：关键论断核验（2026-08-24 代码直接验证）

| 论断 | 核验结果 |
|------|----------|
| `plan_dispatcher_sync.py:538` 创建 PlanRun 无 `project_id`/`build_version` | ✅ 属实（`pr = PlanRun(plan_id, status, failure_threshold, plan_snapshot, run_type, run_context, triggered_by, ...)`，无 project_id/build_version） |
| 全后端 `build_version` 无写入点 | ✅ 属实（`grep -rn build_version backend/ --include="*.py"` 排除 models/schemas/migration 后零命中） |
| results 按 `plan_run.project_id` 快照过滤 | ✅ 属实（`results.py:233-240` outerjoin PlanRun.project_id + `_scope_by_project`） |
| `test_project_routes.py:92` 直接构造 PlanRun 绕过派发 | ✅ 属实（测试 fixture 直接 `PlanRun(project_id=...)`，未走 dispatcher） |
| `projects.py` 无 update/archive/PATCH 路由 | ✅ 属实（8 端点：1 POST 建项目 + map preview/apply + models + 5 GET；全仓无 `update_project`/`archive_project` audit action） |
| specialty 无 API / 前端零引用 | ✅ 属实（`project.py:91-104` 字典 + `plan.specialty_id` 列在；routes 与 frontend 零引用） |
| `project_changed` 广播未实现（P2 note 延期） | ✅ 属实（`notes/feature/2026-08-20-project-registry-frontend-p2.md:38,54`「独立小 PR 评审」；frontend 全仓无 `project_changed`） |
| `mtbf_suite.py:523` content_fingerprint 双检测器 | ✅ 属实（`content_fingerprint` 在 :523；`exported_content_sha256` 双基线） |
| `suites.py:574` 原子写 | ✅ 属实（`_atomic_write` mkstemp + fsync + os.replace） |
| `plan.suite_id` 外键不存在 | ✅ 属实（`plan` 表无 suite_id 列；新建议未实现） |

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-24 | resume 尾缀更正：原 `4a7c2d91` 为整理时占位（无真实会话），改为 `unattributed`，头部「产出会话」同步更正 |
| 2026-08-24 | 初版：ADR-0029/0030 独立评审（7.5 / 7 分）、致命缺口「派发不写项目快照」、排期反转主张（P1b 与 P1a 同批）、`plan.suite_id` 外键绑定建议、关键论断核验附录 |
