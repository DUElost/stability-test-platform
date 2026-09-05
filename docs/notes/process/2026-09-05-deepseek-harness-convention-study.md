# deepseek-harness 约束体系对照学习

Status: implemented
Class: process

对 deepseek-ai/deepseek-harness 的 `AGENTS.md` / `CLAUDE.md` / `.claude/` 约束设定
逻辑做了学习与对照，结论作为后续 AI Coding Execution ADR 的引用基线。本文只记录
研究事实与建议，不修改任何共享元文件。注：远端仓库**没有** `.codex/`、`.cursor/`
（它本身是 agent-harness 产品，hooks 以产品代码承载），「.codex 约束设定」对应的
取舍是「不设 .codex」，本分析覆盖该取舍。初稿经人工审阅核出两处事实错误
（CLAUDE.md 文件形态、AGENTS.md 计数），已在本文修正，修正记录见 Verification。

## Decision

### 远端约束体系骨架（研究事实，采样 commit d347e7039）

1. **单一真相由 git symlink + 编辑规则保证**：根 `AGENTS.md`（100644，约 16.5KB）
   承载全部常驻规则——含命令清单、secrets/.env 政策与大量具体约定，规则多为
   1–3 行并链接自己的 home 文档；`CLAUDE.md` 是**指向 `AGENTS.md` 的 git symlink
   （mode 120000）**，内容一致由 git 层保证；`AGENTS.md` 自述「edit the real
   file」。注意：GitHub contents API 会把指向常规文件的 symlink 解析成 file 返回，
   初稿因此误判为两个独立文件，git mode 才是准绳。
2. **作用域化 AGENTS.md 层级**：全库共 **21 个 AGENTS.md**，其中 4 个是
   `snapshots/session/{agent-instructions,ptc-workspace-context}/workspace/` 下的
   会话快照测试夹具（其中 agent-instructions 的 workspace 与 nested 两个为
   symlink），`apps/cli/tests/profiles/AGENTS.md`
   位于测试子树（用途未核）；规则性文件约 16 个，分布在 `packages/`（组级 +
   client/experimental/schedule/web）、`docs/`、`scripts/`、`vendor/`、
   `website/`、`native/landlock-run/`、`.github/`、`snapshots/`（顶层规则）、
   `.agents/notes/`（含 archived/、implemented/ 分层）。`.github/AGENTS.md` 专门教
   CI job 摆放语义，防止 agent 把 master-only job 加进 PR workflow。
3. **`.agents/` 单家目录 + harness 侧薄适配**：skills 与 notes 的真相都在
   `.agents/`；`.claude/` 里只有一个条目：`skills` symlink →
   `../.agents/skills`；不被某 harness 读取的内容不为其做适配。
4. **notes 生命周期工程化，但 supersession 是作者义务而非自动 gate**：路径即状态
   `{proposed,implemented,rejected,archived}/{class}/{日期}-{主题}.md`；class 闭集
   由 `verify-agent-note-classification.ts` 校验，文件格式由
   `verify-agent-note-format.ts` 校验（含 `Status:` 与所在目录一致），归档冻结由
   `verify-archived-agent-notes.ts` 校验（append-only + hash）；`implemented/` 事实
   随代码原地更新、**决策不得原地改写**（反悔写新 note 并交叉链接）。「每个新
   note 执行 supersession 检查」只以 `.agents/notes/AGENTS.md` 规则文本要求作者
   执行（人工义务，配 dsh-archive-agent-notes skill 辅助），**未找到独立自动
   gate**。
5. **skill = 触发式 description + 运行级 SOP**：如「Use before pushing,
   force-pushing, marking ready for review, **or claiming checks pass**…」；正文
   是含精确命令与 CI gotcha 分诊的完整流程，并内嵌反幻觉条款（pending 报
   pending；只报告实际运行过的命令；不得仅因命令成功就声称验证通过）。
6. **门禁文化 + 预算 semantics**：可机械检查的不变量聚合进顶层 gate（doc-sync），
   并「证明每个被改的验收路径会拒绝一个非法用例」。**word ceiling 由
   `scripts/doc-budgets.manifest.json` 只对 8 份文档强制**：根 `AGENTS.md` 1950、
   `docs/AGENTS.md` 1320、`docs/architecture.md` 2400、`packages/AGENTS.md` 750、
   `packages/README.md` 994、`docs/testing.md` 1300、`docs/cordis-primer.md` 600、
   `docs/defensive-patterns.md` 550——**不是**「根 1950、所有子树 600」；
   docs/AGENTS.md 散文另列 tier 目标（如「subtree AGENTS.md ≤ 600」、
   examples/AGENTS.md 310），其中部分条目不在 manifest（有漂移迹象），生效范围以
   manifest 为准。预算语义：ceiling 是 guardrail 而非 reduction target——先搬迁/
   压缩，raise 需在 diff 中 justify。

### 审阅裁决与采纳顺序（供后续 ADR 引用）

总体方向可行：上游体系与本仓库基线
（[2026-09-05-ai-harness-convention-baseline](2026-09-05-ai-harness-convention-baseline.md)）
同向——中立内容住中立文件、适配层薄、防复制、机械 gate。上游 root 16.5KB 支持
「分层 + 预算」的机制，但**不能**用来证明「根文件必须极小」（本仓库 80 行/8KB
预算源自 2026-09-05 Harness 基线及其治理面修订，取代
RESIDENT_CONTEXT_AUDIT_2026-08-27 的 C1–C4 保留结论——该审计当时保留多项常驻
内容，并非预算来源）。差距点按采纳顺序排列：

- **G1 硬不变量跨 Harness 可见 —— 已采纳**：8 条跨模块硬不变量已移入
  `AGENTS.md`，`CLAUDE.md` 只做导入 + 路由。当前 AGENTS 63 行/3815B，未放宽
  80 行/8KB ceiling。
- **G4 证据纪律 —— 已采纳**：「只报告实际运行过的命令与结果；pending 报
  pending；不得因命令成功就声称验证通过」已进入 AGENTS「提交前」小节。
- **G3 note 取代/归档 —— 已采纳规则 + 基础校验**：`docs/notes/README.md` 要求新
  note 先检索旧 note，部分取代交叉链接，完全取代同 PR 归档；治理 checker S10
  校验 2026-09-05 起新 note 的 Status/Class 与目录一致。197 份存量中 78 份旧格式
  明确 grandfathered，不迁移目录、不批量改写历史。
- **G2 backend scoped 指令命名不对称 —— 条件采纳**：`backend/agent/{,aee/}
  CLAUDE.md` 内容中立却用 Claude-only 命名。注意措辞修正：根 `AGENTS.md`「开始
  任务时」第 2 条已要求**所有** harness 检查目标目录的 scoped `CLAUDE.md`，非
  Claude harness 不是「完全拿不到」，而是仅经根规则人工路由、无自动加载。采纳
  形态：真身转 `backend/agent/AGENTS.md`（或 scoped 文件内新增 AGENTS.md 真身 +
  CLAUDE.md 薄壳/symlink，与上游模式一致）；实施前先实测目标 harness 对嵌套
  AGENTS.md 的自动发现行为，并把文件纳入共享元文件串行修改与 checker 覆盖。
- **G5 预算操作细则 / `.agents/` 单家目录 —— 继续观察**：不做采纳决定，等
  Execution ADR 或新 harness 适配出现时再评估。

### 本次处理

- 研究提交先冻结上游事实与裁决；后续 Phase -1 Execution 串行完成 G1/G4/G3，
  同步 `AGENTS.md`、`CLAUDE.md`、Harness 路由、notes 规范和治理 checker。
- G2/G5 不进入 ADR-0034 前置阻塞集；本文作为后续 ADR 的引用基线。

## Alternatives

- **照搬远端 notes 目录生命周期**（proposed/rejected 目录 + 「状态=所在目录」
  gate）——放弃：本仓库 docs/notes 已按 class 分目录并配 README 状态约定与
  archived 冻结区，ADR（方向级）+ note（其余）双轨运行良好；迁移全部存量 note
  的代价高于收益（对应 G3 只采纳规则与基础校验）。
- **把 AGENTS 预算放宽到上游风格**——放弃：上游 root 16.5KB 只证明其「分层 +
  预算」机制，不构成「根必须大」的论据；本仓库 80 行/8KB 预算由 2026-09-05
  Harness 基线及其治理面修订引入，并取代 RESIDENT_CONTEXT_AUDIT_2026-08-27 的
  C1–C4 保留结论（该审计当时保留多项常驻内容，非预算来源）。
- **为多 harness 建 `.agents/` 单家 + symlink（上游模式）**——暂缓（G5）：当前
  skills 只有 Claude Code 一个消费方；等新增受版本控制的 harness 适配时再评估。
- **在研究提交中同时落地 G1–G4**——放弃：先独立冻结证据，随后由串行 Phase -1
  Execution 落地 G1/G4/G3，避免研究分支并发修改共享元文件。
- **立即用 git 级 pre-push 钩子替 Codex hooks**——放弃：Codex PreToolUse
  （apply_patch 前 tsc）的精细控制在 git hook 层做不到，两者互补；观察项。
- **双语 / i18n 化**——不适用：远端是双语国际项目；本仓库单语中文。

## Verification

- 采样基线：deepseek-ai/deepseek-harness @ **d347e703908d0406b7a7ef80e3a0e594d86b2215**
  （master，release/dsh-0.1.3-alpha.1 merge；2026-09-05）。方法：gh api 递归 tree
  + contents raw。已核实数字：AGENTS.md 共 21 个（其中 4 个 snapshots/session/
  workspace/ 快照夹具）；根 AGENTS.md size 16557B、mode 100644；CLAUDE.md mode
  **120000（symlink）**；doc-budgets manifest 8 条目（见 Decision 骨架 6）；notes
  gate 脚本存在 verify-agent-note-format / -classification / verify-archived-agent-
  notes，无 supersession 专用 gate。
- 审阅修正（2026-09-05，两轮）：第一轮——初稿「CLAUDE.md 为 git 内独立重复
  文件」错误（contents API 对指向常规文件的 symlink 按 file 返回导致误判，git
  mode 120000 为准）、「19 处嵌套」错误（实际 21 个路径、含 4 个快照夹具）；
  第二轮——快照夹具中 symlink 为 2 个（agent-instructions 的 workspace 与
  nested）而非 1 个；CLAUDE.md 硬不变量为 8 条而非 6 条；80 行/8KB 预算出处为
  2026-09-05 Harness 基线及其治理面修订（取代 RESIDENT_CONTEXT_AUDIT_2026-08-27
  的 C1–C4 保留结论），而非该审计本身。
- 局限：未在远端运行任何 verify gate（无本地 pnpm 环境）；11 个 skills 与
  archived notes 未逐个通读；远端是自身产品的 dogfood 实践，仓库类型（TS monorepo
  产品）与语言（双语）和本仓库（Python/FastAPI 运维平台、单语中文）不同，结论
  迁移注意语境差；`apps/cli/tests/profiles/AGENTS.md` 用途未核。
- 本地结构性检查：`venv/bin/python tools/dev/check_governance_surface.py --check`
  通过（阻塞项全绿 S1–S10、S5x）；`--self-test` 通过。

## Revisit

- 后续 Execution ADR 定稿前引用本文的事实边界与已采纳状态；
- G2 实施前实测嵌套 AGENTS.md 自动发现行为；新增受版本控制的 harness 适配、
  harness 升级改变规则自动发现时（对应 `harness-adapters.md` 的修改顺序）；
- AGENTS 接近 80 行/8KB ceiling，或硬不变量再次出现 Harness 不对称时复查 G1。
