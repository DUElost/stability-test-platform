# AI-Native SDLC Playbook × stability-test-platform 治理实践对照审查

- **状态**：Living（2026-08-26 初版；结论随治理实践演进修订）
- **日期**：2026-08-26
- **性质**：**外部方法论对照评审**（非 ADR、非 Agent Note）——Anthropic《The AI-Native SDLC Playbook》要点提炼 + 本项目 CI/CD 与 AI 治理实践按六阶段对照 + 缺口与性价比建议
- **来源**：<https://claude.com/blog/the-ai-native-sdlc-playbook>（Anthropic Applied AI；Louis Claxton；2026-08-21）
- **产出方**：Cursor Agent（Composer / Auto）——文件名尾缀 `cursor`，供与其他 agent 同题分析文档并列比对
- **方法**：WebFetch 抓取原文全文 → 通读根 `CLAUDE.md` / `AGENTS.md`、`docs/notes/process/`（注意力预算、pr-agent-gate、#421）、`docs/development/cursor-rules.md`、`.github/workflows` 与 `.githooks` 摘要、`docs/notes/README.md` Agent Note 约定 → 按六阶段成熟度对照。未调 GitHub API 复核分支保护；未跑 workflow 冒烟
- **关联评审**：[同题 · Claude Code 首轮](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md)（file:line 证据基线）、[同题 · Composer](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md)（同族会话、文本高重叠，共识计数不叠加）、[同题 · CodeBuddy](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md)（原文双源核对 + G6）、[同题 · Claude Code 第二轮](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md)（三方交叉裁决）、[总汇 Synthesis](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md)（canonical 编号 + 裁决固化）

---

## 0. 结论摘要（TL;DR）

| 阶段 | Playbook 要求 | STP 成熟度 | 一句话 |
|------|---------------|------------|--------|
| Plan | `intent.md` 机读交接 | 低 | 有 design/ADR/issue，无统一意图流水线 |
| Design | `spec.md` + skills 约束会话 | 中低 | 设计文档强；无「合入 intent → 自动出 spec」 |
| Build | `plan.md` + CLAUDE.md + skills + hooks | 中高 | 机构知识文件化很强；缺强制 plan 与 agent 动作钩子 |
| Test | session 自证 + **agent 配置 continuous evals** | 中 | 代码测/分层 CI 强；**治理面零 eval** |
| Deploy | AI 审 + 门禁 + 分层自治 | **高** | PR-Agent + 注意力预算 + auto-merge 双保险；比文章更敢合入 |
| Maintain | 破带 → `intent.md` 闭环 | 低–中 | backstop/事故 note 有；无无头分诊闭环 |

**一句话结论**：Playbook 描述「全阶段产物闭环 + 监管级 agent 围栏」；本项目在 **Deploy/CI 与机构知识入仓** 上已是强 AI-native（甚至更激进：`approvals=0` + ~2min 注意力预算），弱项集中在 **Plan/Design 机读交接、agent 配置 eval、运行时 hooks、运维无头闭环**——正好对应文章「build 变快后瓶颈往左右挪」的那几段。

与 [claude-code 同题稿](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md) 的共识核心一致（注意力预算、fail-closed gate、evals 为最大缺口）；本稿更强调 **六阶段产物链缺口** 与 **单人规模下「刻意不追」清单**。

---

## 1. 原文内容摘要

### 1.1 核心论点

代码生产已不是瓶颈。Agent 把 Build 压到小时级后：

1. 瓶颈移到 Plan / Review·Test / Deploy（仍按人速）；
2. 「人写才值得逐行审」的控制假设失效；
3. 例外仍走会议/会签时，治理成本相对 agent 产出上升。

### 1.2 方法论骨架

- **线性 → 闭环**：每阶段提交下一阶段可消费的产物；提交链即审计轨迹。
  `intent.md → spec.md → plan.md → diff+测试 → PR+评审发现 → 事故记录`。
- **Plays**：六阶段可选择性采纳的战术单元（变更 / 入门 / 步骤 / 治理 / 度量），有依赖图。
- **人的位置**：注意力集中在 **gates**（审 agent 标出的内容），判断力决策仍由人负责；写码 agent 无权批准自己。

### 1.3 六阶段关键实践（压缩）

| 阶段 | 关键做法 |
|------|----------|
| Plan | 发起者口语 → `intent.md`；PO 审后入仓 |
| Design | 同会话压缩需求+设计为 `spec.md`；skills 注入品牌/安全/合规；flagged concerns 先解 |
| Build | plan mode 默认；`plan.md` 入仓后再写码；`CLAUDE.md` 约一页、「错两次就写入」；skills 咨询性、**hooks 确定性**；worktree 并行 + subagents |
| Test | session 反馈环自验；bug 先失败测试 + hook 护测试文件；**CI continuous evals** 护 CLAUDE.md/skills/hooks 变更 |
| Deploy | AI 双向审 PR（`REVIEW.md`）；hooks 作审批门；`claude -p` 判读型流水线任务；prod 门前人授权；回滚须演练 |
| Maintain | 确定性破带 → 分层唤起 Claude → 写 `intent.md` 再进环；Claude Tag 等通道同构 |

传统↔AI-native 对照表与度量指标见原文；本文不复述模板示例。

---

## 2. 对照基线：本项目 CI/CD 与 Agent 治理（摘要）

| 实践 | 落点（权威记载） |
|------|------------------|
| required checks：lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests / **pr-agent-gate** | `AGENTS.md` Key conventions · PR 合入 |
| 合入路径注意力预算 ~2min；超预算一律异步 | `docs/notes/process/2026-08-14-merge-path-attention-budget.md` |
| PR 轻量 / 全量仅 workflow_dispatch；每日 UTC 18:00 `main-ci-backstop` | `ci.yml`、`main-ci-backstop.yml` |
| `pr-backend-test` 信息性、非阻塞 | 同上 attention-budget 补充（#281 P2） |
| pr-agent-gate：fail-closed；**仅 security concerns 否决**（B-lite）；命令 job 与门禁分离 | `docs/notes/process/2026-08-21-replace-coderabbit-with-pr-agent-gate.md`、`pr-agent.yml` |
| #421：gate failure 显式 `disable-auto-merge` | `docs/notes/process/2026-08-25-pr-agent-gate-automerge-bypass.md` |
| auto-merge；`approvals=0`；`enforce_admins`；strict | `AGENTS.md`；`enable-auto-merge.yml` |
| 空行污染三重防线；脚本版本不可变 CI | `.githooks/pre-commit`、`ci.yml`、`tools/dev/check-script-version-immutability.py` |
| Agent Note 强制（Decision/Alternatives/Verification/Revisit） | `docs/notes/README.md`；`AGENTS.md` Agent Notes |
| 机构知识：`CLAUDE.md` + `AGENTS.md` + `.cursor/rules` 薄适配 | `docs/development/cursor-rules.md` |
| 生产机调试约束、ADR-0024 production guard | `AGENTS.md`、`CLAUDE.md` |
| 无项目级 Claude Code hooks/skills/agents；无治理面 eval 套件；无 DORA 采集 | 目录与文档实测（与 claude-code 稿 §2 一致） |

---

## 3. 六阶段对照

对齐度：✅ 超出 / ✅ 对齐 / 🟡 部分 / ❌ 缺失

### 3.1 Plan / Design

| 维度 | 文章 | 本项目 | 对齐度 |
|------|------|--------|--------|
| 意图机读产物 | `intent.md` | Issue / 口头 / design 起草；无 `intent/` 流水线 | ❌ |
| 规格机读产物 | `spec.md` + skills 约束 | `docs/design/` + ADR；无「合入 intent 触发 spec」 | 🟡 |
| 策略注入时机 | 写 spec 时读 skills | 策略在 CLAUDE/ADR/Note；靠会话加载，非 Design 阶段自动化 | 🟡 |

**判断**：工程平台已有强设计与方向级产物，但**缺产品流水线式的意图→规格机读交接**；单人仓库不必照搬 PO 角色，但若要多 agent 接力，仍缺统一入口工件。

### 3.2 Build

| 维度 | 文章 | 本项目 | 对齐度 |
|------|------|--------|--------|
| 先 plan 再写码 | `plan.md` 入仓 | 常用 plan/ask；**不强制**入仓 | 🟡 |
| CLAUDE.md | ~1 页；错两次写入 | 体系完整、偏长；子系统懒加载 + `.cursor/rules` 补偿 | 🟡 |
| Skills | `.claude/skills/` 版本化 | 项目以 rules/globs 为主；用户侧 skills 非仓内制度 | 🟡 |
| Hooks（agent 动作） | PreToolUse 拦保护区/凭证 | **无**项目级 agent hooks；有 **git** pre-commit + CI 确定性门 | 🟡 |
| 并行 / subagents | worktree + `.claude/agents/` | 工具能力具备；无仓内标准 verifier agent 定义 | 🟡 |

**判断**：机构知识「文件化」是强项；缺口是 **强制 plan 产物** 与 **agent 执行时硬拦**（文章：skill 劝导 + hook 兜底）。

### 3.3 Test

| 维度 | 文章 | 本项目 | 对齐度 |
|------|------|--------|--------|
| Session 反馈环 | 单命令自证；完成前贴输出 | Agent 测快；verification order 成文；生产禁连 `stp` 跑全量 | ✅ |
| 护测试文件 | hook 禁改 | 仅约定 | ❌ |
| **Continuous evals** | 护 CLAUDE.md/skills/hooks | **零** | ❌ |

**判断**：代码侧反馈环成熟；**对「驾驭 agent 的配置」无回归门禁**——与 claude-code 稿 G1 同评为最大缺口。

### 3.4 Deploy（本项目最强层）

| 维度 | 文章 | 本项目 | 对齐度 |
|------|------|--------|--------|
| AI PR 审 | 双向；`REVIEW.md` | 单向 gate（审而不修）；B-lite 仅 security 阻断 | 🟡 |
| 职责分离 | 写码者不能自批 | gate 不 approve；合入信任机器门禁 | ✅（更激进） |
| 合入速度 | 人审仍在 critical path | **注意力预算显式压合入路径**；全量夜间 | ✅ 超出 |
| 防平台漏判 | 未展开同等深度 | #421 disable-auto；门禁/命令分离 | ✅ 超出 |
| 供应链人闸 | managed marketplace 等 | Dependabot major / `github_actions` 人工审 | ✅ |
| Code owner 必审 | 文章仍建议 | **`approvals=0`** | 有意分歧 |

**判断**：Deploy/CI 是 STP 的 AI-native 主阵地。`approvals=0` 不是疏忽，而是与注意力预算同构的取舍——人审前置到 gate **设计**，不占用每个 PR 的同步块。交叉分析时应显式裁决「领先 vs 过度自动化」（见 §8）。

### 3.5 Maintain

| 维度 | 文章 | 本项目 | 对齐度 |
|------|------|--------|--------|
| 破带检测 | 确定性 bands | backstop 失败开 issue、恢复关；事故 → 确定性 CI 门 | 🟡 / ✅* |
| LLM 分诊 / 写 intent | 2σ/3σ 唤起 Claude | 止于通知；人手读日志 | ❌ |
| 回滚演练 / DORA | 明确要求 | hot-update runbook 有；演练与 DORA 无 | ❌ |

\*事故→**确定性**门禁（空行污染、脚本不可变）在机械类风险上可视为「超出」文章的 LLM eval 形态；语义类回归仍无覆盖。

### 3.6 审计产物：Agent Note ≈ 半套 playbook 链

| Playbook 链 | STP 对应 |
|-------------|---------|
| intent / spec / plan | 弱/分散（issue、design、会话 plan） |
| 方向决策 | ADR |
| 非平凡取舍 | **Agent Note**（强制四节） |
| 合入证据 | PR + required checks |
| 事故 | `docs/notes/`、`docs/operations/incident-*` |

Agent Note 的强制性高于文章「自愿工件」；粒度是**决策与运维**，不是「每个变更一条 intent→plan」。

---

## 4. 已对齐且宜保持（防重构误伤）

1. **CI 分层 + 注意力预算**：任何「全量搬回 PR 必查 / Merge Queue」须先过 `2026-08-14-merge-path-attention-budget.md`。
2. **fail-closed 三细节**：门禁与命令分离、#421 双保险、infra vs 真实失败区分——事故驱动，删任一会重开已知洞。
3. **事故→确定性门禁习惯**：空行污染「文件整体空行率」须保持在最前检查位。
4. **Agent Note 四要素 + 文档只写现状**：对照基线可长期复用。
5. **CLAUDE.md / rules 单一事实源 + 薄适配**：改全局先改 AGENTS/CLAUDE，勿在 rules 复制整篇。

---

## 5. 缺口清单（按本稿优先级）

| # | 缺口 | 影响 | 现有缓解 |
|---|------|------|----------|
| G1 | 治理面无 continuous evals | agent 契约/门禁质量靠「下次犯蠢」发现 | 无 |
| G2 | Plan/Design 无机读交接物 | 多 agent / 跨会话接力易丢意图；审计靠事后 note | design/ADR/Note 分散补位 |
| G3 | 无项目级 agent hooks | 已发布脚本目录、凭据路径可被会话先改坏，等 CI | ruff exclude + CI；git hook 需手动启用 |
| G4 | AI 审单向（无修闭环） | non-security findings 耗合入路径外注意力 | B-lite 仅卡 security |
| G5 | Maintain 无分诊 / 无 intent 回流 | backstop issue 启动成本高；不进 SDLC 环 | 自愈关 issue；事故人工写 note |
| G6 | 强制 `plan.md` / 护测试文件 / 回滚演练 / DORA | 各为中低；合起来抬高无人值守成本 | 约定与 runbook |

编号与 [claude-code 稿 G1–G5](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md#5-缺口清单按严重度) **不完全同构**：本稿把 **Plan/Design 产物链** 单列为 G2；claude-code 稿把高危路径本地拦截与评审单向拆得更细。汇总时勿直接按号合并，按主题并表。

---

## 6. 建议（按性价比，含「刻意不追」）

### 6.1 值得小步补（不破坏 ~2min 预算）

| 优先级 | 建议 | 验收直觉 |
|--------|------|----------|
| **P0** | 治理面最小 evals（10–20 条契约冒烟：表名单数、Pydantic v2、唯一 action、`script:`、CLAUDE import 陷阱等）；仅在 `CLAUDE.md` / `.cursor/rules` / pr-agent 配置变更时触发，或挂 backstop | 故意注入的契约错误能被拦一次 |
| **P1** | 高风险路径 **确定性** 拦网（agent hook 或等价本地脚本）：已发布 `scripts/*/v*/`、`.env.backend`、凭据类路径 | 会话内直改即时拒绝；CI 仍保留 |
| **P1** | 非平凡 PR 的 plan 意图写入 **Agent Note Decision**（或 PR 正文固定小节），不强制新开 `plan.md` 仪式 | 抽查 5 个 feature PR 可定位「改哪些文件/风险/如何证」 |
| **P2** | backstop failure 附机械摘要（红灯 job + 日志摘录）；有余力再加 `claude -p` 分诊 | issue body 含三要素即可人工加速 |
| **P2** | DORA 近似（PR 周期 + backstop 失败频次）与 hot-update 回滚演练记录 | 月度可查；演练进 `docs/operations` |

### 6.2 刻意不追（与当前治理同构）

| 不追项 | 理由 |
|--------|------|
| 强制 code owner 审批每个 PR | 直接打穿注意力预算；人审已在 gate 设计与供应链例外路径 |
| 完整 `intent/` 产品组织流程 | 当前是工程平台 + 单人主路径，非多角色产品流水线 |
| 无头 Maintain「告警→intent→修」全自动 | 真机/生产库约束下风险高；宜保持确定性检测 + 人开环 |
| 企业级 managed settings / MCP 部署分层全套 | 规模与数据分级未到；文档级禁区 + ADR-0024 更划算 |

---

## 7. 局限性与置信度

- **原文单源**：仅博客正文；未读其文末平台文档清单。
- **静态对照**：分支保护细节采信 `AGENTS.md`，未用 `gh api` 复核；workflow 行为未实测触发。
- **视角**：产出方为 Cursor 侧 agent，对「仓内 Cursor rules vs Claude Code hooks」权重可能偏高；交叉时应与 claude-code 稿对质。
- **规模**：文章面向大型企业；本项目单人 + ~20 host，§6.2 的「不追」是显式前提而非遗漏。

---

## 8. 交叉分析指引（与其他同题产出汇总）

汇总时建议：

1. **事实层**（应一致；冲突以 file:line / `gh` 复核为准）  
   - required checks 是否恰六项；全量是否不在 PR 路径；  
   - pr-agent-gate fail-closed 条件枚举；B-lite 是否仅 security；  
   - 项目级 `.claude/hooks|skills|agents` 是否仍为空。

2. **判断层**（预期分歧，需人裁决）  
   - `approvals=0` + auto-merge：领先还是过度？（本稿：有意取舍；claude-code 稿立场接近）  
   - CLAUDE.md：瘦身 vs 分层已够？  
   - evals：最小条数与挂载（PR 阻塞 vs backstop）  
   - **Plan/Design 机读产物**是否值得立项（本稿抬高；claude-code 稿更侧重元治理与 hooks）

3. **本稿可能偏弱、他稿更强处**  
   - file:line 证据密度、G 编号与 P0 验收标准的可执行性 → 见 claude-code 稿 §2/§5/§6  
   - Dependabot 分组与供应链章节对照 → claude-code §8.3 已点名待补

4. **他稿可能偏弱、本稿补充处**  
   - 六阶段成熟度总表与「刻意不追」清单  
   - Agent Note ≈ 半套产物链的定位  
   - 强制 `plan.md` 仪式 vs 塞进 Note/PR 正文的轻量替代

5. **利益声明**：见 §7；汇总文档应并列两稿利益声明，避免单方「AI 应获更多自主权」主张未经质询进入实施。
