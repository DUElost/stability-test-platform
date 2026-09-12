# 文档地图（Documentation Map）

> **最后更新**：2026-09-06  
> **文档中心**：[`README.md`](./README.md)  
> **待删/归档清单**：[`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)

本页只保留三样常驻必需品：**阅读顺序**、**文档分层定义与登记簿**、**权威归属**。
逐文件的描述型索引（设计 / 开发运维 / PRD·验收）已迁往 [hub README](./README.md)，按需查阅。
冲突时以**代码与测试**为准。  
根目录 [`../README.md`](../README.md) 保持精简；环境变量、测试禁区、执行协议细则在子文档。

---

## 阅读顺序

### 新人 onboarding

```
../README.md → ../AGENTS.md → docs/README.md
    → design/00-system-overview.md
    → development/local-development.md
    → design/01-execution-pipeline.md
    → design/07-execution-protocol.md（状态机 / abort / claim）
```

### 新功能开发

```
prd/（或 Epic Issue）→ adr/ → design/
    → 代码 + 测试（见 development/testing.md）→ acceptance/
```

### 发版 / 运维

```
operations/README.md → production-minimum-deployment-checklist.md
    → operations/agent-version-and-hot-update.md（先升 Agent 再开版本门禁）
    → preprod-drill-runbook.md → acceptance/00-platform-smoke.md
```

---

## 文档分层

| 层级 | 位置 | 回答什么 |
|------|------|----------|
| **仓库首页** | [`../README.md`](../README.md) | 是什么、怎么跑起来、文档指针 |
| **需求 PRD** | [`prd/`](./prd/) | 做什么、成功标准、非目标 |
| **架构 ADR** | [`adr/`](./adr/) | 为什么这样定 |
| **技术设计** | [`design/`](./design/) | 模块、接口、数据流、**执行协议** |
| **验收** | [`acceptance/`](./acceptance/) | 可测通过标准 + 测试映射 |
| **开发** | [`development/`](./development/) | 本地环境、**env 表**、测试约定 |
| **运维** | [`operations/`](./operations/) + runbook | 部署、Agent 版本、联调、监控 |
| **共享启动契约** | [`../AGENTS.md`](../AGENTS.md) | 总原则、跨模块硬不变量、安全红线与按需入口 |
| **Claude 入口** | [`../CLAUDE.md`](../CLAUDE.md) | 导入共享契约并路由状态机与领域细节 |
| **全面审查指引** | [`reviews/PROJECT_REVIEW_PLAN.md`](./reviews/PROJECT_REVIEW_PLAN.md) | 全面只读审查总纲（R01–R15 范围与入口、逐轮基线与报告模板、跨区收口）；**指引而非审查结果**，总纲建立不代表任何区域已审查完成 |
| **Living 审查** | [`reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](./reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md) | 设备日志流转框架 + 缺陷/DoD/落地顺序（v3.0，阶段 0 ✅）；前一版快照 [`reviews/PROJECT_REVIEW_2026-08-09_previous.md`](./reviews/PROJECT_REVIEW_2026-08-09_previous.md) |
| **Living 审查** | [`reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md`](./reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md) | 多项目并存需求 + 生产数据基准 + 缺口核对 G1–G14 + 落地顺序（ADR-0029 背景分析） |
| **Living 审查** | [`reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md`](./reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) | MTBF 多用例平台化研究（runtask.xml 实测 + 平台缺口 G1–G5 + 候选形态 A/B/C + 设计草图；[ADR-0030](./adr/ADR-0030-multi-case-suite-management.md) 背景分析，Accepted v1.9） |
| **Living 审查** | [`reviews/ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_317ef8ab.md`](./reviews/ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_317ef8ab.md) | ADR-0029/0030 实现综合评审（路线图 78/48、再设计建议、差距 6 项；resume `317ef8ab`） |
| **Living 审查** | [`reviews/ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_245a4531.md`](./reviews/ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_245a4531.md) | ADR-0029/0030 实现综合评审（落地度 ≈85%/60%、评分 8/6.5、生产库实测 + file:line 证据；resume `245a4531`） |
| **Living 审查** | [`reviews/ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_unattributed.md`](./reviews/ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_unattributed.md) | ADR-0029/0030 实现独立评审（快照缺口 + 排期反转 + 绑定上移；来源未署名，原 `4a7c2d91` 占位已更正） |
| **Living 审查** | [`reviews/ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_fcd9fe46.md`](./reviews/ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_fcd9fe46.md) | ADR-0029/0030 实现综合评审（独立核验评分 7.5/6.5、快照不变量主张、fleet 单值旋钮正确性悬崖、`plan.suite_id` 双模式绑定；resume `fcd9fe46`） |
| **Living 审查** | [`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md`](./reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md) | Anthropic《AI-Native SDLC Playbook》× 本项目 CI/CD 与 AI 治理对照（15 维度矩阵 + 缺口 G1–G5 + P0/P1/P2 建议 + 多 agent 交叉分析指引；产出方 Claude Code） |
| **Living 审查** | [`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md`](./reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md) | 同上题并行评审（六阶段成熟度 + Plan/Design 产物链缺口 +「刻意不追」清单 + 交叉分析指引；产出方 Cursor Agent / Auto） |
| **Living 审查** | [`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md`](./reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md) | 同上题并行评审（原文双源交叉核对 + 独有 G6 机器可消费工件缺口 + §7 与 claude-code 版交叉比对；产出方 CodeBuddy） |
| **Living 审查** | [`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md`](./reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md) | 同上题第二轮评审（三方交叉比对与裁决：G6 降级 P1、先拦截后赋能、gate 单点依赖论证 + DOC-MAP 引用完整性发现；产出方 Claude Code 第二轮） |
| **Living 审查** | [`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md`](./reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md) | 同上题并行评审（六阶段成熟度 + 三层治理剖面 §4 + G1–G6 主题并表指引 + 与 claude-code-2「先拦截后赋能」对齐；产出方 Cursor Composer） |
| **Living 审查** | [`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md`](./reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md) | 同上题五稿总汇（canonical C-G1–C-G7 编号映射 + 裁决固化 D1–D5 + 「刻意不追」正式采纳 + 最终行动清单 P0–P2 + 独立性折扣声明；产出方 Claude Code 综合评判轮） |
| **Living 审查** | [`reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md`](./reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md) | automation-toolkit 合入 × 平台优化七方向可行性（展锐拆两步：P2 汇总服务化先行、P1 采集 Agent 化必开 ADR 重议 #220；Jira 定位管道复用；缺口 G1–G24 + 落地顺序 + 各项 ADR 触发条件；toolkit 主张经 gh REST 对照远端复核；跟踪载体 = GitHub Projects 看板） |
| **Living 审查** | [`reviews/FRONTEND_NAV_IA_REDESIGN_2026-08-28.md`](./reviews/FRONTEND_NAV_IA_REDESIGN_2026-08-28.md) | 前端导航与布局 IA 治理方案（P1–P7 现状审计：admin 入口散落三层三处/僵尸路由/分组频次混列/页签三实现/命名不一；方案 A 保守档=平台管理组收拢+执行组重排+僵尸路由清理+页头命名对齐；v1.2/v1.3 二次演进定稿=**三级频率分层**〔一级高频常驻/二级中频折叠组/三级低频收角落（更多功能组+UserMenu 下拉）〕+默认折叠与活跃组自动展开；AI 助手 pinned 入口设计；四项开放问题已裁决〔Q2 被 v1.2 覆盖、admin 单一来源被 v1.3 撤销〕） |
| **Living 审查** | [`reviews/REVIEW_FRONTEND_NAV_IA_2026-08-28.md`](./reviews/REVIEW_FRONTEND_NAV_IA_2026-08-28.md) | 前端导航 IA 方案只读审核（P1–P7 逐条代码核验属实；四项裁决=方案 A/项目保持独立一级/pinned v1 抽屉留 v2/页头改「脚本库」；补充 HostsPage.test 断言破坏点 230/238/247） |
| **Living 审查** | [`reviews/AI_ASSISTANT_PLAN_2026-08-27.md`](./reviews/AI_ASSISTANT_PLAN_2026-08-27.md) | 平台 AI 助手实施计划（ADR-0031 配套：两阶段路线 / 四表模型 / 26 工具清单 / 14 端点 / 测试矩阵 / 部署步骤；v1.3 采纳可行性分析风险 #8=T1 统一 action+续轮） |
| **Living 审查** | [`reviews/REVIEW_ADR0031_AI_ASSISTANT_2026-08-28.md`](./reviews/REVIEW_ADR0031_AI_ASSISTANT_2026-08-28.md) | ADR-0031 只读审核（H1=RunConsole 透传生产 DATABASE_URL 须 AGENT_TEST_ENV 显式覆盖等 6 项已采纳修订） |
| **Living 审查** | [`reviews/FEASIBILITY_ANALYSIS_AI_ASSISTANT_2026-08-28.md`](./reviews/FEASIBILITY_ANALYSIS_AI_ASSISTANT_2026-08-28.md) | AI 助手可行性分析（需求×基座映射全「高」；风险 #8 SAQ timeout×T1 长任务已转化为设计约束） |
| **Living 审查** | [`reviews/FRONTEND_UI_REVIEW_2026-08-19.md`](./reviews/FRONTEND_UI_REVIEW_2026-08-19.md) | 前端界面视觉与布局审查（A 轨源码取证 + B 轨目视取证 + 第三方复核轮） |
| **Living 审查** | [`reviews/FRONTEND_UI_REVIEW_2026-08-27.md`](./reviews/FRONTEND_UI_REVIEW_2026-08-27.md) | 前端各界面全量审查（功能 / UX / a11y / 错误处理；复核轮第三方裁决） |
| **Living 审查** | [`reviews/FRONTEND_ARCHITECTURE_REVIEW_AND_ROADMAP_2026-09-01.md`](./reviews/FRONTEND_ARCHITECTURE_REVIEW_AND_ROADMAP_2026-09-01.md) | 前端架构全面只读审查与未来演进规划（健康基准/分层架构/设计令牌/通信与同步/主工作台剖析/四维演进路线） |
| **历史审查** | [`reviews/RESIDENT_CONTEXT_AUDIT_2026-08-27.md`](./reviews/RESIDENT_CONTEXT_AUDIT_2026-08-27.md) | 治理面常驻上下文首轮评估；其常驻细节保留裁决已由 2026-09-05 Harness 基线整理取代 |
| **Living 审查** | [`reviews/PLATFORM_HEALTH_REVIEW_2026-09-03.md`](./reviews/PLATFORM_HEALTH_REVIEW_2026-09-03.md) | 平台全面只读健康审查报告（核心执行健康；梳理 3 个 A 级实缺陷与 ADR-0033 在途核验；登记下一阶段 6 维风险台账） |
| **Living 审查** | [`reviews/SCRIPT_VERSION_BLOAT_ENDGAME_FEASIBILITY_2026-09-10.md`](./reviews/SCRIPT_VERSION_BLOAT_ENDGAME_FEASIBILITY_2026-09-10.md) | 脚本版本膨胀终态可行性（#735 长效机制）：复制冗余 78% 量化 + 四条路径裁决（P2 共享库不做、P1「不可变收窄至零引用退役」是唯一需新 ADR 项、P3 归 ADR-0033 D3 轨道） |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fdca41.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fdca41.md) | 审查覆盖与修复有效性审计（R01–R15 之后的元审查；供多 Harness 综合审查）：覆盖缺口（跨区收口未启动/覆盖证据链与总纲漂移/无主候选面）+ 修复质量量化（182 PR，修复类 84% 带测试）+ 闭环脆弱点（FIFO 单车道 #1246、审批边界 #1293、清理边界 #1294）+ 待裁决项 CA-D01–CA-D07；**v1.1 附录 B**：止血盘点与组合治理（7 簇矩阵 + ADR-0017/ADR-0008 增补与新立队列协议候选，CA-D08–CA-D12） |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_b77c27-verification.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_b77c27-verification.md) | R01–R15 独立核验（同题第二意见，`IV-*` 编号，与 `CA-*` 稿并表汇聚）：R 区之外 44 条存量缺陷池（30 条零 R 关联）+ 夜间全量验证网连续两晚 failure 且不阻断合入 + 同交付物 4 条在窗 Execution 并行实证 + 对平行稿的更正（IV-X01/X02）+ 待裁决项 IV-D* |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_b77c27-confirmation.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_b77c27-confirmation.md) | 三问确认稿（第三意见，`CF-*` 编号，与 `CA-*`/`IV-*` 并表汇聚）：横切系统性断言代码级独立复核（redis 无 socket 超时/`_locks` 无淘汰/零 jitter/时钟混用/无客户端幂等——全实证）+ 9-PR 逐单测试抽样（9/9 带专项测试、人工身份、当日关单）+ backstop 红灯修复「pending 而非 verified」声明 + open issue 吞吐边际更新（243→237）+ 三稿计数口径差异说明；**§4 追加（Q4）**：止血/组合治理核验（#1028 四版本复制实锤、ruff 无 BLE/TRY、ADR-0008/0017 零覆盖、半套 ADR 链、26 治理包 issue 全 OPEN、CA/IV「吞异常是否新 ADR」载体分歧标记）；无新增待裁决项 |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_4e188e.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_4e188e.md) | 三问确认稿（Cursor `4e188e`；#1349 入库）：R01–R15 覆盖、已关闭修复与多 Harness 模式的独立核验与吞吐数据 |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_e16d6d.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_e16d6d.md) | 四问确认稿（Cursor `e16d6d`；#1349 入库，追加止血/组合治理）：退役稿引用已按「已退役」标注（断链修复见 PR #1385，去向见退役稿转正记录） |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_7d4f85-convergence.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_7d4f85-convergence.md) | **三问多源汇聚裁决**（**dsh `7d4f85`**；#1349 入库；2026-09-11 归属更正）：五稿去重与分歧裁定——Q1 裁定 IV 正确、CA 否定论被证伪；Q2 裁定可持续条件未满足；Q3 五稿一致；**当前唯一裁决层** |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_7d4f85-confirmation.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_7d4f85-confirmation.md) | 三问确认稿（dsh `7d4f85`，与汇聚稿同源会话；2026-09-11 入库）：R01–R15 之外覆盖面 / 已关闭修复有效性 / 多 Harness 模式 |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_85d793.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_85d793.md) | 退役稿转正（Codex `85d793`，独立审查）：修复有效性与组合治理——含 #901/#1123 已关闭修复的隔离反例（refresh 原子消费未前提化、取消路径互斥提前释放）、#942 契约漂移、通知幂等边界；10 个候选治理主题 + ADR 判定；建议反例转回归测试 |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_705379-synthesis.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_705379-synthesis.md) | **历史草案（恢复入库）**：R01–R15 综合裁决（会话 `705379`；原 `PROJECT_REVIEW_R01_R15_SYNTHESIS_2026-09-11.md` 在清理中丢失，恢复自 03:20:08 完整快照）；已被汇聚稿取代，留档供追溯 |
| **Living 审查** | [`reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fdca41-audit.md`](./reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fdca41-audit.md) | **有效审查文档集审计（元审查）**：9 份文档代码级抽验/引用完整性/合规/可信度分级 + 6 条处置建议；含归属更正（汇聚稿与确认稿=dsh `7d4f85`）与计数口径警示 |
| **Living 审查** | [`reviews/PLATFORM_AUDIT_2026-09-11.md`](./reviews/PLATFORM_AUDIT_2026-09-11.md) | 平台整体只读审查（四维度并行：架构 6/10·代码质量 7.5/10·测试与 CI 8/10·需求与文档；总分 6.8/10，初基线 `ca0665c4`，合入前复检基线 `dfbeb2ef`）：**2 Critical**（同步连接池未配容量参数 / RunConsole 进程内单例未登记为扩展阻塞点）+ **9 High**（心跳超时默认值多处重复定义、`services→api.routes` 反向依赖、God-module 路由、NFS 原始日志零 TTL、HddSpill 腾退不足、`merge_task` 全失败无终态、ADR-0031 ID 冲突、ADR 状态标注碎片化、PR 门禁不覆盖控制面测试、`action_templates` 死代码）；**风险台账 R-01~R-09 复核：完整修复 0 条 / 部分缓解 2 条 / 仍存在 7 条**；**合入前逐条复检：11 条仍成立 / C-02·H-01·H-04 部分或形态变化 / H-09 已由上游 `56234cb3` 修复**；前序基线见 `PLATFORM_HEALTH_REVIEW_2026-09-03.md`（本轮为其后 815 提交复检） |
| **Living 审查** | [`reviews/PLATFORM_AUDIT_2026-09-11-SCHEDULING.md`](./reviews/PLATFORM_AUDIT_2026-09-11-SCHEDULING.md) | 上述审查批次的**处置进度盘点与剩余项排期**（实查 2026-09-12）：13 条发现中 **6 条已关闭**（C-01、C-02、H-05、H-06、H-07b、H-09）、**5 条修复在途**（H-01 #1593、H-02 #1616、H-04 #1596、H-07 #1589）、**2 条未认领**（H-03 建议暂缓至 #1616 分层门禁落地后重评；H-08 属决策项，需 owner 裁定是否引入 Merge Queue）；含逐条实查状态表 + 三轨道排期建议 + 验收清单 |
| **实现规格** | [`reviews/IMPLEMENTATION_SPEC_PROMPT.md`](./reviews/IMPLEMENTATION_SPEC_PROMPT.md) | 阶段 3 重构实现规格——Agent 工作提示词（产出 device-log-event implementation spec） |
| **设计** | [`design/2026-08-27-platform-ai-assistant.md`](./design/2026-08-27-platform-ai-assistant.md) | 平台 AI 助手设计（组件职责/轮次时序/动作状态机/权限隔离矩阵/安全边界/部署观测；ADR-0031 配套） |
| **架构 ADR** | [`adr/ADR-0033-tool-kit-ecosystem-integration.md`](./adr/ADR-0033-tool-kit-ecosystem-integration.md) | 外部工具统一接入契约规范与包管理解耦模型（三层宿主/Tool Contract 退出码分层/Manifest 发布格式×DB catalog 唯一权威/防腐适配器/与 ADR-0032 权威分家；D0·D3 权威即刻生效、D2 按族准入、包存储条件落地、legacy 例外登记；**落地状态：未落地**；Accepted v1.2；#745） |
| **架构 ADR** | [`adr/ADR-0034-multi-harness-execution-contract.md`](./adr/ADR-0034-multi-harness-execution-contract.md) | 多 Harness 并行执行契约与执行登记（选择权原则/三维状态模型/Registry 非调度器/overlap 真值表/drift gate 非 merge queue/G2 真身+薄壳/Role=元数据+扩展点（v1.7 收敛，v1.8 缺省归一化+再开启条件）/并发上限反转——移除 ≈2-3、守对象重锚在窗 Execution 规模与 reconcile 负载（v1.9）/附录 A 增补 dsh web 实测——根级基线✅+scoped 触碰动态✅、静态 patch disabled 与运行时矛盾（v1.10）/dsh web 转正回填——Registry CLI 全周期 dogfood 通过 #1256→PR #1291、0.1.5 复测一致（v1.11）/CodeBuddy CLI/IDE 分立——附录 A 原单行实为 CLI 结论、2026-09-11 人工补测 IDE 得 Q1=否/Q2=是/Q3=一次（Zcode 同形态，与 CLI 相反）、照 Cursor 先例拆两行并校正 CLI 版本 2.149.0、补 IDE 版本 4.11.3（v1.12）；**Accepted v1.12**；两轮八源评审综合见 `reviews/REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`） |
| **架构 ADR** | [`adr/ADR-0035-agent-host-identity.md`](./adr/ADR-0035-agent-host-identity.md) | Agent 主机身份与凭据体系（决策四段化：§3 当前状态=接受共享 AGENT_SECRET+威胁模型与冒充面收窄 / §4 目标形态=A 每主机凭据 / §5 迁移路径=C 注册质询+实施骨架 / §6 升级触发条件+§6.1 检测来源（信号来源/检出方/命中后第一步）；四方案对比；ADR Accepted ≠ 实施已启动；两份竞争提案合并为单一权威；R02-R01/#906；**Accepted v1.2**） |
| **架构 ADR** | [`adr/ADR-0036-notification-delivery-semantics.md`](./adr/ADR-0036-notification-delivery-semantics.md) | 通知投递语义契约（How delivery behaves：`ACCEPTED`=渠道接受请求≠DELIVERED/三态失败/强制 deadline/SAQ 唯一重试 owner+投递级幂等成对/at-least-once+每通道去重键/投递事实落 DB/同步仅限连通性测试；协议码与 retry 参数不入正文；挂起送达回执与入站契约；**Accepted v1.0（2026-09-11 定稿）**；与 ADR-0011 分工 What vs How；R11 #1117/#1120/#1122） |
| **执行契约** | [`development/ai/execution-contract.md`](./development/ai/execution-contract.md)（+[规范附录](./development/ai/execution-contract-annex.md)） | AI Execution Contract 唯一权威源（Registry 协议/三维状态与 transition table/scope 谓词与 overlap 判据/test_impact/字段封闭性/实现与契约先后纪律；**Living v1.12**：契约分层——细则迁附录、正文预算收紧（v1.12）/ §2.1 命令清单以 `--help` 为准 + §3.3 `update --all`（v1.11）/ integration 缓存失效 `landed` + 僵尸候选两类对齐 + derived 归属前提（v1.10）/ 僵尸候选 `closed-unmerged`（v1.9）/ 决策实体唯一性 + 决策类必须 `--issue`（v1.8）/ 并发上限反转（v1.7）；附录承载写入协议细则、drift 豁免清单、已满足的启动判据与过渡条款、v1.1–v1.8 明细，与正文同版本演进、冲突以正文为准；ADR-0034 P0a 交付） |
| **设计** | [`design/2026-09-external-tools-integration-and-package-architecture.md`](./design/2026-09-external-tools-integration-and-package-architecture.md) | 外部工具统一接入架构与包管理实施计划（ADR-0033 配套：协议定义与 §2.5 双轨衔接/§3.3 注册流与门禁分工/NFS 布局/Agent 缓存/去重+专项适配器/三阶段排期） |
| **Sprint 快照** | [`archive/sprints/`](./archive/sprints/) | 已归档一次性任务单 |
| **跟踪** | GitHub Issues | 进行中、审查结论 |

---

## 权威 vs 归档

- **权威**：本树 `design/` · `development/` · `operations/` · `adr/` · `prd/` · `acceptance/`，及根 `AGENTS.md` / `CLAUDE.md` 摘要  
- **全面审查指引**：`reviews/PROJECT_REVIEW_PLAN.md`（分区导航与覆盖清单；不是审查结果）
- **Living 审查**：`reviews/` 其余文档（缺陷/DoD/落地顺序与轮次报告；不替代 `design/`）
- **归档**：`archive/`（不新增规范）  
- **过时处理**：见 [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)
