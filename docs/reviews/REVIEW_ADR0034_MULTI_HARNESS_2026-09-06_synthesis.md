# ADR-0034 v0.3 多 Harness 审查综合裁决（synthesis）

- **日期**：2026-09-06
- **性质**：八份独立只读审查的权威综合（playbook 2026-08-26 模式）；本文件编号为唯一权威映射，后续引用一律用 R 编号
- **审查源**（8 份，同目录）：`_0cd302`、`_2873a2`、`_4f6e4b`、`_6d0f05`、`_9261bd`、`_9c124`、`_aa114c`、`_d4a277`
- **总评谱系**：1 份建议直接 Accepted（aa114c）；多数「修完关键项再 Accepted」；9261bd 列 3 阻断。**共识：架构方向无异议（8/8 通过状态模型按集成窗口判定、事实分层、TTL 分期、不建 merge queue、Registry 非调度器）；分歧集中在契约定义完备度。**
- **处置**：采纳 21 项 → ADR v0.4（PR 同批）；**R6/R18 已由用户 2026-09-06 晚裁决**（见各条目）；范围外遗留 3 项（附录 B）。
- **裁决后补记（2026-09-06）**：9261bd 经用户修订——①冻结版 Contract v1 确认为**两维**（Execution × Integration），三维模型为评审建议、未获显式确认；②B3 重定义为「finish 缺少独立可持久化语义表达」，lifecycle 字段与 `finished_at` 字段二选一皆合规（v0.4 采用 lifecycle，兼载 ABANDONED 放弃语义）；③Competition 降为非阻断追溯建议。本表 R6/R18 裁决栏已按此更新。

## 1. 裁决总表（R 编号 → v0.4 落点）

| R | 发现（共振源数） | 裁决 | v0.4 落点 |
|---|---|---|---|
| R1 | `--git-common-dir` 返回 cwd 相对路径、主 checkout/linked worktree 行为不一致（**6 源**：2873a2-H1、9261bd-B1、9c124-L4、aa114c-建议4、4f6e4b-B4、d4a277-必修2，多源独立实测一致） | **采纳** | §2.2：固定 `--path-format=absolute`（git≥2.31）为**唯一发现方式**；删除「common dir 外替代路径」句（与冻结版唯一性冲突且重引 NFS/CIFS 风险）；§5 P1 验收加三位置解析一致样例 |
| R2 | READY/ABANDONED/CLOSED 写入权威未定义；`READY→MERGED` 无执行者、overlap 集合只增不减（**7 源**：0cd302-4.1①②、2873a2-H2、4f6e4b-A1、9c124-M1、aa114c-建议1、6d0f05-N1、9261bd-M1/M2） | **采纳** | §2.3：READY 由 `update` 依 GitHub checks **派生刷新**（事实刷新非自声明，主干推进重跑则回退 PR_OPEN）；CLOSED=PR 关闭未合（GitHub 事实）；ABANDONED 仅显式人工动作（`finish --abandon`），永不自动产生；`status` 给出僵尸候选清单；transition table/reconcile 入 P0 contract 必备目录 |
| R3 | STALE「持久字段」vs「派生提示」矛盾（§2.3 vs §2.5）（**5 源**：0cd302-4.1③、4f6e4b-A2、9261bd-H4、9c124-L1、aa114c-建议3） | **采纳** | §2.3/§2.5 统一：**持久层只存 `last_seen`；liveness 值（LIVE/STALE）为查询时派生**，P1 不回写、P2 heartbeat 后仍由派生或唯一回写者，永不影响 integration risk |
| R4 | `status` 刷新 `last_seen` = 观察行为改变被观察状态（9261bd-H3，单源但独立成立） | **采纳** | 并入 R2/R3：`status` 严格只读；仅携带 execution identity 的写命令（update/finish/declare）可刷新自身 `last_seen` |
| R5 | overlap 检测未绑定 git diff——「diff 优先」只写在 §2.4 未接线（d4a277-必修1，单源但带 2026-09-04 真实反例：`docs/drift-sync-*` 声明 `docs` 实际触及 `backend/`、`.github/`） | **采纳** | §2.2：**overlap 参与集合不变（§2.3），但参与者 scope 数据 = `declared ∪ derived(diff)`、冲突以 derived 为准**；声明仅在零 diff 时单独生效；§5 P1 加「声明≠diff」fixture |
| R6 | 两维模型下 `finish` 缺少独立可持久化语义表达（9261bd-B3 修订版；原报告的「三维=冻结条款」表述经用户修订撤回——冻结版确认为两维，三维为评审建议） | **采纳（lifecycle 实现选择）**，已裁决 | §2.3：以**独立 lifecycle 字段**为 finish 提供持久化表达（修订后 B3 允许 lifecycle 或 `finished_at` 二选一，v0.4 选前者兼载 ABANDONED 放弃语义）；`finish` 只写 lifecycle（解耦 PR 先后）；overlap = lifecycle 非终态 且 integration∈{NO_PR,PR_OPEN,READY}；**三维为实现选择而非冻结条款**（ADR §2.3 裁决背景句已注明） |
| R7 | 原子写协议不全：缺 validate 与 parent-directory fsync（9261bd-B2，引冻结版） | **采纳** | §2.2 固化九步全序为硬约束（异常处理/残留清理/损坏恢复下沉 contract.md） |
| R8 | 「五处 canonical」与「唯一权威源」冲突（**4 源**：2873a2-M4、9261bd-H5、4f6e4b-B6①、9c124-L2） | **采纳** | §2.7 P0/§5 统一为「**单一 canonical Contract + 明确列举薄入口**（AGENTS.md/CLAUDE.md/.cursor/rules/.codex）」 |
| R9 | ADR §2 细则与 contract.md 双份漂移；存量内联规范去向未定（**4 源**：0cd302-4.2⑧、4f6e4b-A3、6d0f05-N2、9c124-L3） | **采纳** | §2.10/P0：建 contract 时**一次性平移细则**，ADR §2 收缩为决策要点+指针（否则 ADR 变第二契约源，违反自设规矩） |
| R10 | P0 接线清单漏 `repository-workflow.md`（**2 源**：2873a2-M1、6d0f05-B1；实核 AGENTS.md:39 直接指向它且两处写「以 09-04 为准」） | **采纳** | §2.7 P0 补 |
| R11 | `execution-contract.md` 未入 S2 link_files / S6 预算（2873a2-M2；G2 有 checker 同步条目而 P0 无，不对称） | **采纳** | §2.7 P0 补「入 S2 + S6」 |
| R12 | test_impact P1 强制有摩擦；字段 P1 落 P3 才消费、噪音积累（2 源：0cd302-4.3⑦、2873a2-M3） | **部分采纳** | §2.9：P1 **允许缺省（缺省=indirect 并提示）**；否决「推迟到 P3」（保留 P1 起的历史采集，P3 只统计显式声明） |
| R13 | coverage-mismatch 的 CI 证据层级未定义——PR checks 不含全量测试，`direct` 声明将常态误报（**3 源**：4f6e4b-B2、9c124-M2、aa114c-建议2） | **采纳** | §2.9 写死：证据口径 = **夜间全量/合并后 CI 运行记录**（非 PR 轻量 checks），与 ~2min 合入路径注意力预算原则联动 |
| R14 | 文档同步族：README 主表缺行（5 源）、DOC-MAP 无 0034、governance 文档 :29 仍 S1–S10、起草 note 开篇锚 v0.1、P0「现 63 行」快照、「Phase 1」术语、`aee/` 路径（0cd302、9261bd-M4、9c124-A1/A2、aa114c-建议5、6d0f05-A4；均已实核） | **采纳** | 本 PR 一并修：README 主表补行、DOC-MAP 日期+引用、governance :29 → S1–S11、note 锚 v0.4、ADR 措辞三处 |
| R15 | ADR:20「S1-S11 归 PR #853」归因串位——S11 由 #856 引入（6d0f05-A3；实核成立） | **采纳** | §1 修正为 S1–S10 归 #853、S11 归 #856 |
| R16 | drift 比对对 Agent Note 等强制随附物的豁免未留痕（6d0f05-A5） | **采纳（轻）** | P0 行加半句：豁免规则入 contract 初稿 |
| R17 | Scope 缺拒绝规则：absolute path / `..` / symlink escape / 组件边界（9261bd-H1） | **采纳** | §2.8 补明列；P0 contract 定义基于路径组件边界的 overlap 谓词 |
| R18 | Competition mode（显式受审计竞争）未入 ADR（9261bd-H2 原版；但 09-05 方案与其余 7 源均未提及） | **已裁决：不入 Contract** | 用户 2026-09-06 裁决：冻结版不含此条款，降为**非阻断追溯建议**——ADR Revisit 留观察（overlap=hint+不上锁已隐含允许并行），真实竞争需求出现再议，不为不存在的条款加复杂度 |
| R19 | G2 补充族：symlink 写入方向与防护、验收含根契约同时可见（#857 下根 @import 不解析）、试点路径写全、P0 后接 G2（4 源：9261bd-M3、4f6e4b-B6②、0cd302-4.2⑥、d4a277-3.3） | **采纳** | §3：方向=CLAUDE.md→AGENTS.md symlink、写入防护句；验收口径加「根 bootstrap（总原则/硬不变量）与 scoped 内容同时可见」，P2 Adapter 明确根契约供给；试点 `backend/agent/`→`backend/agent/aee/`；P0 合入后接 G2 试点 |
| R20 | #855 触发语义三段未分（9261bd-M5） | **采纳** | §5：Git merge=可引用 / Accepted=方向生效 / P0 完成=#855 补全开工 |
| R21 | 与 #847「不为 N=2 引入 WIP 公告」缺显式对账；Alternatives 首条理由不准确（派生视图实已覆盖本机全部 worktree，真实缺口=双方均零 diff 时）；审阅瓶颈原结论未正面回应（d4a277-3.1/3.2、2873a2-R2） | **采纳** | §1/取代对象处补对账半句（工具媒介自动执行 vs 手工仪式、advisory、N 已跨 Harness）；§4 首条理由改窄口径；§2.6 补「审阅瓶颈未变，Registry 提升的是审计面信息完备性而非审阅吞吐，任务排队仍是主策略」 |
| R22 | P1 缺启动判据（P4 有而 P1 无，v0.3 加重失衡）（2873a2-R1） | **采纳** | §2.7 P1 备注列触发判据（如「连续两周并行 worktree ≥3」或「发生 ≥2 次跨 Harness 撞车返工」） |

## 2. 不采纳/缓办项

- **章节重排（4f6e4b-B1）**：§2.8-2.10 前向引用问题随 R9 的 P0 平移自然消解，不单独重排（避免大段移动引入错位）。
- **execution-contract.md 现不存在**（0cd302 §5）：非缺陷，P0 产物，分期使然。
- **「声明仪式过重」总体担忧（0cd302 §6）**：以 R12（缺省策略）+ 既有 fail-open 原则回应，不再降级。
- **§1「单人单 Harness」定性句校准（9c124-B3）**：并入 v0.4 顺手改（「同引擎多会话 → 多 Harness 引擎 × 执行层语义缺位」）。

## 3. 范围外遗留（建议另立 docs PR，不随本批）

1. `docs/design/2026-08-step-stall-detection.md:88-89` 两层钟 schema 门过期句（6d0f05-A1）；
2. `effective_slots` 容量公式无常驻文档出处（6d0f05-A2）；
3. `production-diagnostics.md` 缺「backend/.env AGENT_SECRET 陈旧值」具体告警（6d0f05-B5）。

## 4. 验证

- 八源证据抽查：R1/R5 的 git 实测与 09-04 反例、R14/R15 的四处行号、R10 的 repository-workflow 指针——均已对本仓实核成立；
- v0.4 修订后 `check_governance_surface.py --check` 全绿（S10 对本文件不适用，reviews/ 非 note class 目录）；
- ADR 版本链：v0.1(#858) → v0.2(#859) → v0.3(#860) → **v0.4(本 PR)**。
