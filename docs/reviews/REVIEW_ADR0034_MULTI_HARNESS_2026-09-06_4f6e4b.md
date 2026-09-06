# ADR-0034 多 Harness 执行契约只读审查（v0.3）

- **状态**：Living（审查快照；不代改被审文档）
- **日期**：2026-09-06
- **会话 resume**：`55fdb7c6-2fae-4644-aa24-ad70674f6e4b`（后缀 `4f6e4b`）
- **审查对象**：[`docs/adr/ADR-0034-multi-harness-execution-contract.md`](../adr/ADR-0034-multi-harness-execution-contract.md)（**Proposed v0.3**）
- **关联合入**：PR [#858](https://github.com/DUElost/stability-test-platform/pull/858)（v0.1）、[#859](https://github.com/DUElost/stability-test-platform/pull/859)（v0.2）、[#860](https://github.com/DUElost/stability-test-platform/pull/860)（v0.3 Contract hardening，commit `864da462`）、[#861](https://github.com/DUElost/stability-test-platform/pull/861)（README M7 → v0.3）
- **关联 Issue**：[#855](https://github.com/DUElost/stability-test-platform/issues/855)、[#857](https://github.com/DUElost/stability-test-platform/issues/857)、[#854](https://github.com/DUElost/stability-test-platform/issues/854)
- **并行审查**：同日已有两份 v0.3 只读审查——
  [`…_0cd302`](REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_0cd302.md)（正文内部问题 / 实施歧义角度）、
  [`…_9c124`](REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_9c124.md)（机械校验角度）。
  本文为独立第三份（契约空白 + 一致性角度），与前两份交集处显式交叉引用，不重复展开。

## 1. 结论摘要

v0.3 的「六项必修 + 三项完善」方向正确，实质性修复 v0.2 三处硬伤：状态模型两维
正交（overlap=集成窗口）修正「仅 ACTIVE 参与」的语义错误；registry root 落
`git-common-dir` 天然解决多 worktree 唯一性与免跟踪；MERGED 只认 GitHub 事实杜绝
registry 僭越。与引用基线（deepseek 对照 note、Phase -1 基线 note）及周边文档
（repository-workflow、harness-adapters、2026-09-04 note）一致性核对无冲突；附录 A
实测补上了 G2「先实测再裁决」的前置，G2 从条件采纳转正式裁决的闭环成立。

**技术内容可以接受**。但有 3 处契约定义层空白建议 Accepted 前钉死（见 §3）——
v0.3 自身精神是「不留给实现自由解释」（§2.8 标题），这几处恰恰留下解释空间。

## 2. 方法与范围

只读审查：`864da462`（v0.3）对 v0.2 的 diff + ADR 全文（§1–§6、附录 A）+ draft
note 增量（`2026-09-06-adr-0034-draft.md`）+ README M7 状态行 + 引用方一致性抽查
（AGENTS.md / repository-workflow.md / harness-adapters.md / 2026-09-04 note）。
未运行任何命令修改工作区。

## 3. Accepted 前建议钉死的契约空白

与前两份审查的重叠说明：**A1/A2 与 0cd302 §4.1-1/2/3 同题**，本审查从「事实来源
分层表自身不完整」角度复述一遍以保持自洽，不再另造编号；**A3 与 0cd302 §4.2-8
同题**（该文件从「ADR 变第二契约源」角度，本审查补迁移处置句）。

### A1. READY / ABANDONED 的判定者与写入者未定义（§2.3）

事实来源分层表只对 MERGED 特指「只能由 GitHub PR 状态确认」，并约束 `ai_work`
不得单方面写 MERGED；但 `PR_OPEN→READY` 由谁判定、由谁写入，`ABANDONED` 由谁
触发，均未规定。留白则 P1 各实现自行发明语义。建议补一句：READY 由
`ai_work update/status` 依据 GitHub PR 的 required-checks 状态派生刷新（事实刷新
非人工宣称）；ABANDONED 只能由显式人工动作触发，永不由超时/命令自动产生。

### A2. STALE 是「展示层派生」还是「持久字段」，§2.3 与 §2.5 口径打架

§2.3 写「超时只标 STALE 提示人工确认」；§2.5 P1 写「不自动改写任何字段」。若
STALE 落库则矛盾，若永不落库则 P1 的 liveness 字段只有 ACTIVE 一个持久值。
建议统一：**持久只存 `last_seen`；ACTIVE/STALE 为 status 展示层派生**（last_seen
+ TTL 计算），P2 heartbeat 就位后方可将 STALE 落为持久字段——与「last_seen 在
P1 不是 liveness 权威」自洽。

### A3. §2.10 分家后，ADR 正文 §2.1–2.9 存量内联规范去向未定

P0 建立 `execution-contract.md` 后，ADR §2 内联的完整规范与 contract.md 形成双份
事实；ADR 在本仓库是 living doc（v0.2→v0.3 即实例），「细则不回填 ADR 正文」只
管未来、管不住已内联部分。建议 P0 行补迁移处置：迁移时 ADR §2 收敛为决策要点 +
指向 execution-contract.md，或显式标注「§2 为 Accepted 时快照，权威以
execution-contract.md 为准」。（0cd302 §4.2-8 同题，方向一致。）

## 4. 建议（非阻塞）

### B1. 章节顺序：§2.8/2.9/2.10 位于 §2.7 分期表之后

分期表 P0/P1 行前向引用 §2.8/§2.9/§2.10。建议把 2.8–2.10 移到 2.7 之前（§2.6
后直接接 2.8），消除前向引用。

### B2. coverage-mismatch 的「CI 证据」来源口径（§2.9）

PR required checks 不含完整 backend/frontend tests（full 走手工 workflow/夜间）。
若证据源是 PR checks，则每个 `direct` 声明都可能常态亮 mismatch。建议指明证据
来源 = PR checks + 手工 full workflow 运行记录，或明确「接受该噪声为 advisory
设计意图」。（未见前两份审查覆盖，属本审查新增项。）

### B3. registry 可见性边界补一句（§2.2）

`git-common-dir` 保证同克隆多 worktree 唯一，但**同一机器两个独立克隆不共享
registry**。现状派生视图同为 per-clone，语义一致；建议显式写「registry 按克隆
隔离，跨克隆无共享」，防多克隆场景误读为全局登记。（新增项。）

### B4. `git-common-dir` 输出可能是相对路径

取决于 cwd / GIT_DIR 环境；实现需 realpath 归一化（P1 实测项已隐含，可在 §2.2
加「输出先归一化」字样）。（新增项。）

### B5. Role Context 未列入 §2.10 contract.md 内容清单

§2.1 模型含 Role Context、P2 交付它，但 2.10 清单只列「状态模型、scope 语法、
registry 协议、drift/coverage 语义」。建议补入，或在 §2.1 注明其定义归属 P2。
（与 0cd302 §4.2-6 不同，该文件修的是 G2 路径写法，本条为清单完备性。）

### B6. 编辑级

① P0 行「五处 canonical」与 §2.10 图「四入口 + 一权威」数字口径统一；② G2 试点
（`backend/agent/`→`aee/`，共享元文件串行 PR）与 P0 同为元文件接线的先后关系
可加半句「P0 合入后接 G2 试点」；③ Verification P0 行「（现 63 行）」改「（预算
内）」，避免行数快照漂移。（①②③均为新增；G2 路径写法 `aee/` →
`backend/agent/aee/` 已由 0cd302 §4.2-6 提出，本文不重复。）

## 5. 一致性核对（通过项）

- deepseek 对照 note（引用基线）G1–G5：G1/G3/G4 采纳描述与 AGENTS/notes README/
  checker 现状一致；G2 裁决、G5 Revisit 与 note 建议顺序一致 ✓
- 取代关系：2026-09-04 note「Accepted 后生效」→ P0 标 superseded + 交叉链接，
  与 note 生命周期约定自洽 ✓
- repository-workflow.md / harness-adapters.md 的 2026-09-04 指针 → P0 接线计划
  覆盖 ✓
- README 索引 v0.3（#861）、ADR 头状态行 v0.3、draft note 修订记录三者同步 ✓
- §4 Alternatives 新增两行（overlap 仅 ACTIVE / P1 硬 TTL）与 §2.3/§2.5 对应 ✓
- Verification P1 自测清单与 §2.3/§2.5/§2.8/§2.9 Contract 条款一一对应 ✓
- 9c124 的六项必修机械核验（全部通过）与本文不冲突，本文未重复机械核验 ✓

## 6. 审查结论

Proposed v0.3 技术内容可接受；§3 三处契约空白（READY/ABANDONED 判定者、STALE
落库口径、§2 内联规范迁移处置）建议 Accepted 前定稿——其中前两处与 0cd302 同题，
采纳时以 0cd302 §4.1 + 本文 §3 合并为一条修订即可。§4 为编辑级建议，可随
Accepted 前微修 PR 一并处理。
