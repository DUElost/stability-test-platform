# ADR-0034 多 Harness 执行契约只读审查（Proposed v0.3）

- 日期：2026-09-06
- 审查类型：只读系统性审查与审计（未修改任何文件）
- 审查对象：ADR-0034 `docs/adr/ADR-0034-multi-harness-execution-contract.md`（v0.2 → v0.3，commit `864da462`）+ draft note 增量 + #861 README 索引同步
- 范围基线：本审查承接同日同一会话对「约定文件规范化 + ADR-0034 v0.2」的整段审计（`0f4753fb..a6869878`，7 merges #846–#859），本文记录其 ADR-0034 相关处置状态 + v0.3 新结论
- resume：`9c124`（当前会话编号后六位）

---

## 1. 结论摘要

**v0.3 无 A 级缺陷、无阻塞项**。六项必修修正方向全部正确：两维状态模型修掉了 v0.2「仅 ACTIVE 参与 overlap」的真实语义错误；MERGED 绑定 GitHub PR 状态消灭「自报合入」虚假终态；TTL 分期避免了把 09-04 已否决的「过期声明被信任」问题以新形态带回。与既有原则（diff 优先、hint 非 gate、合入路径注意力预算、Role 非 ownership 边界）的兼容性检查全部通过。发现集中在索引层遗留（A1 半修复）与四处 Contract 定义级补充建议（M1/M2/L1–L3），均不阻塞 Proposed 评审。

## 2. 机械校验记录

| 校验 | 结果 |
|---|---|
| `check_governance_surface.py --check`（v0.3 合入后复跑） | S1–S11 + S5x 全绿 |
| AGENTS.md / CLAUDE.md | #853 后未被触碰（`git log -1` 确认），63 行/3815B 不变；附录 A 探针 Q1 对照标题受 S9 白名单保护 |
| #861 索引同步 diff | 仅 M7 里程碑行 v0.1→v0.3 一处；主清单表未动 |
| `execution-contract.md` | 尚不存在（P0 产物，与分期一致） |

## 3. 上轮审计发现处置状态（ADR-0034 相关）

| 项 | 状态 |
|---|---|
| A1 adr/README 索引 | **半修复**：M7 行版本已同步（#861，commit message 自认 v0.2/v0.3「修订时漏更」）；**主表仍缺 ADR-0034 行**（`docs/adr/README.md:84` 表止于 ADR-0033）。先例 ADR-0011（Proposed）在主表 → Proposed 即入表是既有惯例，此半未闭环 |
| A2 DOC-MAP | 未动：头部「最后更新 2026-09-03」过期；ADR-0034 在 hub/DOC-MAP 零索引引用 |
| B3 §1「单人单 Harness」定性句（v0.3 仍为 `:17` 原文） | 未动，仍建议校准为「同引擎多会话 → 多 Harness 引擎 × 执行层语义缺位」 |
| B4 §2.7 P0「五处 canonical」措辞 | 未动，且 v0.3 新增 §2.10 后矛盾加剧 → 见新发现 L2 |
| B1/B2/B5/B6（S11 代码锚定边界 / required checks 双份 / 迁移指针遗失 / G2 根规则行同步） | 未涉及本文，观察项保持 |

## 4. v0.3 六项必修逐项核验（全部通过）

1. **registry root = `$(git rev-parse --git-common-dir)/ai-work/`**（§2.2）：所有 worktree 解析同一位置、`.git` 内天然免跟踪、flock + same-dir temp + 原子 rename + 「仅本地 FS」约束自洽；正确替代 v0.2「主 checkout 固定绝对路径」（主 checkout 本身也是 worktree 之一，无主从可言）。
2. **liveness × integration 两维正交、overlap = integration ∈ {NO_PR, PR_OPEN, READY}**（§2.3）：修正正确且有反例论证（finish→STALE 后 PR 仍改 `foo.py`，新 Execution 必须仍见 overlap）。v0.2「仅 ACTIVE 参与」确为语义错误。
3. **STALE 永远 advisory**（§2.3）：不改变业务语义、不退出集成窗口——与「声明式 CLI 无可靠心跳源」论证呼应，未把过期状态写回语义层。
4. **TTL 分期**（§2.5）：P1 advisory（仅 status 提示、不改字段、不剔除）、P2 heartbeat 升格——克制且理由充分。
5. **MERGED 只能由 GitHub PR 状态确认** + 事实来源分层表（§2.3）：Registry/Git/GitHub/CI 四层权威各司其职；`finish(PR #N)` 语义收窄为「停止编码且已开 PR」，`ai_work` 不得单方面写终态。
6. **原子写协议固化**（§2.2）：flock → same-dir temp → fsync → 原子 rename，正确。

## 5. 三项 Contract 完善核验（通过）

- **§2.10 execution-contract.md 单一权威源**：与 Phase -1「事实单源 + 入口最小引用」一致；「AGENTS.md 不空壳化、不复制执行语义」边界句必要。
- **§2.9 test_impact × coverage-mismatch**：把测试证据纳入并行执行模型，是 #855 语义缺口族的机器可执行回应，方向正确（CI 证据层级问题见 M2）。
- **§2.8 Scope MVP 边界**：repo-relative file/directory、明确禁 glob/ownership/自动拆分/semantic/locking、Role 非 ownership——与 09-04 约定第 2 条及注意力预算主原则一脉相承。

§4 Alternatives 新增两行否决记录（overlap 仅 ACTIVE / P1 即硬 TTL）、§5 Verification 逐期对齐、draft note v0.3 修订段与 commit 六必修三完善一一对应——记录纪律无退化。

## 6. v0.3 新发现

| # | 级别 | 发现 | 建议 |
|---|---|---|---|
| M1 | 中-低 | **ABANDONED 写路径未定义**：CLI 仅 `declare/status/update/finish`（§2.2），§2.3 终态集合含 `ABANDONED` 但无命令产生它（MERGED 已绑定 update+GitHub 核对；「编码中途放弃、无 PR」路径更无着落） | Contract 补 `finish --abandon` 或独立子命令；ABANDONED 可由人工在任何 integration 态裁决 |
| M2 | 中-低 | **coverage-mismatch 的「CI 证据」层级未定义**（§2.9）：本项目 PR 路径有意不含 backend tests（~2min 合入注意力预算），若按 PR CI 解释，backend `direct` 改动近乎全量 mismatch → advisory 上线即噪声 | Contract 明示证据层级：夜间全量/合并后 run 记录聚合，而非 PR 轻量 checks；与 merge-path-attention-budget 原则联动写死 |
| L1 | 低 | **P1 的 STALE 是否落库未对齐**：§2.3 liveness 定义读作字段写入，§2.5 P1 读作派生提示 | 显式一句：P1 STALE = status 派生视图不持久化；P2 heartbeat 就位后 `update` 方可写 STALE 字段 |
| L2 | 低 | **§2.7 P0「五处 canonical」与 §2.10 四入口图不一致**（B4 加剧）：`§2.10` 图为 contract ← AGENTS/CLAUDE/.cursor/.codex 四入口；`§2.7 P0` 仍写「…/docs 五处 canonical」（§5 同源「五处最小引用接线」）。docs 既非入口、单一权威源下也不该 canonical | P0 行统一为 §2.10 口径 |
| L3 | 低 | **contract 建立后 ADR 细则段的处置未写明**：§2.10 说细则演进「不回填 ADR 正文」，但 §2.2-2.5/2.8/2.9 细则现全在 ADR 正文——平移后两者并存、再演进只入 contract → ADR 细则段成为陈旧副本（违反「文档只写现状」） | P0 明示：细则平移入 contract 后正文收缩为决策/理由 + 指针，或标注快照性 |
| L4 | 低 | **P1 实现注意**：`git rev-parse --git-common-dir` 输出可能相对 cwd（linked worktree 与主 checkout 表现不同） | P1 验证含「子目录 cwd + linked worktree」双场景解析一致性；实现建议 `--path-format=absolute`（git ≥2.31）归一化 |

## 7. 建议的 Accepted 前处理清单

1. 补 ADR-0034 主表行 + DOC-MAP 头部日期（A1 半开 + A2，一行改动级）；
2. M1「ABANDONED 写路径」进 §2.3/§2.7 P1（防 P1 实现期自由解释）；
3. M2「CI 证据层级」在 §2.9 写死（与 2min 预算原则联动，P3 免返工）；
4. L1–L4 措辞级，可随下版或 P0 PR 顺带。

## 8. 审计边界

- PR CI 六项 required 未重跑（合入时全绿）；
- Cursor 2026.09.02 本机无 CLI，附录 A 该行未复验（与基线 note 声明一致）；
- 外部基线（deepseek-harness `d347e7039`）事实未逐一远端复验（study note 已记录两轮人工修正与采样 commit）；
- 本审查为单 agent 产出；如需按仓库 2026-08-26 多稿交叉惯例，可另起独立审查轮核验。
