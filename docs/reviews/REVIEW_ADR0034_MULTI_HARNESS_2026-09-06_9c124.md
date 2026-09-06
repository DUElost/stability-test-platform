# ADR-0034 多 Harness 执行契约只读审查（v0.3 轮 + v0.4 终态核验）

- 日期：2026-09-06
- 审查类型：只读系统性审查与审计（未修改任何审查对象文件）
- 审查对象：ADR-0034 `docs/adr/ADR-0034-multi-harness-execution-contract.md`——第 1 轮 v0.3（commit `864da462`）、第 2 轮 v0.4（commits `a4d2d24e` + `25e9e6c7`）
- resume：`9c124`（本会话编号后六位）
- 本文件为八源多 Harness 审查源之一；**R 编号唯一权威映射与处置见 [synthesis](REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)**；第 1 轮记录为当时快照（其后处置以 synthesis 与 v0.4 终稿为准），第 2 轮为本会话对 v0.4 终态的独立核验

---

# 第 1 轮：v0.3 只读审查（快照）

## 1. 结论摘要

**v0.3 无 A 级缺陷、无阻塞项**。六项必修修正方向全部正确：两维状态模型修掉了 v0.2「仅 ACTIVE 参与 overlap」的真实语义错误；MERGED 绑定 GitHub PR 状态消灭「自报合入」虚假终态；TTL 分期避免了把 09-04 已否决的「过期声明被信任」问题以新形态带回。与既有原则（diff 优先、hint 非 gate、合入路径注意力预算、Role 非 ownership 边界）的兼容性检查全部通过。发现集中在索引层遗留（A1 半修复）与四处 Contract 定义级补充建议（M1/M2/L1–L3），均不阻塞 Proposed 评审。

## 2. 机械校验记录

| 校验 | 结果 |
|---|---|
| `check_governance_surface.py --check`（v0.3 合入后复跑） | S1–S11 + S5x 全绿 |
| AGENTS.md / CLAUDE.md | #853 后未被触碰（`git log -1` 确认），63 行/3815B 不变；附录 A 探针 Q1 对照标题受 S9 白名单保护 |
| #861 索引同步 diff | 仅 M7 里程碑行 v0.1→v0.3 一处；主清单表未动 |
| `execution-contract.md` | 尚不存在（P0 产物，与分期一致） |

## 3. v0.2 轮审计发现处置状态（ADR-0034 相关）

| 项 | 状态（第 1 轮时点） |
|---|---|
| A1 adr/README 索引 | **半修复**：M7 行版本已同步（#861）；主表仍缺 ADR-0034 行。→ **v0.4 已补主表行（见第 2 轮 N1 的版本残留）** |
| A2 DOC-MAP | 未动：日期 09-03 过期、零索引引用。→ **v0.4 已修（日期 09-06 + ADR-0034 行）** |
| B3 §1「单人单 Harness」定性句 | 未动 → **v0.4 已校准为「同引擎多会话」** |
| B4 §2.7 P0「五处 canonical」措辞 | 未动 → **v0.4 已统一为单一 canonical + 薄入口（R8）** |

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

| # | 级别 | 发现 | 处置（R 映射） |
|---|---|---|---|
| M1 | 中-低 | **ABANDONED 写路径未定义**：CLI 仅 `declare/status/update/finish`，§2.3 终态集合含 `ABANDONED` 但无命令产生它 | **R2 采纳**：ABANDONED 仅显式 `finish --abandon`；CLOSED 入 integration |
| M2 | 中-低 | **coverage-mismatch 的「CI 证据」层级未定义**：PR 路径有意不含全量测试，`direct` 声明将常态误报 | **R13 采纳**：证据口径=夜间全量/合并后记录 |
| L1 | 低 | **P1 的 STALE 是否落库未对齐**：§2.3 读作字段写入、§2.5 读作派生提示 | **R3 采纳**：liveness 派生不持久化，持久层只存 `last_seen` |
| L2 | 低 | **§2.7 P0「五处 canonical」与 §2.10 四入口图不一致** | **R8 采纳**：单一 canonical Contract + 明确列举薄入口 |
| L3 | 低 | **contract 建立后 ADR 细则段的处置未写明**（双份漂移风险） | **R9 采纳**：P0 细则一次性平移，ADR §2 收缩为要点+指针 |
| L4 | 低 | **P1 实现注意**：`--git-common-dir` 输出可能相对 cwd | **R1 采纳**：`--path-format=absolute` 为唯一发现方式 |

## 7. 审计边界（第 1 轮）

- PR CI 六项 required 未重跑（合入时全绿）；
- Cursor 2026.09.02 本机无 CLI，附录 A 该行未复验（与基线 note 声明一致）；
- 外部基线（deepseek-harness `d347e7039`）事实未逐一远端复验（study note 已记录两轮人工修正与采样 commit）。

---

# 第 2 轮：v0.4 终态核验（八源 synthesis + 人工裁决后）

## A. 总体结论

**21 项采纳全部忠实落地，无一项走样或半落地**；R6/R18 裁决与 ADR 文本一致；第 1 轮全部发现（M1/M2/L1–L4、A1/A2、B3/B4）在 R 映射中闭环。八源审查→synthesis→人工裁决→v0.4 的流程是仓库迄今质量最高的一轮：R 编号唯一权威、裁决理由带证据源计数、未采纳项有明确理由。**无 A/B 级新缺陷；仅 1 处索引级残留（N1）+ 3 处措辞级观察（N2–N4），均不阻塞。**

## B. 21 项采纳逐条落地核验（全部通过）

| 组 | R | 终稿落点核验 |
|---|---|---|
| Registry 定位 | R1 | §2.2 `--path-format=absolute` 唯一发现方式 + 删除替代落点 + 按克隆隔离；§5 P1 三位置解析样例 ✓ |
| 状态模型 | R2 | §2.3 READY 派生刷新+回退 PR_OPEN、CLOSED 入 integration、ABANDONED 仅显式人工、僵尸候选清单、transition table 入 P0 contract ✓ |
| | R3/R4 | §2.3 liveness 派生不持久化；§2.5 status 严格只读 ✓ |
| | R5 | §2.2 overlap = `declared ∪ derived(diff)`、冲突以 derived 为准、09-04 反例内嵌；§5 P1 fixture ✓ |
| | R6 | §2.3 裁决背景句（两维确认 + lifecycle/finished_at 二选一 + 选 lifecycle）+ finish 只写 lifecycle ✓ |
| | R7 | §2.2 九步全序含父目录 fsync，异常/恢复下沉 contract ✓ |
| 权威源 | R8–R11 | §2.7 P0 单一 canonical + 四薄入口、细则一次性平移、repository-workflow.md 补入、S2+S6 入列 ✓ |
| test_impact | R12/R13 | §2.9 缺省=indirect+提示；证据口径=夜间全量/合并后记录 ✓ |
| 同步族 | R14/R15 | README 主表行、DOC-MAP 日期+行、governance S1–S11、note 锚 v0.4、§1 归属拆分 ✓（残留见 N1） |
| Scope/G2/触发 | R16–R22 | §2.8 拒绝规则+组件边界谓词；§3 symlink 方向+写入防护+试点路径+双边验收+P0 后接；§5 #855 三段触发；§2.6 正面回应；P1 启动判据 ✓ |
| R18 | Competition | §6 Revisit 已裁决条：不入 Contract、降非阻断 ✓ |

## C. R6/R18 人工裁决一致性核验（通过）

- **R6**：§2.3 裁决背景句完整记载「Contract v1=两维（用户确认）→ B3 收窄为 finish 持久化表达 → lifecycle/finished_at 二选一 → 选 lifecycle」——裁决链无断点；「三维是实现选择而非冻结条款」限定句在场。
- **R18**：Revisit 已裁决条（冻结版无此条款、overlap=hint 已隐含允许并行）——不留死条款、不丢观察位。
- 9261bd 源文件已随裁决修订（88 行），synthesis 裁决栏同步「裁决后补记」——证据链闭合。

## D. v0.4 新发现

| # | 级别 | 发现 | 建议 |
|---|---|---|---|
| N1 | 低 | **README 两行版本不同步（A1 缺陷类复发）**：`docs/adr/README.md:85` 主表行 v0.4 ✓、`:97` M7 看板行仍写 **v0.3**（#862 加主表行时漏同步） | 一行修复；缺陷类三次出现（v0.2/v0.3 → #861；v0.4 看板行），可作 S2/S5x 类扩展候选（登记 #854 门禁缺口候选），不必现在做 |
| N2 | 低 | **术语撞名 `lifecycle`**：与既有硬不变量「Pipeline 顶层只接受 lifecycle」（pipeline_def 域，S11 锚定）同词异域 | contract.md 术语节显式消歧（如 `exec.lifecycle`），不必改 ADR 裁决 |
| N3 | 低 | §2.3:56 僵尸句「lifecycle 非终态」——若读成「≠FINISHED」会漏「finish 后忘 abandon」僵尸 | P0 平移时精确化为 `lifecycle ∈ {CODING, FINISHED}` |
| N4 | 低 | §2.3:50/53「三个正交字段」与 liveness 不持久化同句并存（liveness 为派生展示值） | 「字段」改「维度」；contract 数据模型节明确持久字段 = {scope, role, PR 号, lifecycle, last_seen} |

## E. 机械校验（第 2 轮）

- `check_governance_surface.py --check`：#862/#863 后复跑 S1–S11+S5x 全绿（DOC-MAP 新行链接解析通过）；
- 残留 grep：无 v0.2/v0.3 状态/命名/版本残留（唯一 ACTIVE 命中在 Alternatives 历史否决行，合规）；
- README 主表 + DOC-MAP + governance 文档三处 R14 落点实读确认。

## F. 结论与建议（第 2 轮）

v0.4 具备 **Accepted 候选质量**：模型完成了从「方向正确」到「可实现」的收口（写入权威、派生规则、拒绝规则、缺省策略、证据口径全部显式化），每项修订可追溯到带证据源的 R 裁决。Accepted 前只需处理 N1（一行版本同步）；N2–N4 是 P0 contract 平移时的措辞精确化项，不阻塞本版。

## G. 审计边界（第 2 轮）

- PR CI 六项 required 未重跑（合入时全绿）；
- 本核验为八源 synthesis 之后的独立终态复核，未重复八源已做证据采集，聚焦落地忠实度与残留；
- Cursor 2026.09.02 本机无 CLI（附录 A 行未复验，与基线声明一致）。
