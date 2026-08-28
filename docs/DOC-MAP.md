# 文档地图（Documentation Map）

> **最后更新**：2026-08-27  
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
| **不变量摘要** | [`../CLAUDE.md`](../CLAUDE.md) | 架构不变量、关键约定、状态机摘要 |
| **Living 审查** | [`reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md`](./reviews/DEVICE_LOG_FLOW_REVIEW_2026-08-09.md) | 设备日志流转框架 + 缺陷/DoD/落地顺序（v3.0，阶段 0 ✅）；前一版快照 [`reviews/PROJECT_REVIEW_2026-08-09_previous.md`](./reviews/PROJECT_REVIEW_2026-08-09_previous.md) |
| **Living 审查** | [`reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md`](./reviews/PROJECT_TAXONOMY_REVIEW_2026-08-18.md) | 多项目并存需求 + 生产数据基准 + 缺口核对 G1–G14 + 落地顺序（ADR-0029 背景分析） |
| **Living 审查** | [`reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md`](./reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md) | MTBF 多用例平台化研究（runtask.xml 实测 + 平台缺口 G1–G5 + 候选形态 A/B/C + 设计草图；[ADR-0030](./adr/ADR-0030-multi-case-suite-management.md) 背景分析，Accepted v1.8） |
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
| **Living 审查** | [`reviews/FRONTEND_NAV_IA_REDESIGN_2026-08-28.md`](./reviews/FRONTEND_NAV_IA_REDESIGN_2026-08-28.md) | 前端导航与布局 IA 治理方案（P1–P7 现状审计：admin 入口散落三层三处/僵尸路由/分组频次混列/页签三实现/命名不一；方案 A 保守档=平台管理组收拢+执行组重排+僵尸路由清理+页头命名对齐；AI 助手 pinned 入口设计；四项开放问题已裁决；PR1→PR2 切分） |
| **Living 审查** | [`reviews/REVIEW_FRONTEND_NAV_IA_2026-08-28.md`](./reviews/REVIEW_FRONTEND_NAV_IA_2026-08-28.md) | 前端导航 IA 方案只读审核（P1–P7 逐条代码核验属实；四项裁决=方案 A/项目保持独立一级/pinned v1 抽屉留 v2/页头改「脚本库」；补充 HostsPage.test 断言破坏点 230/238/247） |
| **Sprint 快照** | [`archive/sprints/`](./archive/sprints/) | 已归档一次性任务单 |
| **跟踪** | GitHub Issues | 进行中、审查结论 |

---

---

---

---

## 权威 vs 归档

- **权威**：本树 `design/` · `development/` · `operations/` · `adr/` · `prd/` · `acceptance/`，及根 `AGENTS.md` / `CLAUDE.md` 摘要  
- **Living 审查**：`reviews/`（缺陷/DoD/落地顺序；不替代 `design/`）  
- **归档**：`archive/`（不新增规范）  
- **过时处理**：见 [`DOC-RETIREMENT.md`](./DOC-RETIREMENT.md)
