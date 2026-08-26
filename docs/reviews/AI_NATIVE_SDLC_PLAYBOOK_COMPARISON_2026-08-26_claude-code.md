# AI-Native SDLC Playbook × stability-test-platform 治理实践对照审查

- **状态**：Living（2026-08-26 初版；结论随治理实践演进修订）
- **日期**：2026-08-26
- **性质**：**外部方法论对照评审**（非 ADR、非 Agent Note）——Anthropic《The AI-Native SDLC Playbook》要点提炼 + 本项目 CI/CD 与 AI 治理实践逐维度对照 + 缺口清单
- **来源**：<https://claude.com/blog/the-ai-native-sdlc-playbook>（Anthropic Applied AI 团队；2026 年发布）
- **产出方**：Claude Code（ox-alpha）——文件名尾缀 `claude-code`，供与其他 agent 同题分析文档并列比对
- **方法**：WebFetch 抓取原文全文提炼 → 通读 `.github/workflows/{ci,pr-agent,main-ci-backstop,enable-auto-merge}.yml`、`.githooks/pre-commit`、`scripts/run_gates.py` 门禁定义、根 `CLAUDE.md` / `AGENTS.md` / DOC-MAP → 以 file:line 为证据逐条对照。未运行任何验证性实验（见 §7 局限）
- **关联评审**：[同题 · Cursor](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_cursor.md)（六阶段成熟度 + Plan/Design 产物链 +「刻意不追」；产出方 Cursor Agent / Auto）、[同题 · Composer](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_composer.md)（六阶段 + 三层治理剖面 + G 主题并表指引；产出方 Cursor Composer）、[同题 · CodeBuddy](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_codebuddy.md)（原文双源核对 + G6 + §7 交叉比对）、[同题 · Claude Code 第二轮](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_claude-code-2.md)（三方交叉比对与裁决）、[总汇 Synthesis](./AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md)（canonical 编号映射 + 裁决固化；本稿盲区「机器可消费工件」在其中列为 C-G6）

---

## 0. 结论摘要（TL;DR）

| 维度 | 文章主张 | 本项目 | 对齐度 |
|------|----------|--------|--------|
| 注意力集中在 gates | 核心命题 | **显式成文为「合入路径注意力预算」(~2min)** | ✅ 超出 |
| 生产事故 → 回归门禁 | continuous evals 承接事故 | 事故→确定性 CI 门禁（空行污染/脚本不可变）已制度化 | ✅ 超出（形态不同） |
| PR 评审循环 + 职责分离 | Claude 双向收发评审 | fail-closed security gate + 防绕过双保险，但**单向（审而不修）** | 🟡 部分 |
| 工件链 / 审计轨迹 | intent→spec→plan→PR→incident | Issue→ADR→design→PR+Agent Note 强制归档 | ✅ 对齐 |
| CLAUDE.md 治理面 | 约 1 页，犯错两次即写入 | 体系完整但远超一页，靠分层加载补偿 | 🟡 部分 |
| **continuous evals（元治理）** | 治理面自身变更须过 eval 门禁 | **完全缺失** | ❌ 缺失（最大缺口） |
| hooks 强制护栏（不可关闭） | managed settings 兜底非协商规则 | pre-commit 手动启用 + CI 兜底；无 project-level Claude Code hooks | 🟡 部分 |
| Maintain 分层响应 + LLM 分诊 | bands.yaml + `claude -p` 自动分诊 | backstop 自愈闭环完整但止步于「通知」，无分诊 | 🟡 部分 |

**一句话结论**：本项目在文章 Stage 3–5（Build 护栏 / Test 事故转化 / Deploy 门禁）上不仅对齐、多处更严格（fail-closed、防绕过双保险、门禁与命令分离）；真正差距集中在「元治理」——**用来管 AI 的那些配置本身没有质量门禁**（evals 缺失），以及 Maintain 阶段自动化止步于通知而未到分诊。

---

## 1. 原文内容摘要

### 1.1 核心论点

代码编写不再是瓶颈。AI 使构建阶段压缩到小时级后：

1. 瓶颈转移到构建两侧——规划、评审/测试、部署；
2. 原有控制措施失效——人工逐行评审无法覆盖 agent 生成的绝大部分 diff;
3. 治理成本上升——例外处理仍要走按周/按月的会议。

### 1.2 方法论框架

- **线性流程 → 闭环**：每阶段以一个「承诺工件」收尾，下一阶段从读取该工件开始：
  `intent.md → spec.md → plan.md → diff+测试 → PR+评审发现 → 事故记录`。
  提交链即审计轨迹（谁提出、agent 产出了什么、谁批准了什么）。
- **Plays**：按六个非线性阶段组织的战术单元（变更内容/入门条件/实施步骤/治理考量/度量），有依赖图，可选择性采纳。
- **人类职责重定位**：人的注意力集中在 **gates**——审查 agent 标记的内容，而非从头启动每个阶段；需要判断力的决策始终由人负责。

### 1.3 六阶段关键实践

| 阶段 | 关键做法 |
|------|----------|
| Plan | `intent.md` 用发起者自己的话捕捉意图，产品负责人审核后提交 |
| Design | spec 会话中主动产出 flagged concerns，产品负责人与政策所有者先行解决 |
| Build | plan mode 默认起点；护栏成熟后（调优的 CLAUDE.md/skills/hooks/测试套件）转 auto mode + worktree 并行；CLAUDE.md「犯错两次就写入」且约一页内；skills=咨询性控制、**hooks=必须无条件执行的强制护栏**（保护路径/自动格式化/凭据不入 diff）；重复工作封装为 subagents（如 verifier）；遗留系统 source-of-truth 三种配置可选 |
| Test | 给 Claude 反馈回路（测试/构建/截图 diff 自验自修），区别于全新上下文的 verifier subagent；bug 先写失败测试并用 hook 保护测试文件不被 agent 修改；**CI continuous evals**——20–50 个真实任务套件，在 CLAUDE.md/skills/hooks 变更时运行并作合并门禁；每次生产事故转化为一条回归 eval |
| Deploy | PR 评审循环：Claude 既发出也接收评审；技术负责人以 `REVIEW.md` 定义 pass 标准（bug/安全/spec 合规）、Important 与 Nit 界限及 nit 上限；`@claude` 提及触发修复推送；**职责分离——写代码的 agent 无权批准自己**；hooks 作审批门，非协商规则放个人不可关闭的托管设置；CI 中用 `claude -p` 处理判断型任务（构建失败分诊）；agent 任务跑沙箱 + 短期限定令牌；部署能力按 dev/staging/prod 经 MCP 分层授权；回滚须是演练最多的路径；滞后指标用 DORA |
| Maintain | 确定性脚本监控指标，控制带被突破时无人在调用路径上唤起 Claude；`bands.yaml` 分层响应（1σ 仅记录 / 2σ 只读诊断 / 3σ 经 PR 或预批准 runbook 行动）；诊断结果写成新 `intent.md` 重启闭环；Claude Tag 经 Slack 触发响应，频道历史即审计轨迹；复盘写入版本化 lessons 文件反哺 |

---

## 2. 对照基线：本项目 CI/CD 与 Agent 治理现状（证据清单)

| 实践 | 证据 |
|------|------|
| required checks 六项：lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests / pr-agent-gate | 根 AGENTS.md「Key conventions · PR 合入」 |
| 合入路径注意力预算 (~2 分钟)，超预算检查一律异步 | `docs/notes/process/2026-08-14-merge-path-attention-budget.md` |
| 全量 CI（backend-test/frontend-check/docker-build）仅 workflow_dispatch；每日 UTC 18:00 backstop dispatch 兜底 | `ci.yml:6-9`、`main-ci-backstop.yml:47-115` |
| pr-backend-test 信息性 job（PG 套件超预算故不作 required check） | `ci.yml:195-237` |
| pr-agent-gate：fail-closed（未完成/API 失败/输出缺失/security concerns 均 failure） | `pr-agent.yml:22-31,57-90` |
| 门禁与命令 job 分离（`/review` 评论不产生 required check，防 gate 被顶掉） | `pr-agent.yml:29-31,109-134` |
| #421 双保险：gate failure 显式 disable-auto（源自 PR #399 被 auto-merge 漏判合入的真实事故） | `pr-agent.yml:92-107` |
| auto-merge + branch protection：approvals=0、enforce_admins、strict=true | 根 AGENTS.md「Key conventions · PR 合入」 |
| 空行污染三重防线：pre-commit hook + CI 阻塞门禁 + 清理工具（AST 语义不变校验） | `.githooks/pre-commit:14-124`、`ci.yml:140-152` |
| 脚本版本不可变门禁（2026-07-31 ef8808e ruff --fix 原地改写致 18/27 script 派发中断的事故转化） | `ci.yml:154-161`、`tools/dev/check-script-version-immutability.py` |
| pre-commit 需手动启用（`git config core.hooksPath .githooks`），pipeline_engine.py 即因此漏入 | `.githooks/pre-commit:9-11`、`ci.yml:141-144` 注释自认 |
| backstop 失败→自动开 issue（同 label 去重）/恢复→自动关，含 infra 错误与真实失败区分（#384/#389） | `main-ci-backstop.yml:260-340` |
| Agent Note 制度：每个非平凡变更必须记录决定/备选/验证/重议条件 | 根 AGENTS.md「Agent Notes」 |
| 本机 `.claude/` 仅 settings.local.json，**无项目级 hooks/skills/agents 定义** | 目录实测（2026-08-26） |
| 生产环境分层约束为文档级（只读 SELECT 优先/禁 alembic 试跑/testcontainers 隔离）+ 应用层 production guard（ADR-0024 fail-fast） | 根 AGENTS.md「生产机调试约束」、CLAUDE.md 架构不变量 |
| 无 DORA 类指标采集；无 AI prompt/治理面文件的评测套件 | grep + 目录实测未见 |

---

## 3. 逐维度对照矩阵

对齐度图例：✅ 超出 / ✅ 对齐 / 🟡 部分 / ❌ 缺失

| # | 维度 | 文章主张 | 本项目现状 | 对齐度 | 关键差异 |
|---|------|----------|-----------|--------|----------|
| 1 | 人类注意力集中于 gates | 理念层倡导 | 量化为 ~2 分钟注意力预算并明文化取舍记录 | ✅超出 | 连「哪个检查值多少分钟注意力」都有 note 记录 |
| 2 | 事故→回归防护 | 每次生产事故转化为一条 regression eval | 两次重大事故各转化为一条二值确定性 CI 门禁 | ✅超出* | 形态是确定性脚本而非 LLM eval；对机械污染/契约破坏类风险更可靠零误判，但对「语义类回归」无覆盖（见 #8） |
| 3 | PR 评审循环 | Claude 发出也接收评审，REVIEW.md 定义 pass 标准，@claude 触发修复推送 | 单向：pr-agent-gate 只阻断/放行；security concerns 外的 findings 无处理闭环（CodeRabbit 卡门禁需手动 resolve 有先例） | 🟡部分 | 缺「修」的一侧；也无 REVIEW.md 式 Important/Nit 界限定义 |
| 4 | 职责分离 | 写码 agent 无权批准自己 | gate 只做 security 判定不做 approve；人工 approvals=0 但 enforce_admins 下合入完全信任机器门禁 | ✅对齐 | 本项目走得更激进：人的判断前置到 gate 设计层而非逐 PR 审批 |
| 5 | 防绕过加固 | 未展开 | fail-closed + 门禁/命令分离 + #421 disable-auto 双保险 | ✅超出 | 三条均来自真实事故复盘，文章未涉及此深度 |
| 6 | 工件链审计轨迹 | intent/spec/plan/review/incident 版本化工件链 | Issue→ADR→design→PR+Agent Note（强制，含放弃的备选与重议条件）→事故 note 归档 | ✅对齐 | Agent Note 的强制性高于文章的自愿性工件 |
| 7 | CLAUDE.md 治理面 | 约一页，「犯错两次就写入」 | 体系完整（架构不变量/关键约定/开发陷阱），但常驻部分数百行；有分层加载（子系统细则懒加载）与 .cursor/rules 薄适配层补偿 | 🟡部分 | token 成本与注意力稀释是实价；分层设计是文章没有的缓解手段 |
| 8 | **continuous evals** | CLAUDE.md/skills/hooks/prompt 变更须过 20–50 任务 eval 门禁 | **零**。改 CLAUDE.md/.cursor/rules/pr-agent 配置无任何评测守护 | ❌缺失 | 最大缺口。已有先例证明风险真实：CLAUDE.md @import 写在中文行内静默失效只能靠人肉 /context 发现 |
| 9 | hooks 强制护栏分级 | skill=咨询性；非协商规则放个人不可关闭的托管设置 | pre-commit 可手动绕过（CI 兜底补位，最终等效不可绕过但反馈延迟从秒级退化为分钟级）；无 project-level Claude Code hooks 保护高危路径（如已发布脚本版本目录、.env*/hosts.ini 凭据文件） | 🟡部分 | 高危路径目前只有 ruff exclude + CI 兜底，编辑器直改时本地零拦截 |
| 10 | bug 先写失败测试 + 测试文件保护 | hook 阻止 agent 修改测试文件 | 无机制强制；「verify before asserting」仅为行为约定 | ❌缺失 | 低优先级：现有 required checks 已能兜住大部分回归 |
| 11 | Maintain 分层响应 | bands.yaml（1σ/2σ/3σ 分级动作） | backstop 开 issue 一档制 | 🟡部分 | 有趣的是本项目产品侧的两层钟（timeout_seconds 安全网 + stall_seconds 显式打开）就是 banding 思想，未反哺自身 CI 治理 |
| 12 | LLM 构建失败分诊 | `claude -p` 非交互分诊判断型任务 | failure issue 开出后仍人肉读日志定位 | ❌缺失 | issue body 无红灯 job 摘要/疑似 commit 定位 |
| 13 | 部署分层授权 | dev/staging/prod 经 MCP 分层暴露 + 沙箱短期令牌 | 文档级禁区表 + 应用层 production guard；无工具层部署权限分级 | 🟡部分 | 单人+20 台 host 的规模下成本收益存疑 |
| 14 | 回滚=演练最多的路径 | 明确要求定期演练 | 有 hot-update runbook，无演练机制 | ❌缺失 | Agent 滚动升级有文档，回滚路径未演练 |
| 15 | DORA 滞后指标 | 采用 DORA 度量 | 未采集 | ❌缺失 | backstop issue 数/PR 生命周期可低成本近似 |

---

## 4. 已对齐且值得保持的实践（防止后续重构时误伤）

1. **CI 分层结构本身**（PR 轻 / main 夜间全量）：这是注意力预算的载体。任何「把全量搬回合入路径」的建议都应先过 `attention-budget` note 的取舍逻辑。
2. **fail-closed 方向的三个细节**：门禁/命令 job 分离、#421 双保险、#384 区分 infra 错误与真实失败——均为事故驱动，删除任一都会重开已知攻击面。
3. **事故→门禁的转化习惯**：空行污染三重防线中「文件整体空行率」检查必须保持在最前（pre-commit:46-77 注释记录了顺序错误的坑）。
4. **Agent Note 的四要素模板**（决定/放弃的备选/如何验证/何时重议）：比文章工件链更可操作。
5. **文档只写现状不写变迁**原则：使本文档 §2 的证据清单可以长期作为对照基线使用。

---

## 5. 缺口清单（按严重度）

| # | 缺口 | 影响 | 现有缓解 |
|---|------|------|----------|
| G1 | 治理面无 evals：CLAUDE.md/AGENTS.md/.cursor/rules/pr-agent 配置变更零回归防护 | AI 门禁与 agent 行为契约的质量只能靠「下一次犯蠢」发现 | 无 |
| G2 | AI 评审单向：findings 无修复推送闭环 | non-security findings 依赖人工往返，消耗合入路径外的人工注意力 | gate 只卡 security，其余仅参考 |
| G3 | 高危路径无本地强制拦截：已发布脚本版本目录、凭据文件在编辑器/AI 直改时零拦截 | 反馈延迟秒级→分钟级；AI 会话内可能先改坏再等 CI 拦 | ruff extend-exclude + CI 阻塞门禁 |
| G4 | backstop 失败 issue 无自动分诊 | 每晚看门人启动成本高；failure issue 可能积压 | 自愈闭环（恢复自动关）已存在 |
| G5 | 回滚路径未演练；无 DORA 指标 | 故障时回滚不是「演练最多的路径」；改进无度量 | hot-update runbook 存在 |

---

## 6. 建议（P0/P1/P2，含验收标准）

| 优先级 | 建议 | 验收标准 | 备注 |
|--------|------|----------|------|
| **P0** | 最小 continuous evals：10–20 条「给定治理面文件状态 + 典型问题 → agent 应答符合契约」冒烟集，覆盖 CLAUDE.md import 语法、表名单数、Pydantic v2、唯一 action 类型等高频契约；挂在 pr 路径（治理面文件变更时触发）或至少 backstop | 治理面 PR 在无人工干预下被 eval 拦截一次故意注入的错误（如中文行内 @import） | 不求 50 条；文章数字是大型组织经验值 |
| **P1** | project-level Claude Code hooks：deny 编辑 `backend/agent/scripts/*/v*/`、`.env.backend`、`hosts.ini`、`backend/.env` | hooks 生效后 AI 会话内直改上述路径被即时拒绝 | 与 .githooks 互补而非替代；CI 门禁保留 |
| **P1** | pr-agent findings 修复闭环：允许 AI 将 non-security findings 的修复推至同分支（push 自动复评把关） | 一个含 nit 的 PR 由 AI 修复推送并通过复评合入 | 注意与 CodeRabbit 卡门禁历史兼容 |
| **P2** | backstop failure issue 附 `claude -p` 分诊段：红灯 job 清单 + 失败步骤日志摘录 + 疑似 commit | 新开 failure issue body 含上述三要素 | 零人在调用路径上 |
| **P2** | DORA 近似指标：PR 生命周期 + change failure rate（backstop issue 频次近似） | 月度数字可在 GitHub API 一条查询内得出 | 先度量再谈目标 |
| **P2** | 回滚演练：hot-update runbook 加季度演练项 | 演练记录归档 docs/operations | — |

---

## 7. 局限性与置信度

- **原文单源**：仅依据博客正文，未读其引用的平台团队落地文档清单；文中数字（20–50 条 eval 等）未经第二来源印证。
- **静态核验**：所有本项目证据来自文件通读（file:line 见 §2），未实际触发 workflows 验证行为；分支保护设置（approvals=0/enforce_admins）采信 AGENTS.md 记载，未调 GitHub API 复核。
- **视角偏差**：本文产出方本身是「被治理的 AI」，对「AI 应当获得多大自主权」的判断（尤其 §6 P1 第二条）存在利益相关，交叉分析时应重点质询。
- **规模前提**：文章面向大型企业治理；本项目的单人+20 host 规模下，部分 play（部署 MCP 分层、DORA 体系）性价比存疑，本文已在优先级上体现该取舍。

---

## 8. 交叉分析指引（供与其他 agent 输出对照汇总）

汇总多份同题分析时可按以下核对点查缺补漏：

1. **事实层核对**（应各 agent 一致，不一致则以 file:line 复核为准）：
   - required checks 是否恰为六项；全量 CI 是否确实不在 PR 路径；
   - pr-agent-gate 的 fail-closed 条件枚举是否完整（未完成/API 失败/输出缺失/security concerns 四类）；
   - `.claude/` 下是否存在项目级 hooks/skills（当前实测：否）。
2. **判断层分歧**（预期会有分歧，需显式裁决）：
   - 「approvals=0 + auto-merge」是领先实践还是过度自动化？本文立场：人的判断已前置到 gate 设计层，属有意取舍；
   - CLAUDE.md 体量问题：瘦身 vs 分层加载已足够？
   - evals 的最小可行规模与挂载位置（PR 阻塞 vs backstop 异步）。
3. **本文可能遗漏的角度**（其他 agent 若覆盖请并入）：
   - 前端 vitest / docker-build 不在合入路径的风险敞口定量评估；
   - Dependabot 分组策略（auto-merge 组 vs 人工组）与文章供应链治理章节的对照；
   - `.cursor/rules` 与 CLAUDE.md 双轨漂移风险的量化。
4. **利益声明**：见 §7 第三条。
