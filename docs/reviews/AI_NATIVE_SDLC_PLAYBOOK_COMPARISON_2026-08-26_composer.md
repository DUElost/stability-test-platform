# AI-Native SDLC Playbook × stability-test-platform 治理实践对照审查

- **状态**：Living（2026-08-26 初版；结论随治理实践演进修订）
- **日期**：2026-08-26
- **性质**：**外部方法论对照评审**（非 ADR、非 Agent Note）——Anthropic《The AI-Native SDLC Playbook》要点提炼 + 本项目 CI/CD 与 Agent 治理实践按六阶段对照 + 缺口与性价比建议
- **来源**：<https://claude.com/blog/the-ai-native-sdlc-playbook>（Anthropic Applied AI；Louis Claxton；2026-08-21）
- **产出方**：Cursor Agent（Composer）——文件名尾缀 `composer`，供与其他 agent 同题分析文档并列比对
- **方法**：WebFetch 抓取原文全文 → 通读根 `CLAUDE.md` / `AGENTS.md`、`docs/notes/process/`（注意力预算、pr-agent-gate、#421）、`docs/development/cursor-rules.md`、`.github/workflows/{ci,pr-agent,main-ci-backstop,enable-auto-merge}.yml`、`.githooks/pre-commit` 摘要、`docs/notes/README.md` Agent Note 约定 → 按六阶段成熟度对照 + 三层治理（CLAUDE/skills/hooks）剖面。未调 GitHub API 复核分支保护；未跑 workflow 冒烟
- **关联评审**：[同题 · Claude Code](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md)（15 维度矩阵 + G1–G5 + file:line 证据最密）、[同题 · Cursor](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md)（六阶段 +「刻意不追」；同族另一 Cursor 会话，共识计数不叠加）、[同题 · CodeBuddy](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md)（原文双源核对 + G6）、[同题 · Claude Code 第二轮](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md)（三方交叉比对与裁决）、[总汇 Synthesis](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md)（canonical 编号映射 + 裁决固化）

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

**一句话结论**：Playbook 描述「全阶段产物闭环 + 监管级 agent 围栏」；本项目在 **Deploy/CI 与机构知识入仓** 上已是强 AI-native（甚至更激进：`approvals=0` + ~2min 注意力预算），弱项集中在 **Plan/Design 机读交接、agent 配置 eval、运行时 hooks、运维无头闭环**——与文章「Build 变快后瓶颈往左右挪」的论断一致。

与 [claude-code 稿](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md) 的共识：**元治理 evals 缺失（G1）为最大缺口**；与 [claude-code-2 稿](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md) 的共识：**先拦截（evals/hooks）、后赋能（规划工件）** 的演进序更稳妥。

---

## 1. 原文内容摘要

### 1.1 核心论点

代码生产已不是瓶颈。Agent 把 Build 压到小时级后：

1. 瓶颈移到 Plan、Review/Test、Deploy（仍按人速）；
2. 「人写才值得逐行审」的控制假设失效；
3. 例外仍走会议/会签时，治理成本相对 agent 产出上升。

安全评审队列是典型例子：安全团队按人速编制，agent 倍增产出后，要么队列堆积，要么带病合入——regulated 组织不能接受，政策检查须与 agent 同速。

### 1.2 方法论骨架

- **线性 → 闭环**：每阶段提交下一阶段可消费的产物；提交链即审计轨迹。
  `intent.md → spec.md → plan.md → diff+测试 → PR+评审发现 → 事故记录`。
- **Plays**：六阶段非线性战术单元（变更 / 入门 / 步骤 / 治理 / 度量），有依赖图；Clay play 无前置，其余按箭头采纳。
- **人的位置**：注意力集中在 **gates**（审 agent 标出的内容）；判断力决策仍由人；**写码 agent 无权批准自己**。
- **理想终态**：accepted artifact 自动触发下一阶段（`intent.md` merge → Design pass；`spec.md` → plan mode；merged PR → pipeline；生产破带 → 新 `intent.md`）。

### 1.3 治理哲学：四级政策代码化

| 层 | 角色 | 强制性 |
|----|------|--------|
| `CLAUDE.md` | 工作知识（命令、惯例、常错点） | 会话加载 |
| **skills** | 机构政策（品牌/安全/合规） | **咨询性**——提高合规概率 |
| **hooks** | PreToolUse 等 | **确定性**——拦路径、护测试、格式化 |
| managed settings | 平台/IT 托管 | **不可协商**——个人无法关闭 |

原文：「skill 使违规罕见，hook 使之几乎不可能。」

### 1.4 六阶段关键实践（压缩）

| 阶段 | 关键做法 |
|------|----------|
| Plan | 发起者口语 → brainstorm → `intent.md`；PO 审后入仓；非工程师可经连接器提交 |
| Design | 单会话读 `intent.md` + skills → `spec.md`；flagged concerns 先解 |
| Build | plan mode 默认；`plan.md` 入仓后再写码；CLAUDE.md ~1 页、「错两次写入」；worktree 并行 + subagents；遗留系统须声明 source-of-truth |
| Test | session 反馈环自验；bug 先失败测试 + hook 护测试文件；**continuous evals**（20–50 任务）护 CLAUDE.md/skills/hooks；事故→回归 eval |
| Deploy | AI **双向**审 PR（`REVIEW.md`）；hooks 作审批门；`claude -p` 判读型流水线；prod 门前人授权；**回滚须演练**；DORA |
| Maintain | 确定性破带 → `bands.yaml` 分层 → 诊断写 `intent.md`；Claude Tag 等同构通道 |

---

## 2. 对照基线：本项目 CI/CD 与 Agent 治理（证据清单）

| 实践 | 证据 |
|------|------|
| required checks 六项：lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests / **pr-agent-gate** | 根 `AGENTS.md`「Key conventions · PR 合入」 |
| 合入路径注意力预算 ~2min；超预算检查一律异步 | `docs/notes/process/2026-08-14-merge-path-attention-budget.md` |
| PR 轻量 / 全量仅 `workflow_dispatch`；每日 UTC 18:00 `main-ci-backstop` | `ci.yml:6-9`、`main-ci-backstop.yml` |
| `pr-backend-test` 信息性、非阻塞 | attention-budget 补充（#281 P2）；`ci.yml` PR 路径不跑全量 backend-test |
| pr-agent-gate：fail-closed；**仅 security concerns 否决**（B-lite）；门禁 job 与 `/review` 命令 job **分离** | `pr-agent.yml:21-31,57-90,109-134` |
| #421 双保险：gate failure 显式 `disable-auto-merge` | `pr-agent.yml:92-107`；`docs/notes/process/2026-08-25-pr-agent-gate-automerge-bypass.md` |
| auto-merge；`approvals=0`；`enforce_admins`；`strict=true` | `AGENTS.md`；`enable-auto-merge.yml` |
| 空行污染三重防线；脚本版本不可变 CI | `.githooks/pre-commit`、`ci.yml`、`tools/dev/check-script-version-immutability.py` |
| Agent Note 强制（Decision/Alternatives/Verification/Revisit） | `docs/notes/README.md`；`AGENTS.md` Agent Notes |
| 机构知识：`CLAUDE.md` + `AGENTS.md` + `.cursor/rules` 薄适配 | `docs/development/cursor-rules.md` |
| 生产机调试约束、ADR-0024 production guard | `AGENTS.md`、`CLAUDE.md` |
| **无**仓内 `.claude/skills`、`.claude/hooks`、`.claude/agents`；**无**治理面 eval 套件 | 目录与文档实测（与 claude-code 稿 §2 一致） |

`pr-agent-gate` fail-closed 与门禁/命令分离（防 security-gate bypass）：

```21:31:.github/workflows/pr-agent.yml
  pr-agent-gate:
    name: pr-agent-gate
    # required check（替代原 code-rabbit-gate），只在 pull_request 事件上跑：
    # - review 对当前 head 完成且无 security concerns → success；
    # - 有 security concerns → failure 阻断；
    # - review 未完成 / 工具或 API 失败 / 输出缺失 → failure 阻断
    #   （fail-closed；逃生：修复后 push 自动复评，或对失败检查点 rerun，
    #   或临时从分支保护摘除该 required check）。
    # issue_comment 命令（/review 等）由独立非门禁 job 处理，避免评论触发的
    # 成功 check run 顶掉/覆盖门禁（security-gate bypass）。
```

---

## 3. 六阶段对照

对齐度：✅ 超出 / ✅ 对齐 / 🟡 部分 / ❌ 缺失

### 3.1 Plan / Design

| 维度 | 文章 | 本项目 | 对齐度 |
|------|------|--------|--------|
| 意图机读产物 | `intent.md` | Issue / 口头 / design 起草；无 `intent/` 流水线 | ❌ |
| 规格机读产物 | `spec.md` + skills 约束 | `docs/design/` + ADR；无「合入 intent 触发 spec」 | 🟡 |
| 策略注入时机 | 写 spec 时读 skills | 策略在 CLAUDE/ADR/Note；靠会话加载，非 Design 阶段自动化 | 🟡 |

**判断**：工程平台设计文档与 ADR 强，但**缺产品流水线式意图→规格机读交接**；单人仓库不必照搬 PO 角色，多 agent 接力仍缺统一入口工件（与 [codebuddy 稿 G6](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md) 同向）。

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

**判断**：代码侧反馈环成熟；**对「驾驭 agent 的配置」无回归门禁**——与 claude-code 稿 G1 同评为最大缺口。已有 **事故→确定性门禁**（空行污染、脚本 sha 不可变）覆盖机械类风险，不覆盖语义契约。

### 3.4 Deploy（本项目最强层）

| 维度 | 文章 | 本项目 | 对齐度 |
|------|------|--------|--------|
| AI PR 审 | 双向；`REVIEW.md` | 单向 gate（审而不修）；B-lite 仅 security 阻断 | 🟡 |
| 职责分离 | 写码者不能自批 | gate 不 approve；合入信任机器门禁 | ✅（更激进） |
| 合入速度 | 人审仍在 critical path | **注意力预算显式压合入路径**；全量夜间 | ✅ 超出 |
| 防平台漏判 | 未展开同等深度 | #421 disable-auto；门禁/命令分离 | ✅ 超出 |
| 供应链人闸 | managed marketplace 等 | Dependabot major / `github_actions` 人工审 | ✅ |
| Code owner 必审 | 文章仍建议 | **`approvals=0`** | 有意分歧 |

注意力预算核心取舍：

```6:13:docs/notes/process/2026-08-14-merge-path-attention-budget.md
## Decision

单人项目的第一资源是注意力。CI/流程设计的第一约束：**合并路径上的阻塞
检查保持 ~2 分钟内**（当前 required checks 为 lint / CodeQL / pr-typecheck
/ pr-compileall / pr-agent-tests，全绿即 auto-merge；`code-rabbit-gate`
同为 required check，由 merge-gate 写状态但语义是 best-effort——仅当
CodeRabbit 对当前 head 给出终态决策时构成阻断）；任何引入等待或分心
（要惦记、要切回来看结果）的检查一律放异步路径（夜间批量全量 CI 兜底）。
```

**判断**：`approvals=0` 不是疏忽，而是与 B-lite gate、注意力预算同构——人审前置到 **gate 设计层**（只卡 security、fail-closed、双保险），不占用每个 PR 的同步注意力块。文章面向大企业 code owner 必审；STP 单人 + ~20 host 规模下是 **有意分歧**。

### 3.5 Maintain

| 维度 | 文章 | 本项目 | 对齐度 |
|------|------|--------|--------|
| 破带检测 | 确定性 bands | backstop 失败开 issue、恢复关；事故 → 确定性 CI 门 | 🟡 / ✅* |
| LLM 分诊 / 写 intent | 2σ/3σ 唤起 Claude | 止于通知；人手读日志 | ❌ |
| 回滚演练 / DORA | 明确要求 | hot-update runbook 有；演练与 DORA 无 | ❌ |

\*事故→**确定性**门禁在机械类风险上可视为文章 LLM eval 形态的替代；语义类回归仍无覆盖。

### 3.6 审计产物：Agent Note ≈ 半套 playbook 链

| Playbook 链 | STP 对应 |
|-------------|----------|
| intent / spec / plan | 弱/分散（issue、design、会话 plan） |
| 方向决策 | ADR |
| 非平凡取舍 | **Agent Note**（强制四节） |
| 合入证据 | PR + required checks |
| 事故 | `docs/notes/`、`docs/operations/incident-*` |

Agent Note 的强制性高于文章「自愿工件」；粒度是**决策与运维**，不是「每个变更一条 intent→plan」。

---

## 4. 三层治理剖面（本文独有组织轴）

将文章 Build/Test/Deploy 横切为 STP 与 Playbook 的「劝导 / 确定性 / 合入门禁」对照：

| 层 | Playbook | STP | 会话内 | 合入时 |
|----|----------|-----|--------|--------|
| 知识加载 | CLAUDE.md ~1 页 | CLAUDE + AGENTS + rules（偏长、分层） | ✅ 强 | 间接（lint/compileall） |
| 咨询性策略 | skills | rules/globs；无仓内 skills | 🟡 | — |
| 确定性动作拦 | hooks（PreToolUse） | **无** agent hooks | ❌ | 🟡 CI/pre-commit 补位 |
| 合入门禁 | REVIEW.md + code owner | pr-agent-gate + 6 checks + #421 | — | ✅ 超出 |

**结论**：STP 把「确定性」 heavily 押在 **合入路径** 与 **夜间全量**，会话内软约束多、硬拦少——与 Playbook「build 阶段 hook 高频、deploy 阶段 hook 审批门」的分层不完全同构，但符合注意力预算。

---

## 5. 已对齐且宜保持（防重构误伤）

1. **CI 分层 + 注意力预算**：任何「全量搬回 PR 必查 / Merge Queue」须先过 `2026-08-14-merge-path-attention-budget.md`。
2. **fail-closed 三细节**：门禁与命令分离、#421 双保险、infra vs 真实失败区分——事故驱动，删任一会重开已知洞。
3. **事故→确定性门禁习惯**：空行污染「文件整体空行率」须保持在最前检查位。
4. **Agent Note 四要素 + 文档只写现状**：对照基线可长期复用。
5. **CLAUDE.md / rules 单一事实源 + 薄适配**：改全局先改 AGENTS/CLAUDE，勿在 rules 复制整篇。

---

## 6. 缺口清单

| # | 缺口 | 影响 | 现有缓解 | 与同题稿编号 |
|---|------|------|----------|--------------|
| G1 | 治理面无 continuous evals | agent 契约/门禁质量靠「下次犯蠢」发现 | 无 | 与 claude-code G1 同 |
| G2 | Plan/Design 无机读交接 | 多 agent 接力易丢意图 | design/ADR/Note 分散 | cursor 稿单列；codebuddy **G6** |
| G3 | 无项目级 agent hooks | 已发布脚本目录等可先改坏，等 CI | ruff exclude + CI；git hook 需手动启用 | claude-code **G3**（高危路径） |
| G4 | AI 审单向 | non-security findings 耗合入外注意力 | B-lite 仅卡 security | claude-code **G2** |
| G5 | Maintain 无分诊 / intent 回流 | backstop issue 启动成本高 | 自愈关 issue；人工写 note | claude-code G4 |
| G6 | 回滚未演练；无 DORA | 回滚非「演练最多路径」 | hot-update runbook | claude-code G5 |

**汇总勿按号硬并**：G2/G6（本稿/claude-code）与 codebuddy G6（机器可消费工件）主题相近但编号不同；交叉汇总按 **主题列** 并表（见 §9）。

---

## 7. 建议（按性价比，含「刻意不追」）

### 7.1 值得小步补（不破坏 ~2min 预算）

| 优先级 | 建议 | 验收直觉 | 与 claude-code-2 裁决 |
|--------|------|----------|----------------------|
| **P0** | 治理面最小 evals（10–20 条契约冒烟）；paths 触发或挂 backstop | 故意注入的契约错误能被拦一次 | **先拦截** |
| **P1** | 高风险路径确定性拦网（hook 或本地脚本）：`scripts/*/v*/`、`.env.backend` | 会话内直改即时拒绝 | **先拦截** |
| **P1** | 非平凡 PR plan 意图写入 Agent Note Decision（或 PR 固定小节） | 抽查 feature PR 可定位改哪些文件/如何证 | **后赋能**（G2/G6 轻量补） |
| **P2** | backstop failure 附机械摘要；有余力再加 `claude -p` 分诊 | issue body 含三要素 | — |
| **P2** | DORA 近似 + hot-update 回滚演练记录 | 月度可查 | — |

### 7.2 刻意不追（与当前治理同构）

| 不追项 | 理由 |
|--------|------|
| 强制 code owner 每个 PR | 打穿注意力预算；人审已在 gate 设计与供应链例外 |
| 完整 `intent/` 产品组织流程 | 工程平台 + 单人主路径，非多角色产品流水线 |
| 无头 Maintain「告警→intent→全自动修」 | 真机/生产库约束；宜确定性检测 + 人开环 |
| 企业级 managed settings / MCP 部署分层全套 | 规模未到；文档禁区 + ADR-0024 更划算 |

---

## 8. 局限性与置信度

- **原文单源**：仅博客正文；未读文末平台文档清单。
- **静态对照**：分支保护采信 `AGENTS.md`，未用 `gh api` 复核；workflow 未实测触发。
- **视角**：产出方为 Cursor Composer，对「仓内 rules vs Claude Code hooks」权重可能偏高；交叉时应与 claude-code 稿对质。
- **规模**：文章面向大型企业；§7.2「不追」是单人 + ~20 host 前提，非遗漏。

---

## 9. 交叉分析指引（与其他同题产出汇总）

参照 [ADR_0029_0030 四份并行评审](./ADR_0029_0030_IMPLEMENTATION_REVIEW_2026-08-24_245a4531.md) 模式：**多产出方独立勘察一致的事实可免复核采信，分歧显式化后只在裁决点花注意力**。

### 9.1 事实层（应一致；冲突以 file:line / `gh` 复核）

- required checks 是否恰六项；全量是否不在 PR 路径；
- pr-agent-gate fail-closed 四类条件；B-lite 是否仅 security；
- `.claude/` 下是否仍无项目级 hooks/skills/agents。

### 9.2 判断层（预期分歧，需人裁决）

| 议题 | 本文立场 | 他稿参考 |
|------|----------|----------|
| `approvals=0` + auto-merge | 有意取舍，非落后 | claude-code / cursor 接近 |
| evals 挂载 | paths 触发或 backstop，不拉长 PR 阻塞 | claude-code P0 同 |
| G2/G6 规划工件 | P1 轻量（Note/PR 小节），不强制 `plan.md` 仪式 | claude-code-2：G6 降级 P1 |
| 演进序 | 先拦截后赋能 | claude-code-2 §0 裁决 |

### 9.3 本文补充角度（他稿若未覆盖可并入）

- **§4 三层治理剖面**：会话内 vs 合入时「确定性」分布；
- **Deploy 与文章分歧的显式定性**：更激进合入 + 更工程化防漏判；
- **六阶段成熟度总表**（§0）作为汇总索引行。

### 9.4 他稿更强、本文宜吸收处

- file:line 证据密度、15 维度矩阵 → [claude-code 稿 §2–§3](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md)
- 原文双源核对、机器可消费工件论述 → [codebuddy 稿](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md)
- 三方分歧裁决表 → [claude-code-2 稿 §5](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md)
- Dependabot 分组 vs 供应链治理 → claude-code 稿 §8.3（本文未展开）

### 9.5 利益声明

见 §8 第三条；汇总文档应并列各稿利益声明，避免单方「AI 应获更多自主权」未经质询进入实施。

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-26 | 初版：WebFetch 原文 + 六阶段对照 + 三层治理剖面 + G1–G6 + P0–P2 + 交叉分析指引；产出方 Composer |
