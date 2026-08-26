# AI-Native SDLC Playbook × stability-test-platform 治理实践对照审查（Claude Code · 第二轮）

- **状态**：Living（2026-08-26 初版；结论随治理实践演进修订）
- **日期**：2026-08-26
- **性质**：**外部方法论对照评审**（非 ADR、非 Agent Note）——Anthropic《The AI-Native SDLC Playbook》要点提炼 + 本项目 CI/CD 与 AI 治理实践逐维度对照 + 与既有两份同题评审的交叉比对与裁决建议
- **来源**：<https://claude.com/blog/the-ai-native-sdlc-playbook>（Anthropic Applied AI 团队；2026 年发布）
- **产出方**：Claude Code（第二轮会话）。首轮产出：[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md)；同题并行产出：[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md)、[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md)（本文写作时尚未落盘，故 §5 裁决范围仅三方，见 §6）、[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md)（同上）；总汇：[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md`](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md)
- **方法**：WebFetch 抓取原文全文提炼 → 通读 `.github/workflows/{ci,pr-agent,main-ci-backstop,enable-auto-merge}.yml`、`.githooks/pre-commit`、根 `CLAUDE.md` / `AGENTS.md` / `DOC-MAP.md` → 逐条复核既有两份评审的事实层断言（`.claude/` 目录实测仅 `settings.local.json`；workflows 四文件在盘；关键锚点 grep 复核：`ci.yml:152,160`、`pr-agent.yml:24-30`、`.githooks/pre-commit:10`）→ 未运行验证性实验（见 §8）
- **本文定位**：与首轮 claude-code、codebuddy 两份**并行且独立**的完整分析，非替代关系。事实层三方一致处不重复铺陈，重心放在：①独立裁决两份评审的分歧（§5）；②文档基础设施层面的新发现（§6）；③合并后的缺口清单与建议排序（§4/§7）

---

## 0. 结论摘要（TL;DR）

| 维度 | 文章主张 | 本项目 | 对齐度 |
|------|----------|--------|--------|
| 注意力集中在 gates | 核心命题 | **明文化「合入路径注意力预算 ~2min」**，全量 CI 移出 PR 路径 | ✅ 超出 |
| 事故→回归门禁 | 每次生产事故→一条 regression eval | 两次重大事故各转化为**确定性 CI 门禁**（空行污染三重防线、脚本版本不可变） | ✅ 超出*（形态是确定性脚本而非 LLM eval） |
| PR 评审循环 + 职责分离 | Claude 双向收发评审 | fail-closed security gate 但**单向（审而不修）** | 🟡 部分 |
| 工件链 / 审计轨迹 | intent→spec→plan→PR→incident | Issue→ADR→design→PR+Agent Note 强制归档 | ✅ 对齐 |
| CLAUDE.md 治理面 | 约一页，犯错两次即写入 | 体系完整但远超一页，靠分层加载补偿 | 🟡 部分 |
| **continuous evals（元治理）** | 治理面自身变更须过 eval 门禁 | **完全缺失** | ❌ 缺失（最大缺口） |
| hooks 强制护栏 | managed settings 兜底非协商规则 | pre-commit 手动启用 + CI 兜底；无 project-level Claude Code hooks | 🟡 部分 |
| Maintain 分层响应 + LLM 分诊 | bands.yaml + `claude -p` 自动分诊 | backstop 自愈闭环完整但止步于「通知」，无分诊 | 🟡 部分 |

**一句话结论**：本项目在文章 Stage 3–5（Build 护栏 / Test 事故转化 / Deploy 门禁）上不仅对齐、多处更严格（fail-closed、防绕过双保险、门禁与命令分离）；真正差距集中在「元治理」——**用来管 AI 的那些配置本身没有质量门禁（evals 缺失）**，以及 Maintain 阶段自动化止步于通知而未到分诊。**事实层与既有两份评审完全一致；分歧集中在演进优先级**——本文裁决：先拦截（evals/hooks）、后赋能（规划工件/skills），G6 规划工件判 P1 而非 P0（理由见 §5.2）。

---

## 1. 原文要点提炼

> 与两份并行评审 §1 可互校；三方提炼一致，此处不重复铺陈细节。

### 1.1 核心论点

代码不再是瓶颈。AI 压缩构建阶段后，瓶颈转移到构建两侧的流程（规划、评审/测试、部署门禁、治理），仍按人类速度运行；原控制措施（人工逐行评审）跟不上 agent 产出的 diff；治理成本上升。SDLC 需与实现阶段同等程度转型：线性流 → 闭环，人类判断保留在 gate。

### 1.2 核心机制：Committed Artifacts

每阶段结束时向版本控制提交一份工件，下一阶段从**读取**它开始：

`intent.md → spec.md → plan.md → diff+测试 → PR+评审发现 → 事故记录`

**提交链即审计线索**（谁要求了什么、agent 产出了什么、谁批准了什么）。早期 .md 工件是主导工件——产品负责人与 agent 读写同一份文件。

### 1.3 治理哲学（政策代码化四级）

CLAUDE.md（工作知识）→ **skills（咨询性控制）** → **hooks（确定性强制）** → managed settings（不可协商）。原文：「skill 使违规罕见，hook 使之几乎不可能」。

### 1.4 六阶段关键实践

| 阶段 | 关键做法 |
|------|----------|
| Plan | `intent.md` 用发起者自己的话捕捉意图，产品负责人审核后提交；非工程人员经 claude.ai/Cowork + 连接器代为提交，无需 git 经验 |
| Design | 需求与设计压缩为一次会话，由编码为 skills 的机构标准引导；写 spec 时**实时应用政策**并产出 flagged concerns（矛盾政策处先行解决） |
| Build | Plan mode 默认起点（产 plan.md → 拷问计划 → 批准后提交）；护栏成熟后转 auto mode + worktree 并行；CLAUDE.md「犯错两次就写入」且约一页内；subagents 封装重复工作；遗留系统 fact-of-source 三配置可选 |
| Test | 反馈回路（测试/构建/截图 diff 自验自修）；bug 先写失败测试，hook 保护测试文件不被 agent 弱化；**continuous evals**——20–50 条真实任务，CLAUDE.md/skills/hooks 变更时运行并作合并门禁；**每个生产事故转化为一条回归 eval** |
| Deploy | Claude 双向参与 PR 评审（REVIEW.md 定 pass 标准 / Important 与 Nit 界限，`@claude` 触发修复推送）；**职责分离——写码 agent 无权批准自己**；hooks 作批准门，非协商规则放个人不可关闭的托管设置；`claude -p` 非交互处理判断型任务；部署能力按 dev/staging/prod 经 MCP 分层授权 + 沙箱短期令牌；**回滚是最需演练的路径**；滞后指标 DORA |
| Maintain | 无人在调用路径上：确定性脚本监控指标，`bands.yaml` 分层响应（1σ 记录 / 2σ 只读诊断 / 3σ 只能开 PR 或跑预批准 runbook）；诊断写成 `intent.md` 重启闭环；Claude Tag 当 incident 第一响应者，「频道即审计轨迹」；复盘写入版本化 lessons 反哺 |

---

## 2. 对照基线：本项目 CI/CD 与 Agent 治理现状（证据清单，已复核）

| 实践 | 证据 |
|------|------|
| required checks 六项：lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests / pr-agent-gate | 根 AGENTS.md「Key conventions · PR 合入」 |
| 合入路径注意力预算 ~2min；超预算检查一律异步 | `docs/notes/process/2026-08-14-merge-path-attention-budget.md` |
| PR 轻量 / main 全量仅 workflow_dispatch / 每日 UTC 18:00 backstop 兜底 | `ci.yml`、`main-ci-backstop.yml` |
| pr-agent-gate fail-closed 四类条件（未完成/API 失败/输出缺失/security concerns）+ 门禁/命令 job 分离（防 gate 被顶掉） | `pr-agent.yml:24-30` |
| #421 双保险：gate failure 显式 disable-auto（真实事故转化） | `pr-agent.yml`（PR #399 被 auto-merge 漏判合入） |
| auto-merge + branch protection：approvals=0、enforce_admins、strict=true | 根 AGENTS.md「Key conventions · PR 合入」 |
| 空行污染三重防线（pre-commit + CI 阻塞门禁 + AST 语义校验工具） | `.githooks/pre-commit`、`ci.yml:152` |
| 脚本版本不可变门禁（2026-07-31 事故转化；含 `_` 辅助模块） | `ci.yml:160`、`tools/dev/check-script-version-immutability.py` |
| pre-commit 需手动启用（`git config core.hooksPath .githooks`），可绕过 | `.githooks/pre-commit:10` |
| 生产约束：`.env.backend` 唯一源 + 无兜底默认 + 禁生产库试跑 + testcontainers 隔离 | 根 AGENTS.md「生产机调试约束」、ADR-0024 production guard |
| **无 project-level Claude Code hooks/skills/agents 定义** | `.claude/` 目录实测仅 `settings.local.json`（2026-08-26 复核） |

---

## 3. 逐维度对照矩阵

对齐度图例：✅ 超出 / ✅ 对齐 / 🟡 部分 / ❌ 缺失

| # | 维度 | 文章主张 | 本项目现状 | 对齐度 | 关键差异 |
|---|------|----------|-----------|--------|----------|
| 1 | 人类注意力集中于 gates | 理念层倡导 | 量化为 ~2min 注意力预算并明文化取舍记录 | ✅超出 | 连「哪个检查值多少注意力」都有 note |
| 2 | 事故→回归防护 | 每次事故→一条 regression eval | 两次重大事故各转化为一条二值确定性 CI 门禁 | ✅超出* | 确定性脚本对机械污染/契约破坏类零误判，但对「语义类回归」无覆盖（见 #8） |
| 3 | PR 评审循环 | Claude 发出也接收评审；REVIEW.md 定 pass 标准；@claude 触发修复 | 单向：pr-agent-gate 只阻断/放行；non-security findings 无修复闭环 | 🟡部分 | 缺「修」的一侧；无 Important/Nit 界限定义 |
| 4 | 职责分离 | 写码 agent 无权批准自己 | gate 只做 security 判定不做 approve；approvals=0 + enforce_admins | ✅对齐 | 走得更激进：人的判断前置到 gate 设计层而非逐 PR 审批 |
| 5 | 防绕过加固 | 未展开 | fail-closed + 门禁/命令分离 + #421 disable-auto 双保险 | ✅超出 | 三条均来自真实事故复盘 |
| 6 | 工件链审计轨迹 | intent/spec/plan/review/incident 版本化工件 | Issue→ADR→design→PR+Agent Note（强制，含放弃的备选与重议条件）→事故 note | ✅对齐 | Agent Note 强制性高于文章的自愿性工件（机器可消费子面缺口见 G6） |
| 7 | CLAUDE.md 治理面 | 约一页，「犯错两次就写入」 | 体系完整但常驻部分数百行；分层加载（子系统懒加载）+ `.cursor/rules` 薄适配层补偿 | 🟡部分 | token 成本与注意力稀释是实价；分层是文章没有的缓解手段 |
| 8 | **continuous evals（元治理）** | 治理面变更须过 20–50 任务 eval 门禁 | **零**。改 CLAUDE.md/AGENTS.md/.cursor/rules/pr-agent 配置无任何评测守护 | ❌缺失 | **最大缺口**。真实先例：CLAUDE.md @import 写在中文行内静默失效只能人肉 /context 发现 |
| 9 | hooks 强制护栏分级 | skill=咨询性；非协商规则放个人不可关的托管设置 | pre-commit 可手动绕过（CI 兜底，反馈秒级→分钟级）；无 project-level hooks 保护高危路径（已发布脚本版本目录、`.env.backend`、`hosts.ini`） | 🟡部分 | 高危路径目前只有 ruff exclude + CI 兜底，编辑器直改时本地零拦截 |
| 10 | bug 先写失败测试 + 测试文件保护 | hook 阻止 agent 修改测试文件 | 无机制强制；「verify before asserting」仅为行为约定 | ❌缺失 | 低优先级：required checks 已能兜住大部分回归 |
| 11 | Maintain 分层响应 | bands.yaml（1σ/2σ/3σ 分级动作） | backstop 开 issue 一档制 | 🟡部分 | 产品侧两层钟（timeout 安全网 + stall 显式打开）即 banding 思想，未反哺自身 CI 治理 |
| 12 | LLM 构建失败分诊 | `claude -p` 非交互分诊判断型任务 | failure issue 开出后仍人肉读日志定位 | ❌缺失 | issue body 无红灯 job 摘要/疑似 commit 定位 |
| 13 | 部署分层授权 | dev/staging/prod 经 MCP 分层暴露 + 沙箱短期令牌 | 文档级禁区表 + 应用层 production guard | 🟡部分 | 单人+20 host 规模下成本收益存疑 |
| 14 | 回滚=演练最多的路径 | 明确要求定期演练 | 有 hot-update runbook，无演练机制 | ❌缺失 | Agent 滚动升级有文档，回滚路径未演练 |
| 15 | DORA 滞后指标 | 采用 DORA | 未采集 | ❌缺失 | backstop issue 数/PR 生命周期可低成本近似 |

---

## 4. 缺口合并清单（G1–G6，两版 + 本文三方合并）

| # | 缺口 | 提出方 | 影响 | 本文判定 |
|---|------|--------|------|----------|
| G1 | 治理面无 evals：CLAUDE.md/AGENTS.md/.cursor/rules/pr-agent 配置变更零回归防护 | 两版共识 | AI 门禁与 agent 行为契约的质量只能靠「下一次犯蠢」发现 | **P0**，无可争议 |
| G2 | AI 评审单向：findings 无修复推送闭环 | 两版共识 | non-security findings 依赖人工往返 | P1 |
| G3 | 高危路径无本地强制拦截：已发布脚本版本目录、凭据文件在编辑器/AI 直改时零拦截 | 两版共识 | 反馈延迟秒级→分钟级；AI 会话内可能先改坏再等 CI 拦 | P1 |
| G4 | backstop 失败 issue 无自动分诊 | 两版共识 | 每晚看门人启动成本高；failure issue 可能积压 | P2 |
| G5 | 回滚路径未演练；无 DORA 指标 | 两版共识 | 故障时回滚不是「演练最多的路径」；改进无度量 | P2 |
| G6 | 无机器可消费的规划工件（intent/plan.md）：工件全是给人读的决策档案（ADR/Agent Note），agent 无法直接接棒人类规划 | codebuddy 独有 | agent 只能从人类文档重新理解已完成的规划 | **P1（降级）**，理由见 §5.2 |

---

## 5. 与既有两份评审的交叉比对（本文核心价值）

### 5.1 共识（三方一致，高置信）

- **最大缺口 = continuous evals 缺失**：治理面自身变更无评测守护。
- Maintain 自动化止步于通知（未到分诊/闭环）；hooks 高危路径无本地拦截；评审单向无修复闭环。
- 已对齐且值得保持：CI 分层（PR 轻 / main 全量 / 夜间 backstop）、fail-closed 三细节（门禁/命令分离、#421 双保险、infra 错误与真实失败区分）、事故→门禁转化习惯、Agent Note 四要素模板。
- 事实层独立勘察一致：required checks 六项、fail-closed 四类条件、`.claude/` 无项目级 hooks/skills。

### 5.2 分歧裁决建议

| 议题 | claude-code 首轮 | codebuddy 版 | **本文裁决** | 理由 |
|------|-----------------|--------------|--------------|------|
| 工件链判定 | 整体 ✅ 对齐 | 拆出「机器可消费意图/plan 工件」子面判 ❌（G6） | **接受 G6 存在，严重度降为 P1** | 大型变更已有 design doc 流程承接规划；小型变更引入 plan.md 是每 PR 的文档负担；文章把 plan.md 定位在「护栏成熟后转 auto mode」阶段，本项目 hooks/evals 护栏尚未成熟——顺序上应排在拦截类之后 |
| 演进优先级 | 先拦截：evals→hooks→findings 闭环 | 先赋能：evals ∥ plan.md → skills 化 | **倾向首轮（先拦截）** | 拦截缺口的代价是**已实付**的（@import 中文行内失效事故只能人肉发现）；赋能类收益需护栏先建立才有安全前提（文章自己也要求「先护栏后加速」） |
| skills 化 Agent Notes | 未单列 | P1 明确建议「Agent Notes skills 化」 | **有条件支持**，但须沿用薄适配层哲学 | 项目已有 `.cursor/rules` 先例：权威内容留 CLAUDE.md/AGENTS.md、适配层只放指针。skills 化若复制全文会造成第三轨漂移面 |
| approvals=0 定性 | 有意取舍（✅） | 同意 + 补充「判断前置到 gate 后 gate 成唯一事实来源，而 gate 无 eval 守护」 | **同意 codebuddy 补充**，并视为 G1 的第二条理由 | 判断前置到 gate 设计层后，gate 自身可靠性成为单点依赖——这恰好强化 evals 的 P0 地位（守 gate 的 gate） |

### 5.3 各版遗漏互补

- **首轮 claude-code 独有、本文已并入**：前端 vitest/docker-build 不在合入路径的风险敞口定量评估；Dependabot 分组（auto-merge 组 vs 人工组）与文章供应链治理的对照；`.cursor/rules` 与 CLAUDE.md 双轨漂移风险量化。
- **codebuddy 独有、本文已并入**：G6 机器可消费工件子缺口；Agent Notes skills 化的具体路径；plan.md 作为「人工审 plan 替代逐行审代码」的注意力重定位手段。
- **本文独有**：①DOC-MAP 引用完整性发现（§6）；②gate 单点依赖论证（§5.2 第 4 行）把 codebuddy 的补充升级为 G1 的结构性理由；③对首轮 §7 利益声明的回应：首轮的「hooks/门禁优先」与「被治理 AI 扩大自主权」的自我利益方向**相反**，可信度更高——本文立场同向，同样受益于该检验。

---

## 6. 文档基础设施发现（本文独有）

1. **DOC-MAP.md 引用完整性**（发现于本文写作时；已闭环）：当时 `cursor` 条目指向尚未落盘的文件、codebuddy 已落盘却无条目——本文补齐后，cursor 与 composer 两稿相继落盘并完成登记。遗留的裁决范围局限（§5 未纳入这两稿增量）由 [总汇 Synthesis](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md) 回收。
2. **首轮 claude-code §关联评审「待补链接」占位**：本文已补 codebuddy 与本文链接。
3. 两份既有评审同日同题、事实层一致，为本文交叉比对提供了高可信基线——这是 ADR_0029_0030 系列四份并行评审模式的价值实证：**多产出方独立勘察一致的事实可以免复核采信，分歧显式化后只需在裁决点花注意力**。

---

## 7. 建议（P0/P1/P2，三方并入后本文排序；含验收标准）

| 优先级 | 建议 | 验收标准 | 来源 |
|--------|------|----------|------|
| **P0** | 最小 continuous evals：10–20 条「治理面文件状态 + 典型问题 → agent 应答符合契约」冒烟集，覆盖 @import 语法、表名单数、Pydantic v2、唯一 action 类型等高频契约；治理面文件变更时触发 | 治理面 PR 在无人工干预下被 eval 拦截一次故意注入的错误（如中文行内 @import） | 两版共识 |
| **P1** | project-level Claude Code hooks：deny 编辑 `backend/agent/scripts/*/v*/`、`.env.backend`、`hosts.ini`、`backend/.env` | hooks 生效后 AI 会话内直改上述路径被即时拒绝 | 两版共识 |
| **P1** | pr-agent findings 修复闭环：允许 AI 将 non-security findings 修复推至同分支（push 自动复评把关） | 一个含 nit 的 PR 由 AI 修复推送并通过复评合入 | 两版共识 |
| **P1** | 机器可消费规划工件：**大型变更**引入 `plan.md`（小型变更豁免）；人工审 plan 与结果 diff，替代逐行审代码 | 一次大型变更按 plan.md 执行，人工仅审 plan 与结果 diff | codebuddy 提出，本文降级采纳 |
| **P2** | backstop failure issue 附 `claude -p` 分诊段：红灯 job 清单 + 失败步骤日志摘录 + 疑似 commit | 新开 failure issue body 含上述三要素，零人在调用路径上 | 两版共识 |
| **P2** | DORA 近似指标：PR 生命周期 + change failure rate（backstop issue 频次近似） | 月度数字可在 GitHub API 一条查询内得出 | 两版共识 |
| **P2** | 回滚演练：hot-update runbook 加季度演练项 | 演练记录归档 docs/operations | 两版共识 |

---

## 8. 局限性与置信度

- **原文单源**：仅依据博客正文，未读其引用的平台团队落地文档清单；文中数字（20–50 条 eval 等）未经第二来源印证。
- **静态核验**：所有本项目证据来自文件通读 + 关键锚点 grep 复核（§2），未实际触发 workflows 验证行为；分支保护设置（approvals=0/enforce_admins）采信 AGENTS.md 记载，未调 GitHub API 复核。
- **视角偏差**：本文产出方本身是「被治理的 AI」。本文立场（先拦截后赋能、G6 降级）与「扩大 AI 自主权」的自我利益方向相反，可作为可信度加分项，但仍是主观判断，交叉分析时应质询。
- **规模前提**：文章面向大型企业治理；本项目的单人+20 host 规模下，部分 play（部署 MCP 分层、DORA 体系、20–50 条 eval 规模）性价比存疑，本文已在优先级与规模上体现该取舍。

---

## 9. 交叉分析指引（供最终总汇）

汇总三份（或四份，若 Cursor 版产出）同题分析时按以下核对点查缺补漏：

1. **事实层核对**（应各 agent 一致，不一致以 file:line 复核为准）：
   - required checks 是否恰为六项；全量 CI 是否确实不在 PR 路径；
   - pr-agent-gate 的 fail-closed 条件枚举是否完整（未完成/API 失败/输出缺失/security concerns 四类）；
   - `.claude/` 下是否存在项目级 hooks/skills（当前实测：否）。
2. **判断层分歧**（需显式裁决）：
   - G6 严重度：本文降级为 P1（先拦截后赋能）；codebuddy 判 P0。裁决点：是否已有足够护栏承接 auto mode 式规划工件；
   - evals 的最小可行规模与挂载位置（PR 阻塞 vs backstop 异步）；
   - skills 化与 `.cursor/rules` 薄适配层哲学是否冲突（双轨漂移风险）。
3. **本文独有、需并入总汇的**：§6 文档基础设施发现（DOC-MAP 断链 / codebuddy 缺条目 / cursor 占位）；§5.2 gate 单点依赖论证（G1 的第二条结构性理由）。
4. **总汇收尾动作**（已完成）：cursor/composer 相继落盘登记；各版文件头「关联评审」互指完整；canonical 缺口编号与剩余裁决收敛见 [Synthesis](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md)。
