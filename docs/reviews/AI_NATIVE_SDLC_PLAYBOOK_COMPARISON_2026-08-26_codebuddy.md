# AI-Native SDLC Playbook × stability-test-platform 治理实践对照评审（CodeBuddy 产出）

- **状态**：Living（2026-08-26 初版；结论随治理实践演进修订）
- **日期**：2026-08-26
- **性质**：**外部方法论对照评审**（非 ADR、非 Agent Note、**未定稿任何设计决策**）——Anthropic《The AI-Native SDLC Playbook》要点提炼 + 本项目 CI/CD 与 Agent 治理实践逐维度对照 + 缺口清单。本文**仅作交叉比对输入**，不产生/锁定设计决策。
- **来源**：<https://claude.com/blog/the-ai-native-sdlc-playbook>（Anthropic Applied AI 团队；Louis Claxton；2026-08-21）
- **产出方**：CodeBuddy 助手——文件名尾缀 `codebuddy`，与 [`..._claude-code.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md)（Claude Code 产出）同题并列，供交叉比对、查缺补漏
- **方法**：WebFetch 抓原文（原文站抓取受限，经两源交叉核对：腾讯新闻官方博客完整译文 + 觉醒AI深度解读）→ 通读 `.github/workflows/`、`.githooks/pre-commit`、`CLAUDE.md`/`AGENTS.md`、`docs/notes/README.md`、`backend/agent/` → 全仓搜索确认无 `SKILL.md`/`.claude/`/`intent.md`/eval。未运行验证性实验
- **同题关联**：[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md)（Claude Code 首轮）、[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md)（Cursor Agent / Auto）、[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md)（Cursor Composer）、[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md)（Claude Code 第二轮，三方交叉比对与裁决）、[总汇 Synthesis](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md)（canonical 编号 + 裁决固化；本文 G6 在其中为 C-G6）

---

## 0. 结论摘要（TL;DR）

本项目是**「人类主导 + AI 辅助」的成熟工程仓库**，治理成熟度属上游；但处于 **"Agent 增强传统 SDLC"** 阶段，尚未进入 **"AI 原生 SDLC"**：

- **已拥有 Playbook 的治理哲学**：判断力放关卡、职责分离、fail-closed、事故→门禁转化。
- **尚未拥有 Playbook 的机制层**：committed artifacts、skills、Claude hooks、持续 eval、Agent 主导闭环。

**与 claude-code 版最大共识点**：真正差距集中在「元治理」——**治理面（CLAUDE.md/AGENTS.md/pr-agent 配置）自身的变更没有质量门禁（evals 缺失）**，以及 Maintain 自动化止步于通知未到分诊。

**与 claude-code 版的主要分歧点**（见 §7）：
1. 演进优先级排序不同：claude-code 以「P0 evals / P1 hooks / P1 findings 闭环」为序；本文以「**先落可机器消费的规划工件（intent/plan），再 skills 化 Agent Notes**」为序，理由是项目文档已极其厚重，**缺的首先是"agent 可接力的意图载体"，而非"拦截手段"**。
2. 对「approvals=0 + auto-merge」的定性：claude-code 视为有意取舍（✅）；本文基本同意但提示一个 claude-code 未覆盖的风险面——**判断已前置到 gate 设计层后，gate 自身成了唯一事实来源，而 gate 无 eval 守护**（恰好回到最大缺口）。

---

## 1. 原文要点提炼（供对照基准，与 claude-code 版 §1 可互校）

### 1.1 核心论点
代码不再是瓶颈。AI 压缩构建阶段后，瓶颈转移到**构建两侧的流程**（规划、评审/测试、部署门禁、治理），仍按人类速度运行；原控制措施（人工逐行评审）跟不上 agent 产出的 diff；治理成本上升。

### 1.2 核心机制：Committed Artifacts（提交的产物）
每个阶段结束时向版本控制提交一个工件，下一阶段从**读取**该工件开始：
`intent.md → spec.md → plan.md → 代码 diff+测试 → PR+评审发现 → 事故记录`。
Markdown 为主，产品负责人与 agent 都可读可行动；**commit 链即审计链**。流程从线性改为循环。

### 1.3 治理三原则
- *"skill 使违规罕见，hook 使之几乎不可能"*——咨询性约束用 skill，确定性强制用 hook；
- *"闭环持续运行，人类判断凌驾其上"*——人从逐行盯代码转向在关卡看 agent 标记；
- 变革须兼顾大企业治理合规（分支保护、托管设置、职责分离）。

### 1.4 六阶段要点

| 阶段 | Playbook 做法 |
|------|---------------|
| Plan | 发起人向 Claude 描述 → 迭代收敛 → 生成 `intent.md` → 提交仓库；发起人以 merge 审查批准 |
| Design | 需求与设计压缩为一次会话，由 **skills** 引导；生成规格时即应用约束、产出 flagged concerns |
| Build | Plan Mode 生成 `plan.md` → Auto Mode 自动应用；`CLAUDE.md`、**skills**、**hooks** 护栏、并行 worktree + subagent、反馈回路（自验自修）、bug 先写失败测试 |
| Test | **持续 Eval**：20-50 真实任务写 eval，配置变更时运行；**每个生产事故写成 eval**；CI 以 eval 作门禁 |
| Deploy | 发布门禁表达为 **hook**；AI 双向参与 PR 审查（`REVIEW.md` 定 pass 标准）；职责分离；回滚一条命令 agent 可跑 |
| Maintain | 控制带闭环：确定性脚本监控 → 1σ 日志 / 2σ 只读诊断 / 3σ 只开 PR 或 runbook；诊断写回 `intent.md` 重启闭环；Claude tag 当事故第一响应者 |

---

## 2. 本项目现状（勘察依据 + 关键文件）

| 维度 | 现状 | 关键文件 |
|------|------|----------|
| 团队知识 | 根 `CLAUDE.md` + `AGENTS.md` + 各子目录 `CLAUDE.md` 分层完整 | `CLAUDE.md`、`AGENTS.md`、`backend/agent/CLAUDE.md` |
| 决策留痕 | Agent Notes 制度 + ADR | `docs/notes/README.md`、`docs/adr/` |
| CI 分层 | PR 轻量（lint/pr-typecheck/pr-compileall/pr-agent-tests/pr-backend-test）+ main 全量兜底 + 夜间 backstop | `.github/workflows/ci.yml`、`main-ci-backstop.yml` |
| AI 审查 | pr-agent-gate（PR-Agent + DeepSeek），fail-closed，#421 双保险，门禁/命令 job 分离 | `.github/workflows/pr-agent.yml` |
| 合并门禁 | Git 分支保护 6 项 required checks + enforce_admins + auto-merge | `enable-auto-merge.yml`、根 `AGENTS.md`「PR 合入」 |
| 提交期护栏 | `.githooks/pre-commit`（Git 钩子，需手动启用）+ CI 兜底 | `.githooks/pre-commit` |
| 事故→门禁 | 空行污染三重防线、脚本版本不可变门禁（两次真实事故转化） | `tools/dev/collapse-blank-pollution.py`、`check-script-version-immutability.py` |
| 生产约束 | `.env.backend` 唯一生产源 + 无兜底默认 + 生产库禁试跑/写操作走 PR | `AGENTS.md` §生产机调试约束 |

> **缺失项确认**（全仓搜索，2026-08-26）：无 `SKILL.md`、无 `.claude/` 项目级 hooks/skills、无 `intent.md`/`spec.md`/`plan.md` 类工件、无 eval/golden 回归集。

---

## 3. 逐维度对照矩阵（对齐度图例：✅ 超出 / ✅ 对齐 / 🟡 部分 / ❌ 缺失）

| # | 维度 | 文章主张 | 本项目现状 | 对齐度 | 关键差异 |
|---|------|----------|-----------|--------|----------|
| 1 | 注意力集中 gates | 理念层倡导 | 量化为「合入路径注意力预算 ~2min」并明文化 | ✅ 超出 | 连"哪个检查值多少注意力"都有取舍 note |
| 2 | 事故→回归防护 | 每次事故→一条 regression eval | 两次重大事故→各一条二值确定性 CI 门禁 | ✅ 超出* | 形态是确定性脚本非 LLM eval；机械污染类更可靠，语义类无覆盖 |
| 3 | PR 评审循环 | Claude 发出也接收评审，REVIEW.md 定标准，@claude 触发修复 | 单向：gate 只阻断/放行；non-security findings 无修复闭环 | 🟡 部分 | 缺"修"的一侧；无 Important/Nit 界限定义 |
| 4 | 职责分离 | 写码 agent 无权批准自己 | gate 只做 security 判定不做 approve；enforce_admins | ✅ 对齐 | 判断前置到 gate 设计层 |
| 5 | 防绕过加固 | 未展开 | fail-closed + 门禁/命令分离 + #421 双保险 | ✅ 超出 | 三条均来自真实事故 |
| 6 | 工件链审计轨迹 | intent/spec/plan/review/incident 版本化工件 | Issue→ADR→design→PR+Agent Note（强制）→事故 note | ✅ 对齐 | Agent Note 强制性高于文章自愿性工件；**但缺机器可消费的意图/计划工件** |
| 7 | CLAUDE.md 治理面 | 约一页，「犯错两次写入」 | 体系完整但远超一页，靠分层加载（子系统懒加载）补偿 | 🟡 部分 | 用分层缓解文章没提的 token/注意力成本 |
| 8 | **continuous evals（元治理）** | 治理面变更须过 eval 门禁 | **零**。改 CLAUDE.md/.cursor/pr-agent 配置无评测守护 | ❌ 缺失 | **最大缺口**（与 claude-code 共识） |
| 9 | hooks 强制护栏分级 | 非协商规则放个人不可关的托管设置 | pre-commit 可手动绕过（CI 兜底，反馈秒级→分钟级）；无 project-level hooks 保护高危路径 | 🟡 部分 | 高危路径本地直改时零拦截 |
| 10 | bug 先写失败测试+测试文件保护 | hook 阻止 agent 改测试文件 | 无机制强制；「verify before asserting」仅行为约定 | ❌ 缺失 | 低优先级，required checks 已兜住大部分 |
| 11 | Maintain 分层响应 | bands.yaml（1σ/2σ/3σ） | backstop 开 issue 一档制 | 🟡 部分 | 产品侧两层钟（timeout/stall）即 banding 思想，未反哺自身 CI |
| 12 | LLM 构建失败分诊 | `claude -p` 非交互分诊 | failure issue 开出后人肉读日志定位 | ❌ 缺失 | issue body 无红灯 job/疑似 commit 定位 |
| 13 | 部署分层授权 | dev/staging/prod 经 MCP + 沙箱令牌 | 文档级禁区表 + 应用层 production guard | 🟡 部分 | 单人+20 host 规模下性价比存疑 |
| 14 | 回滚=演练最多的路径 | 明确要求定期演练 | 有 hot-update runbook，无演练机制 | ❌ 缺失 | 回滚路径未演练 |
| 15 | DORA 滞后指标 | 采用 DORA | 未采集 | ❌ 缺失 | 可用 backstop issue 频次低成本近似 |

---

## 4. 已对齐且值得保持的实践（防止重构误伤）

1. **CI 分层结构**（PR 轻 / main 全量 / 夜间 backstop）：注意力预算的载体，任何"把全量搬回合入路径"的建议都应先过 attention-budget note 的取舍逻辑。
2. **fail-closed 三细节**：门禁/命令 job 分离、#421 双保险、#384 区分 infra 错误与真实失败。
3. **事故→门禁转化习惯**：空行污染检查必须保持在最前（pre-commit 注释记录了顺序错误坑）。
4. **Agent Note 四要素模板**（决定/放弃备选/如何验证/何时重议）：比文章工件链更可操作。
5. **文档只写现状不写变迁**原则：使 §2 证据清单可长期作对照基线。

---

## 5. 缺口清单（按严重度，与 claude-code 版 G1–G5 对照）

| # | 缺口 | 影响 | 现有缓解 |
|---|------|------|----------|
| G1 | 治理面无 evals：CLAUDE.md/AGENTS.md/.cursor/rules/pr-agent 配置变更零回归防护 | AI 门禁与 agent 行为契约质量只能靠"下一次犯蠢"发现 | 无 |
| G2 | AI 评审单向：findings 无修复推送闭环 | non-security findings 依赖人工往返 | gate 只卡 security，其余仅参考 |
| G3 | 高危路径无本地强制拦截：已发布脚本版本目录、凭据文件直改零拦截 | 反馈延迟秒级→分钟级；AI 会话内可能先改坏再等 CI | ruff exclude + CI 阻塞门禁 |
| G4 | backstop 失败 issue 无自动分诊 | 每晚看门人启动成本高；issue 可能积压 | 自愈闭环（恢复自动关） |
| G5 | 回滚路径未演练；无 DORA 指标 | 故障时回滚不是"演练最多的路径" | hot-update runbook 存在 |
| G6* | **无跨阶段机器可消费工件**（intent/plan/spec） | Agent 无法"接棒"人类已完成的规划，只能从人类文档重新理解；与 G1 同属"缺 agent 可消费的载体" | 无（*本文补充，claude-code 版列为"已对齐"的工件链中未单独拆出此子缺口） |

> **G6 与 claude-code 版的差异说明**：claude-code 版将「工件链」整体判为 ✅ 对齐（Issue→ADR→design→PR 闭环完整）。本文同意该链存在且质量高，但**额外拆出「机器可消费的意图/计划工件」这一子面**判为缺口——因为项目工件都是给人读的决策档案（ADR/Agent Note），缺少 agent 可直接作为行动起点的 `intent.md`/`plan.md`。这是本文在工件链判定上与 claude-code 版**最实质的分歧点**，供裁决。

---

## 6. 演进建议（按性价比排序；**注意：本文不锁定任何决策，仅列候选供裁决**）

| 优先级 | 建议 | 验收标准 | 备注 |
|--------|------|----------|------|
| **P0** | 最小 continuous evals：10-20 条「治理面文件状态 + 典型问题 → agent 应答符合契约」冒烟集，覆盖 CLAUDE.md import 语法、表名单数、Pydantic v2、唯一 action 类型等高频契约；治理面文件变更时触发 | 治理面 PR 在无人工干预下被 eval 拦截一次故意注入的错误（如中文行内 @import） | 与 claude-code 版 P0 一致 |
| **P0** | 引入轻量**机器可消费规划工件**（`plan.md` 起步）：agent 执行前先产 `plan.md` 提交，人工审 plan 而非逐行审代码 | 一次真实变更：agent 按 plan.md 执行，人工仅审 plan 与结果 diff | 本文独有优先级（claude-code 版未单列）；补的是"agent 可接力的意图载体" |
| **P1** | project-level Claude Code hooks：deny 编辑 `backend/agent/scripts/*/v*/`、`.env.backend`、`hosts.ini`、`backend/.env` | hooks 生效后 AI 会话内直改上述路径被即时拒绝 | 与 .githooks 互补非替代 |
| **P1** | 把 Agent Notes 中**可执行、可校验**的部分沉淀为 skills（随代码分发、可版本化），叙事留在 docs/notes | 一条既有约定（如表名单数）能作为可加载 skill 被 agent 主动调用 | 从 AGENTS.md「关键约定」切入最自然；claude-code 版未单列 |
| **P1** | pr-agent findings 修复闭环：允许 AI 将 non-security findings 修复推至同分支（push 自动复评把关） | 一个含 nit 的 PR 由 AI 修复推送并通过复评合入 | 与 CodeRabbit 卡门禁历史兼容 |
| **P2** | backstop failure issue 附 `claude -p` 分诊段：红灯 job 清单 + 日志摘录 + 疑似 commit | 新开 failure issue body 含三要素 | 零人在调用路径上 |
| **P2** | DORA 近似：PR 生命周期 + change failure rate（backstop issue 频次近似） | 月度数字 GitHub API 一条查询内得出 | 先度量再谈目标 |
| **P2** | 回滚演练：hot-update runbook 加季度演练项 | 演练记录归档 docs/operations | — |

> **与 claude-code 版的优先级分歧**：claude-code 版将「evals → hooks → findings 闭环」设为 P0/P1/P1 序；本文将 **P0 并列为「evals」+「规划工件」**，并在 P1 增加「Agent Notes skills 化」。理由：本项目文档已极其厚重（CLAUDE.md 数百行 + 大量 Agent Notes），**瓶颈已从"缺拦截"转移到"agent 无法消化既有规范并接力人类规划"**——先给 agent 一个可消费的意图载体（plan.md）+ 可执行的规范封装（skills），比再加一层拦截更能解锁吞吐。此分歧**不属于事实层**，属取舍偏好，需人工裁决。

---

## 7. 与 claude-code 版的交叉比对（查缺补漏）

> 本节是本文存在的主要价值——与 [`..._claude-code.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md) 对照，标记共识、分歧与各自遗漏。

### 7.1 共识（两版一致，可采信为高置信）
- **最大缺口 = continuous evals 缺失**（claude-code §0/§3 #8；本文 §3 #8）：治理面自身变更无评测守护。
- **Maintain 自动化止步于通知**（未到分诊/闭环）；**hooks 高危路径无本地拦截**；**评审单向无修复闭环**。
- **已对齐且值得保持**：CI 分层、fail-closed 三细节、事故→门禁转化、Agent Note 模板。
- **事实层**：required checks 六项、pr-agent-gate fail-closed 四类条件、无 `.claude/` 项目级 hooks/skills——两版独立勘察一致。

### 7.2 分歧（需人工裁决）
| 议题 | claude-code 版 | 本文 | 裁决点 |
|------|---------------|------|--------|
| 工件链判定 | 整体 ✅ 对齐 | 拆出「机器可消费意图/计划工件」子面判 ❌ 缺失（G6） | 是否需要 agent 可直接接力的 plan.md？ |
| 演进优先级 | evals → hooks → findings 闭环 | evals **并列**规划工件 → skills 化 → hooks → findings | 先加拦截 vs 先给 agent 可消费载体？ |
| skills 机制 | 未单列建议（仅提及文章主张） | 明确 P1 建议「Agent Notes skills 化」 | 是否值得把经验封装为可执行 skills？ |
| approvals=0 定性 | 有意取舍（✅） | 同意，但补充：判断前置到 gate 后 gate 无 eval 守护=单点依赖 | gate 自身可靠性是否需 eval 兜底？ |

### 7.3 两版各自可能遗漏（供并入）
- **claude-code 版可能未充分展开**：①「机器可消费工件」作为独立缺口；②「Agent Notes skills 化」的具体路径；③ plan.md 作为"人工审 plan 替代逐行审代码"的注意力重定位手段。
- **本文可能未充分展开（claude-code 版覆盖更好）**：① 前端 vitest/docker-build 不在合入路径的风险敞口定量评估；② Dependabot 分组（auto-merge 组 vs 人工组）与文章供应链治理章节的对照；③ `.cursor/rules` 与 CLAUDE.md 双轨漂移风险量化；④ 原文的 Plays 依赖图结构（claude-code §1.2 提及，本文未展开）。**这些建议从 claude-code 版补入。**

### 7.4 利益声明
本文产出方（CodeBuddy）本身是「被治理的 AI」，对「AI 应获得多大自主权」的判断存在利益相关，交叉分析时应重点质询——尤其 §6 的「规划工件 / skills 化」两条，本质是在扩大 agent 自主执行与规范吸收能力，与 claude-code 版「hooks/门禁优先」的保守倾向方向相反。

---

## 8. 局限性

- **原文单源**：原文站抓取受限，本文依赖腾讯新闻完整译文 + 觉醒AI解读交叉核对；文中数字（20-50 eval）未经第二来源印证。
- **静态核验**：所有本项目证据来自文件通读（file:line 见 §2），未实际触发 workflows 验证行为；分支保护设置采信 AGENTS.md 记载，未调 GitHub API 复核。
- **视角偏差**：见 §7.4。
- **规模前提**：文章面向大型企业治理；本项目单人+20 host 规模下部分 play（部署 MCP 分层、DORA 体系）性价比存疑，已在优先级体现。

---

## 9. 交叉分析指引（供汇总多份同题产出）

1. **事实层核对**（应各 agent 一致，不一致以 file:line 复核为准）：required checks 是否恰为六项；pr-agent-gate fail-closed 条件是否四类；`.claude/` 下有无项目级 hooks/skills（当前实测：否）。
2. **判断层分歧**（需显式裁决）：
   - 工件链是否需要「机器可消费的 intent/plan」这一子面；
   - evals 的最小规模与挂载位置（PR 阻塞 vs backstop 异步）；
   - 演进优先级：先拦截（hooks/evals）还是先赋能（plan.md/skills）。
3. **本文独有、需并入总汇的**：G6 机器可消费工件缺口；§6 P0「规划工件」与 P1「Agent Notes skills 化」建议。
4. **待并入 claude-code 版独有角度的**：见 §7.3。
