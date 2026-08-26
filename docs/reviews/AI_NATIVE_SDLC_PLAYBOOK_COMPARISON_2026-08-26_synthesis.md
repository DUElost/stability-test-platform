# AI-Native SDLC Playbook × stability-test-platform 对照评审总汇（Synthesis）

- **状态**：Living（2026-08-26 初版；随建议项落地逐条回填验收钩子）
- **日期**：2026-08-26
- **性质**：**总汇与裁决固化**（非新分析、非 ADR）——对同日五份同题评审做 canonical 缺口编号映射、判断层裁决固化、最终优先级收敛与负向决策采纳；不引入新的事实勘察
- **产出方**：Claude Code（综合评判轮）
- **输入**（按落盘时间序）：

| # | 文档 | 产出方 | 独有贡献 |
|---|------|--------|----------|
| 1 | [claude-code 首轮](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code.md) | Claude Code | file:line 证据基线、15 维对照矩阵 |
| 2 | [codebuddy](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md) | CodeBuddy | 原文双源核对、C-G6 机器可消费工件缺口、「gate 单点依赖」论证素材、skills 化路径 |
| 3 | [claude-code 第二轮](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md) | Claude Code | 事实锚点复核、三方裁决（G6 降 P1、先拦截后赋能）、DOC-MAP 引用完整性发现 |
| 4 | [cursor](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md) | Cursor Agent / Auto | 六阶段成熟度总表、「刻意不追」清单、plan 意图轻量承载案 |
| 5 | [composer](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md) | Cursor Composer | 三层治理剖面（知识/咨询/确定性/门禁 × 会话内/合入时）、引文级证据 |

---

## 0. 结论速览

**最终对齐度判定**（五稿收敛，仅列与首轮 TL;DR 有出入处）：

| 维度 | 判定 | 相对首轮的变化 |
|------|------|----------------|
| 工件链 / 审计轨迹 | ✅ 对齐，**附注 C-G6 子面缺失** | codebuddy 指出首轮盲区：现有工件全为人读决策档案（ADR/Agent Note），缺 agent 可直接接力的行动起点；首轮「整体对齐」结论对人读面仍成立 |
| 其余七维 | 与首轮一致 | 注意力预算 ✅超出 / 事故→门禁 ✅超出* / 评审循环 🟡 / CLAUDE.md 🟡 / evals ❌ / hooks 🟡 / Maintain 🟡 |

**一句话结论**：本项目在 Deploy 门禁与机构知识入仓上已是强 AI-native 且多处严于原文（fail-closed、防绕过双保险、注意力预算明文化）；差距集中在元治理——治理面自身无 eval 门禁（C-G1）、会话内硬拦缺位（C-G3）。判断层三项分歧已固化裁决（§2），负向决策已正式采纳（§3）。

---

## 1. Canonical 缺口编号（C-G1…C-G7）与各稿映射

> 各稿 G 编号互不同构，本表为唯一权威映射；后续 issue 跟踪一律用 C 编号。

| Canonical | 定义 | claude-code 首轮 | codebuddy | claude-code-2 | cursor | composer | 最终优先级 |
|-----------|------|------------------|-----------|---------------|--------|----------|------------|
| **C-G1** | 治理面无 continuous evals（CLAUDE/AGENTS/.cursor/pr-agent 配置变更零回归防护） | G1 | G1 | G1 | G1 | G1 | **P0** |
| **C-G2** | AI 评审单向：non-security findings 无修复闭环，无 Important/Nit 标准 | G2 | G2 | G2 | G4 | G4 | P1 |
| **C-G3** | 高危路径无本地强制拦截（`scripts/*/v*/`、`.env.backend`、`hosts.ini`） | G3 | G3 | G3 | G3 | G3 | P1 |
| **C-G4** | Maintain 无分诊 / intent 回流（backstop failure issue 后人肉读日志） | G4 | G4 | G4 | G5 | G5 | P2 |
| **C-G5** | 回滚未演练 + 无 DORA 近似指标 | G5 | G5 | G5 | G6（部分） | G6（部分） | P2 |
| **C-G6** | 无机器可消费规划工件（intent/plan）：agent 无法接棒人类已完成规划 | —（盲区） | **G6** | G6（降级） | G2 | G2 | P1（裁决见 D2） |
| **C-G7** | bug 先写失败测试 + 测试文件保护无机制强制 | 矩阵 #10 ❌ | 矩阵 #10 ❌ | 矩阵 #10 ❌ | G6（部分） | G6（部分） | P2（低） |

事实层基线（五稿独立勘察零冲突，免复核采信）：required checks 六项、pr-agent-gate fail-closed 四类条件 + B-lite 仅 security 否决（`docs/notes/process/2026-08-21-replace-coderabbit-with-pr-agent-gate.md`）、`.claude/` 无项目级 hooks/skills/agents、全量 CI 不在 PR 路径。

---

## 2. 裁决固化（判断层分歧的最终结论）

| # | 议题 | 裁决 | 理由要点 | 来源 |
|---|------|------|----------|------|
| D1 | `approvals=0` + auto-merge 定性 | **维持，属有意取舍** | 人审前置到 gate 设计层；但 gate 因此成为单点依赖且自身无 eval 守护——该论证作为 C-G1 列 P0 的第二条结构性理由（守 gate 的 gate） | codebuddy 提出 → claude-code-2 升格 |
| D2 | C-G6 严重度与落地形态 | **P1**；落地形态采 cursor 轻量案：非平凡变更把 plan 意图写入 Agent Note Decision 固定小节（改哪些文件/风险/如何验证），不强制新建 `plan.md` 仪式；大型变更可选 plan.md | 大型变更已有 design doc 承接；小型变更加仪式是每 PR 文档负担；文章把 plan.md 定位在护栏成熟后的 auto mode 阶段，本项目护栏（evals/hooks）尚未建立 | codebuddy 提出 → claude-code-2 降级 → cursor 定形 |
| D3 | 演进时序 | **先拦截后赋能**：C-G1/C-G3 先行，C-G6/skills 化随后 | 拦截缺口的代价是已实付的（@import 中文行内静默失效事故）；文章自身要求「先护栏后加速」 | claude-code-2 |
| D4 | Agent Notes skills 化 | **有条件支持**：沿薄适配层哲学——权威内容留 CLAUDE.md/AGENTS.md，skill 只放指针与可执行校验，禁止复制全文形成第三轨漂移面 | `.cursor/rules` 已有同构先例 | cursor/composer 提出 → claude-code-2 附条件 |
| D5 | 「刻意不追」清单 | **采纳为正式负向决策**（见 §3） | 负向决策也须留痕，符合仓库 ADR/Note 文化；单人+20 host 规模前提 | cursor/composer |

---

## 3. 「刻意不追」清单（正式负向决策，重议须新开 ADR）

| 不追项 | 理由 | 重议触发条件 |
|--------|------|--------------|
| 强制 code owner 审批每个 PR / 恢复人工 approvals | 打穿 ~2min 注意力预算；人审已在 gate 设计层与供应链例外路径 | 团队规模 ≥3 人常驻，或 pr-agent-gate 出现两次以上漏判 |
| 完整 `intent/` 产品组织流水线 | 工程平台 + 单人主路径，非多角色产品协作 | 出现稳定的多角色需求方（产品/测试工程师向平台提意图） |
| 无头 Maintain 全自动闭环（告警→诊断→自动修） | 真机 + 生产库同机约束下风险不可控；保持确定性检测 + 人开环 | 生产库与控制面分离部署后 |
| 企业级 managed settings / MCP 部署分层全套 | 规模与数据分级未到；文档禁区表 + ADR-0024 production guard 更划算 | 控制面对外开放或引入外包协作者 |

---

## 4. 最终行动清单

> 2026-08-26 逐项审计后收敛（博客前提依赖项已剔除/降级；四项待决点经用户裁决，
> 见设计文档 [2026-08-governance-surface-protection.md](../design/2026-08-governance-surface-protection.md) §8）。

| 优先级 | 行动 | 状态 | 对应缺口 |
|--------|------|------|----------|
| **P0** | L0 结构门禁（S1–S5 阻塞进 ci.yml lint 与 check:quick/pr）+ S6 信息行；L1 十二条契约 evals 为按需诊断（`check:gov` 手跑，不进 CI） | ✅ 已落地（门禁化前提不成立故降挂载；重议条件见 Revisit） | C-G1 |
| P1 | 已发布脚本版本 M/D 的 pre-commit 提交现场拦截（引擎中立）+ `.claude/settings.json` 凭据写保护 | ✅ 已落地 | C-G3 |
| P1 | 规划意图轻量承载：Agent Note Decision 节兼作轻量 plan 记录（模板半句微调）；skill 低风险试点 test-env-self-check | ✅ 已落地（用户裁决） | C-G6 / C-G7 部分 |
| 观察 | pr-agent non-security findings 修复闭环 → 收集 1–2 月处置数据再议 | 👁 观察项（用户裁决） | C-G2 |
| P2 | backstop failure issue 附机械摘要三要素（**砍** claude -p 分诊，扩展位保留） | ✅ 已落地 | C-G4 |
| 不建 | DORA 近似采集——需要时按查询口径现查 | 🚫 用户裁决暂不建 | C-G5 |
| 降级 | 回滚演练改为「真实回滚即演练」：runbook §5 追加记录表 | ✅ 已落地（用户裁决） | C-G5 |
| 负向 | 失败测试优先机制化（C-G7）→ 并入「刻意不追」，出现「为过测改测」事故再翻案 | 🚫 正式负向决策 | C-G7 |

---

## 5. 保持清单（防重构误伤，五稿一致）

1. CI 分层结构本身（PR 轻 / main 夜间全量 backstop）——任何「全量搬回合入路径」建议先过 `docs/notes/process/2026-08-14-merge-path-attention-budget.md`；
2. fail-closed 三细节：门禁/命令 job 分离、#421 disable-auto 双保险、infra 错误与真实失败区分；
3. 事故→确定性门禁习惯（空行污染「文件整体空行率」检查保持在最前位）；
4. Agent Note 四要素模板（决定/放弃备选/如何验证/何时重议）；
5. CLAUDE.md/AGENTS.md 单一事实源 + `.cursor/rules` 薄适配层。

---

## 6. 方法论备注（供后续多 agent 评审复用）

- **独立性折扣**：cursor 与 composer 同出 Cursor 家族、文本重叠约八成，共识计数折合为一票——本次实际独立视角 ≈3.5 个。后续并行评审应要求各产出方在文件头声明引擎与模式，避免署名混写。
- **利益声明并读**：claude-code 两轮立场（先拦截、限制自身自由度）与被治理 AI 自我利益反向，可信度加分；codebuddy 的扩权倾向（plan.md P0、skills 化）需按其自己声明的方向质询后采纳——本轮经 D2/D4 质询后部分采纳。
- **编号纪律**：并行评审各自编号必然漂移；汇总时建立 canonical 映射（即本文 §1），后续文档引用 canonical 编号而非各稿本地编号。

---

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-26 | 初版：五稿输入收敛、canonical C-G1–C-G7 映射、D1–D5 裁决固化、「刻意不追」正式采纳、最终行动清单 |
| 2026-08-26（晚） | 逐项审计：区分〔证〕/〔前〕依据并剔除博客前提依赖项（L1 门禁化→按需、分诊→机械摘要、C-G7→负向决策）；四项用户裁决（skill 低风险试点 / findings 观察项 / 回滚演练=真实即演练 / DORA 暂不建）；C-G1 两层方案落地，详见 `design/2026-08-governance-surface-protection.md` |
