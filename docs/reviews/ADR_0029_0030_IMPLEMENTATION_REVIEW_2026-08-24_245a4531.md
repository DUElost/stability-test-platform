# ADR-0029 / ADR-0030 实现综合评审（落地复盘 + 评分 + 再设计建议）

- **状态**：Living（2026-08-24 初版；结论随实现推进修订）
- **日期**：2026-08-24
- **性质**：**综合评审**（非 ADR、非 Agent Note）——两份 ADR 落地现状核对 + 评分 + 差距清单 + 再设计建议
- **上游决策**：[ADR-0029](../adr/ADR-0029-project-taxonomy-and-param-layering.md)（项目分类域，Accepted）、[ADR-0030](../adr/ADR-0030-multi-case-suite-management.md)（多用例平台化管理，Proposed）
- **背景分析**：[`PROJECT_TAXONOMY_REVIEW_2026-08-18.md`](./PROJECT_TAXONOMY_REVIEW_2026-08-18.md)、[`MTBF_MULTI_CASE_RESEARCH_2026-08-19.md`](./MTBF_MULTI_CASE_RESEARCH_2026-08-19.md)
- **产出会话**：resume `245a4531-2be3-47ea-9e33-8b54cb49274a`（短 ID `245a4531`，文件名尾缀）
- **方法**：代码直接核验（`backend/models/`、`backend/api/routes/`、`backend/agent/scripts/mtbf_*/`、`backend/alembic/versions/`、`frontend/src/`、`tools/dev/`）+ 生产库只读实测（`127.0.0.1:5432/stp`）+ 双 Explore agent 交叉验证

---

## 0. 结论摘要（TL;DR）

| ADR | 落地度 | 评分 | 一句话 |
|-----|--------|------|--------|
| **ADR-0029** | ≈ 85% | **8 / 10** | 决策质量 9（v2 决策转向是近年最佳取舍）、执行质量 8；扣分：Plan 编辑器写入断点 + specialty 读路径半截 + P3 挂起无记录 |
| **ADR-0030** | ≈ 60% | **6.5 / 10** | P0/P1a 执行 9（字节级保真 + 结构性漂移检测无可挑剔）；扣分：**P1b 绑定门禁完全缺失**（管理闭环断在派发门口）+ P1c 状态传播滞后（自家 v2.3.1 教训复发） |

**最高优先级建议**：P1b 门禁 + Plan 编辑器归属写入——同时补上两份 ADR 各自最大的洞。

---

## 1. 现状核对矩阵（证据 file:line）

### 1.1 ADR-0029（项目分类域）

| 决策项 | 状态 | 证据 |
|--------|------|------|
| D2 `test_project` + facet + `jira_project_key` | ✅ 完整 | `backend/models/project.py:41-88`（`source`/`match_models`/`lower(project_key)` 唯一索引 :82-87） |
| D3 归属列（plan/device/plan_run） | ✅ 完整 | `backend/models/plan.py:54,56`、`host.py:85`、`plan_run.py:52,54`；迁移 `r5s6t7u8v9w0` |
| M-b/M-c 回填 + NULL 归零 | ✅ **生产执行** | `tools/dev/backfill-test-project.py`（dry-run 必备 :296-297、幂等仅 NULL 行、完成标准 exit 2）；生产库实测 547 台全有 project_id、6 SEED + 2 USER 行 |
| 精确映射（禁止前缀推断） | ✅ 严格 | `_map_preview` 等值匹配 `projects.py:211-216`；回填清单外拒绝 exit 2（`backfill-test-project.py:205-217`）；前端禁用（`MapModelsDialog.tsx:65`）；全仓零 `startswith` |
| D6 `specialty` | ⚠️ **半截** | 表 + 种子有（`project.py:91-104`、`backfill-test-project.py:87-91`）；**无 API 读路径、无前端下拉**——Plan 编辑器不可选 |
| P2 登记簿页 + 四页筛选 | ✅ | `/projects` + `/projects/:key` + 批量归入（`devices.py:118`）；Plan/Run/Results 页 `project_key` 过滤，**结果经 `plan_run.project_id` 快照语义**（`results.py:161-179`） |
| 审计 | ⚠️ 部分 | create/assign/apply 全走 `record_audit`（`projects.py:310,395,408`）；**无 update/archive 路由 → facet 修改审计路径不存在** |
| D1/D4/D5/D7/D8/D9 挂起 | ✅ 符合 v2 决策 | `variables`/`storage_key`/`applicable` 均未建（`project.py:8` 注释明示） |
| P2 前置 on_subscribe 校验 | ✅ 已做 | `socketio_server.py:310-386`（白名单 + 实体存在性） |
| **写入断点** | ❌ | `plans.py:489` create_plan 不写 `project_id/specialty_id` → **新 Plan 恒 NULL**；生产实测 5 plan 中 1 个 NULL、99 plan_run 中 5 个 NULL 即此缺口所致 |
| P3 jira 自动带 key | ❌ | 仅详情页展示占位（`ProjectDetailPage.tsx:139`）；后端无任何 jira 提交集成 |

### 1.2 ADR-0030（多用例平台化管理）

| 阶段 | 状态 | 证据 |
|------|------|------|
| **P0** 脚本三件套 + validate + NFS 通道 | ✅ 满分 | `mtbf_setup` v1.3.0（suite_sha256 留痕 :135 + adb root 硬校验 v1.3.0）、`mtbf_check` v1.2.0（PROGRESS 真发射 `_lib.py:80-83` + `capabilities: ["progress_stamps"]`）、`mtbf_finish` v1.4.0（suite_sha256 闭环 :135-146 + NFS JSON 落盘）；真机验收 PlanRun #217/#218 |
| **P1a** 实体 + 13 端点 + 审计 + 渲染器 | ✅ 高质量 | `backend/models/suite.py`、`backend/api/routes/suites.py`（13 端点）、**JSON 非 JSONB 键序决策**（`suite.py` 注释 + 实证 wifiPWD/wifiName 互换）、`content_fingerprint` 结构性漂移检测（六条变更路径反例全翻转）、渲染**逐字节同构**（76791B 往返零容差） |
| **P1b** D2/D3b 绑定门禁 | ❌ **完全未做** | `inject_suite_params`、`suite_key` 绑定、run_context 冻结、precheck 五步门禁（missing/not_exported/content_changed/sha_mismatch/project_mismatch）、D3b 全零代码；脚本侧仍走 `STP_MTBF_EXPECTED_TESTPOINT_COUNT` env 预置（P0 方案，P1b 才删） |
| **P1c** CLI + 文档 + 状态传播 | ❌ | `tools/dev/mtbf-cases.py` 不存在；`mtbf-api.md` §2 仍是占位；`05-data-model.md` 未补；**ADR 头部仍标 Proposed**（P1a 已合入 main 4 天，2026-08-20 `e4cde10`） |
| **P2** 前端 + `test_case_result` | ❌ | 无页面、无表、`types.ts` 无 suite 类型 |
| 测试覆盖 | ✅ | `test_mtbf_suite_routes.py`（20）+ `test_mtbf_validate.py`（11）+ `test_mtbf_scripts.py`（25）+ `test_mtbf_suite.py`（32）；fixtures `backend/agent/tests/fixtures/mtbf/`（真实快照，`.gitattributes -text`） |

**关键结构问题：P1a（能管）超前于 P1b（能保证）**——管理面造出了产物，但没有任何门禁保证「派发用的是库里那份」，消费端仍是 P0 的「读磁盘文件 + env 预置」。这正是 P1 设计里 precheck 五步门禁要解决的事。

---

## 2. 生产库实测证据（2026-08-24 只读）

| 项 | 实测值 |
|----|--------|
| `alembic_version` | `t6u7v8w9x0y1`（两条 taxonomy 迁移已应用；head `u7v8w9x0y1z2` flash seed 未应用） |
| `test_project` | 6 SEED 行（HONOR-MLD/HONOR-ELA/ZTE-Z258/ODM-DAM/TRANSSION-X110/LEGACY）+ 2 USER 行（A57、V552AA） |
| `device.project_id` | 547 台全有，**NULL 归零**（M-c 完成标准达成） |
| `plan.project_id` | 5 行中 4 行有（**新 Plan 不写归属**） |
| `plan_run.project_id` | 99 行中 94 行有（同上） |
| `specialty` | 3 行（mtbf/power-cycle/monkey） |

---

## 3. 评分

### ADR-0029：8 / 10

- **决策质量 9**：v2 决策转向（APK 分化下沉到脚本路由）是近年最佳架构取舍——用一个「单入口 + 设备能力路由」既有先例（`backend=auto`），砍掉 D1/D4/D5/D7 四个机制化方案，把项目模型收敛为登记簿。facet vs 层级树论证（MLD/ELA 同客户同平台但 APK 不同）有生产证据支撑。粒度判据（族 = 项目）被 545 台全部落族内验证。
- **实现质量 8**：精确映射、NULL 归零、快照语义、审计、on_subscribe 前置——每个「容易偷懒」的点都做对了。
- **扣分**：① 写入断点（新 Plan 不写归属）；② `specialty` 半截（表有、读路径无）；③ P3 挂起无记录。

### ADR-0030：6.5 / 10

- **P0 + P1a 质量 9**：字节级保真渲染、结构性漂移检测（不做端点置空纪律）、`JSON` vs `JSONB` 键序论证、adb root 硬校验——执行层无可挑剔。
- **决策质量 7.5**：P0 先行、消费面不变的分阶段是对的；但「窄化复活 + 显式和解」把简单问题复杂化——`test_suite` 本质是 ADR-0029 备选 §3 拒掉的 ExecutionProfile 子集；且 `suite_key` 走 default_params 注入特例（第二个注入特例）而非推动挂起 D1 复议，机制叠加在累积。
- **扣分**：① P1b 缺失——「库改了没导出在此拦截」这一 ADR 核心价值没落地；② P1c 状态传播全滞后（Proposed 标着、P1a 已上线）——**自家 v2.3.1 教训（同一事实七个挂靠位）在自己身上复发**；③ 套件版本化推迟到「触发条件出现」，但相机 MTBF 的「APK↔项目严格对应 + 清单频繁变化」就是可预见的触发条件，至少该做「导出产物按次归档」低成本留痕。

---

## 4. 差距清单（按严重度排序）

| # | 差距 | 严重度 | 修复路径 |
|---|------|--------|----------|
| 1 | **P1b 套件绑定门禁缺失**（D2/D3b + run_context 冻结 + precheck 五步） | 高 | 实现 `inject_suite_params` + admission 挂载五步门禁（`suite_verify_failed` 同层）+ 脚本侧 expected 改注入 |
| 2 | **Plan 编辑器归属写入断点**（新 Plan 恒 NULL） | 高 | create_plan 写 `project_id` + `specialty_id`；Plan 表单加项目/专项选择 |
| 3 | **P1c 状态传播滞后**（ADR 头部、mtbf-api §2、05-data-model、CLAUDE.md 决策表） | 中 | 按 ADR-0030 修订记录 v1.1 ⑥ 的七挂靠位逐一同步 |
| 4 | specialty 读路径半截（无 API / 无前端下拉） | 中 | specialty 字典 API + Plan 编辑器下拉 |
| 5 | 项目 update/archive 路由缺失 → facet 审计路径不存在 | 中 | 补 PUT /archive + `record_audit` |
| 6 | 套件无导出归档（sha 只能归因、不能恢复） | 中 | export-to-tool-dir 时按 `exported_sha256` 归档副本 |
| 7 | P3 jira 自动带 key 未接 | 低 | 问题追踪页提交入口带出 `jira_project_key` |
| 8 | 项目拆分机制缺失（相机 MTBF 可能触发族内 APK 分裂） | 低 | 预留「从项目分裂出子项目」运维路径（新 USER 项目 + 设备/Plan 重归属） |

**补充论据（2026-08-24 吸收自 [`..._fcd9fe46.md`](./ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_fcd9fe46.md) 独立评审）**：

- **#1 升级为「第二套套件上线的硬前置」**：`STP_MTBF_EXPECTED_TESTPOINT_COUNT` 是 **fleet 单值旋钮**（唯一进 `_FLEET_ENV_KEYS` 白名单的 MTBF 键，全 fleet hot-update 同值；`STP_MTBF_TASK_TIMES` 等其余键为 host 级手工，见 `mtbf-api.md` §1.5）——相机 MTBF 等第二套套件随项目分化上线时，单值 expected 基准无法按项目区分，正确性悬在「所有 host 恰好同配置」这一随时会被打破的假设上。P1b 从「性价比最高的补课」升级为硬前置。
- **#2 的验收应为「迁移不变量」而非一次性回填**：M-c 完成标准「回填后 `device.project_id` 无 NULL」把回填当成**一次性事件**——只要派发链路不写快照，NULL 会持续再生，标准当天就被违反。正确验收：**不变量测试**——迁移窗口之后任何 `plan_run` 行 `project_id` 为 NULL 即 CI 失败，而不是靠一次人工核对宣布完成。

---

## 5. 再设计建议（若重新设计，与现状不同的五件事）

1. **P1 内部重排：绑定门禁（P1b）前置，管理面（P1a）后置。** 管理面的验收信号（「外部 agent 导入→改→导出→派发」）里「派发」恰是 P1b 的活；P1a 的 13 端点没有 P1b 的消费端 = 造了「能写不能保证」的仓库。先做注入 + 门禁（复用 ADR-0021 准入链），再放 CRUD。
2. **不叠第二个注入特例，直接复议 ADR-0029 挂起 D1。** `suite_key` 绑定是 `params_override`（挂起 D1）的教科书用例——派发期按 PlanStep 注入套件三字段 + 冻结快照。D1 复议触发条件（「路由表维护成本失控或出现计划级参数分化需求」）已半触发（相机 MTBF 清单分化 = 计划级参数分化）。复议掉 D1，让 `suite_key` 走通用覆盖层，一步到位，避免第三个特例。
3. **套件版本化不推迟：P1 就做「导出产物按次归档」。** 快照 sha 只能归因「被改了」、不能恢复「改之前」。APK 严格对应项目 + 相机套件频繁变化 ⇒「导出覆盖丢旧版」是必然事件。低成本：export-to-tool-dir 同时写 `{NFS}/mtbf/{suite}/{exported_sha256}/runtask.xml`（按 sha 命名天然去重），消费路径不变——版本化机制 20% 成本、80% 收益。
4. **ADR-0029 补「项目拆分」运维路径 + 接上 Plan 编辑器写入。** 族 = 项目在「族内 APK 不分裂」假设下成立，相机 MTBF 恰是「同族 APK 开始分裂」的形态；现状无拆分机制（SEED 不可映射、USER 拆分要逐台重建归属）。
5. **字节级保真的目标值得商榷（权衡而非错误）。** 0 容差渲染（CRLF、属性序、`&#64;` 写法）本质是对「设备端解析器脆弱性」的妥协。若全新设计，先测清 `OfflineScriptManager` 接受域（属性序是否真敏感、CRLF 是否必须），把「规范化渲染 + 一次转换」作目标——把设备端怪癖固化进 golden 测试即固化成平台契约。当前「脚本不可变 + 真机已验」约束下是合理选择。

---

## 6. 落地顺序建议

| 优先级 | 事项 | 对应差距 |
|--------|------|----------|
| **P0** | P1b 门禁（inject_suite_params + 五步门禁 + 脚本侧注入） | #1 |
| **P0** | Plan 编辑器归属写入（project_id + specialty_id） | #2 |
| P1 | 状态传播七挂靠位同步 + mtbf-api §2 定稿 | #3 |
| P1 | specialty 字典 API + 前端下拉 | #4 |
| P2 | 项目 update/archive + facet 审计 | #5 |
| P2 | 导出按 sha 归档 | #6 |
| P3 | jira 自动带 key | #7 |
| —（触发再议） | 项目拆分机制、套件正式版本化、D1 复议 | #8 |

---

## 7. 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-24 | 吸收 [`..._fcd9fe46.md`](./ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_fcd9fe46.md) 独到论据：fleet 单值旋钮正确性悬崖（#1 升级为第二套套件硬前置）+ 快照 = 迁移不变量验收（#2） |
| 2026-08-24 | 初版：ADR-0029/0030 落地现状核对（代码 + 生产库实测 + 双 agent 交叉验证）、评分 8 / 6.5、差距清单 8 项、再设计建议 5 条 |
