# ADR-0034 多 Harness 执行契约只读审查（v0.3→v0.5 四轮 + 完结确认与批次首单）

- 日期：2026-09-06 → 2026-09-07
- 审查类型：只读系统性审查与审计（未修改任何审查对象文件）
- 审查对象：ADR-0034 `docs/adr/ADR-0034-multi-harness-execution-contract.md`（v0.2→v0.5 演进）及其配套（synthesis/契约文档/工具链）
- resume：`9c124`（本会话编号后六位）
- 本文件为该会话对 ADR-0034 全生命周期的累积审查记录：第 1 轮 v0.3（快照）→ 第 2 轮 v0.4 终态核验 → 第 3 轮 v0.5 复审审查 → 第 4 轮完结确认与批次首单（#880）只读确认
- 本文件为八源多 Harness 审查源之一；**R 编号唯一权威映射与处置见 [synthesis](REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)**；第 1 轮记录为当时快照，处置以 synthesis 与后续版本为准

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
| A1 adr/README 索引 | **半修复**：M7 行版本已同步（#861）；主表仍缺 ADR-0034 行。→ 后续 v0.4 已补主表行 |
| A2 DOC-MAP | 未动：日期 09-03 过期、零索引引用。→ v0.4 已修（日期 09-06 + ADR-0034 行） |
| B3 §1「单人单 Harness」定性句 | 未动 → v0.4 已校准为「同引擎多会话」 |
| B4 §2.7 P0「五处 canonical」措辞 | 未动 → v0.4 已统一为单一 canonical + 薄入口（R8） |

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

§4 Alternatives 新增两行否决记录、§5 Verification 逐期对齐、draft note v0.3 修订段与 commit 六必修三完善一一对应——记录纪律无退化。

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

- PR CI 六项 required 未重跑（合入时全绿）；Cursor 2026.09.02 本机无 CLI（附录 A 行未复验）；外部基线（deepseek-harness）事实未逐一远端复验。

---

# 第 2 轮：v0.4 终态核验（八源 synthesis + 人工裁决后）

## A. 总体结论

**21 项采纳全部忠实落地，无一项走样或半落地**；R6/R18 裁决与 ADR 文本一致；第 1 轮全部发现（M1/M2/L1–L4、A1/A2、B3/B4）在 R 映射中闭环。八源审查→synthesis→人工裁决→v0.4 的流程是仓库迄今质量最高的一轮。**无 A/B 级新缺陷；仅 1 处索引级残留（N1）+ 3 处措辞级观察（N2–N4），均不阻塞。**

## B. 21 项采纳逐条落地核验（全部通过）

R1（absolute 唯一发现方式+按克隆隔离）✓ R2（READY 派生刷新/CLOSED/ABANDONED 显式人工/僵尸清单）✓ R3/R4（liveness 派生不持久化、status 只读）✓ R5（overlap=declared∪derived）✓ R6（裁决背景句+lifecycle 实现选择）✓ R7（九步原子写含父目录 fsync）✓ R8–R11（单一 canonical+四薄入口/细则平移/repository-workflow 补入/S2+S6）✓ R12/R13（test_impact 缺省、CI 证据口径=夜间全量）✓ R14/R15（索引同步族、§1 归属拆分）✓ R16–R22（scope 拒绝规则/G2 symlink 方向+#857/三段触发/审阅瓶颈正面回应/P1 启动判据）✓ R18（Competition 已裁决不入 Contract）✓

## C. R6/R18 人工裁决一致性核验（通过）

- R6：§2.3 裁决背景句记载完整（两维确认 → B3 收窄 → lifecycle/finished_at 二选一 → 选 lifecycle）；「三维是实现选择而非冻结条款」限定句在场。
- R18：Revisit 已裁决条；9261bd 源文件随裁决修订（88 行）、synthesis 裁决栏同步——证据链闭合。

## D. v0.4 新发现

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| N1 | 低 | README :85 主表 v0.4 vs :97 M7 行 v0.3（A1 缺陷类复发） | R27 采纳（v0.5 修）——commit 自认「同族缺陷第三次复发」 |
| N2 | 低 | 术语撞名 `lifecycle`（与 pipeline_def 域 S11 锚定词） | R27 → P0 契约消歧注记 |
| N3 | 低 | 僵尸句「lifecycle 非终态」歧义 | R27 → 精确化为 `{CODING, FINISHED}` |
| N4 | 低 | 「三个正交字段」vs liveness 派生不持久化 | R27 → P0 持久字段清单（liveness 不在其中） |

## E. 机械校验（第 2 轮）

`check_governance_surface.py --check` 全绿；残留 grep 无 v0.2/v0.3 残留；R14 三处落点实读确认。

## F. 结论与建议（第 2 轮）

v0.4 具备 Accepted 候选质量；Accepted 前只需 N1 一行修复，N2–N4 归 P0 平移措辞项。

---

# 第 3 轮：v0.5 只读审查（第二轮八源复审后，2026-09-06 夜）

## A. 总体结论

**R23–R30 八项采纳全部忠实落地**；第 2 轮 N1–N4 全部经 R27 闭环。v0.5 修的 R23（ABANDONED×PR_OPEN 悬空泄漏）与 R24（effective scope 两套互斥定义）是真实且重要的洞——第一轮 8 源与 v0.4 核验均漏，第二轮复审修正后模型更闭合。**仍无 A/B 级缺陷；3 处低发现（F1–F3）。**

## B. R23–R30 落地核验（全部通过）

R23：§2.3 真值表三子句逐格验证 2×5 全组合自洽（开放 PR 恒在窗/NO_PR 看 lifecycle/CLOSED×非 ABANDONED 在窗/MERGED 恒出局/abandon 有 PR 警告留窗）✓
R24：effective scope 并集恒成立+drift 提示+`update` 可覆写声明；互斥表述删除；untracked 口径 ✓
R25：derived 三分档（worktree→branch→声明）✓
R26：#847 对账删①，derived 优先正面化解「手写状态会过期」✓
R27：README 双行 v0.5、旧术语、§2.10 图 `.cursor/rules`、symlink 措辞修正（消除漂移≠写保护——技术正确）、Role Context 归属、lifecycle 消歧、持久字段清单、判据数据源、Antigravity 未验证注记 ✓
R28/R29/R30：P0 升 v1.1 口径、P0a/P0b 拆分、supersede 过渡条款 ✓

## C. v0.5 新发现与后续处置

| # | 级别 | 发现 | 后续处置（2026-09-07 核验） |
|---|---|---|---|
| F1 | 低-中 | derived tier-1「工作树 diff」若按互斥档读会漏 committed diff——重开 R5/R24 堵的洞 | **已解**：契约 v1.1 §5.2 锚定「tracked staged/unstaged（对 merge-base）」+ 增 `branch` 持久字段（§1.2）供第二档；过渡条款 §契约 163 保留派生视图口径 |
| F2 | 低 | §5 P0 验证行与 §2.7 P0 行不同步（缺必备目录扩列/v1.1 升版/P0a-P0b） | v1.1 §2 收缩后 ADR 为决策要点记录、细则权威在契约 → 低风险残留，未逐一复核 |
| F3 | 低 | DOC-MAP 版本残留（v0.4）——同族缺陷第 4 次 | **未修（持续）**：至 09-07 完结核验时 README 双行 v1.0、DOC-MAP v1.1，均滞后于 ADR v1.2——同族第 5/6 次；机械门禁建议仍未立项 |

## D. 结论与建议（第 3 轮）

v0.5 维持 Accepted 候选质量；F1 建议 Accepted 前补一句（tier-1 显式含 committed diff），F2 一行同步，F3 建议开机械门禁 issue。

---

# 第 4 轮：完结确认与批次首单只读确认（2026-09-07）

## A. ADR-0034 开发工作完结确认（结论：主体完结 ✅）

| 期 | 状态 | 证据 |
|---|---|---|
| ADR 裁决链 | ✅ Accepted v1.2（v1.0 #865 用户终审 → v1.1 #866 细则迁出 → v1.2 #877 判据修订） | ADR 头版本记录完整 |
| P0a | ✅ execution-contract.md（Living v1.1，唯一权威源） | `docs/development/ai/execution-contract.md` 在场 |
| P0b | ✅ 接线/门禁 | AGENTS.md 双指针（65 行预算内）；checker S6/S2 切到 scoped AGENTS.md（:160/:437） |
| P1 | ✅ Registry MVP | `tools/dev/ai_work.py` 六子命令；`.git/ai-work/registry.yaml` 有活数据（实际使用中） |
| P2 + P2b | ✅ Adapter + 根 bootstrap | #879 + #892（wrapper 注入 + 加载矩阵终验） |
| P3 | ✅ drift gate（advisory） | #893「ADR-0034 分期最后一块」；#895 run_gates 对齐 |
| G2 试点 | ✅ 双侧完成 | `backend/agent/{,aee/}` 均 AGENTS.md 真身 + CLAUDE.md→AGENTS.md symlink |
| 取代标记 | ✅ | 09-04 note 头部「被 Accepted v1.0 取代」留档 |
| 门禁 | ✅ 全绿 | S1–S11+S5x 通过 |

**挂起项（均有意，不阻塞）**：#855（已解锁，P1 启动判据未触发故未开工；候选 b 获 G2 4/4 探针复用证据）、#857（上游 bug，仓库内已 P2b wrapper + G2 symlink 绕行）、P4（观察项）。
**仍开放小项**：索引版本残留（F3 同族第 5/6 次：README/DOC-MAP 停 v1.0/v1.1 vs ADR v1.2）；范围外 3 项（step-stall 过期句 / `effective_slots` 出处 / production-diagnostics AGENT_SECRET 告警）未另立 docs PR。

## B. 批次第一单（#880 修复 / PR #899 / 3e072f62）只读确认（结论：全部属实 ✅）

| 声明 | 核验证据 | 结果 |
|---|---|---|
| PR #899 合入 3e072f62、issue #880 自动 CLOSED | `git rev-parse HEAD`=3e072f62；gh pr=899 MERGED；gh issue 880 = CLOSED/COMPLETED | ✅ |
| main 工作树干净 | `git status --short` 空 | ✅ |
| 语义层 `normalize_requirement_id` REFUSED+改名指引 | ai_work.py:352-366（拒 `#` 开头含指引 `issue-878` slug、拒 `:`）；:371-373 exit 2 | ✅ |
| codec 对称（dump key `_quote`/`_quote` 去裸 `#`/load 引号 key） | :54/:69/:78/:117 | ✅ |
| 损坏隔离 corrupt-<时间戳>+恢复指引 | :207；自测实见 `registry.yaml.corrupt-20260907-140111` | ✅ |
| `load_locked` 无主 .tmp 清理 | note §Decision 4 + :253 | ✅ |
| CodeQL ReDoS 修复 / 重触发拉锯 | 独立 commit 6ce81f80 + db3d3276（check-run 与 workflow 结论脱节） | ✅ |
| 首版 corrupt 样例构造错误已修 | note :15 + 自测通过 | ✅ |
| Registry 两条完整生命周期 | 实读 registry.yaml：`fix-825-gates-parity`（PR #895）与 `fix-880-registry-codec`（PR #899），均 FINISHED + integration_cache=MERGED | ✅ |
| 自测全绿 | `ai_work.py --self-test` 实跑通过（scope/overlap/真值表/liveness/codec/原子写 红绿双向） | ✅ |
| #891 台账（10 项）在场 | gh issue 891 = OPEN「R01 总体架构与硬契约审查台账（2026-09-07：10 项）」 | ✅ |

## C. 第 4 轮结论

批次第一单交付报告每一条可核验声明均与仓库实态一致，无虚报；#880 在批次启动即暴露并修复工具链自身 P0——ADR v1.2「工具先于场景就绪」判据得到正面验证。唯一建议当下处理的开放项：索引三处版本号同步 v1.2 + 将「ADR 版本 × 索引互检」登记为门禁缺口（同族缺陷已复发 6 次，手工同步已被证伪）。
