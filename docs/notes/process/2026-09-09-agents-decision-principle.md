# AGENTS.md 总原则增「要不要做」与止血标注（债务形态约束）

Status: implemented
Class: process

## Decision

`AGENTS.md`「总原则」新增一条两行原则（位于首条"先检查代码/测试/文档"之后）：

```markdown
- 要不要做、有没有必要做：站在问题本质判断（本质问题 / 最小方案），不因惯性或先例而动；
  临时止血必须标注为过渡并写明终态出口——不标注的止血会沉淀为技术债与代码腐化。
```

- 第一行 = 第一性原理（问题本质优先，反对惯性/先例驱动）；
- 第二行 = 长期主义复利的**可判定形态**：不禁止止血，禁止**未标注**的止血。
- 实证基础（本仓库已付的账单）：`docs/adr/ADR-0033-tool-kit-ecosystem-integration.md:21`
  ——外部工具源码直接拷贝膨胀 **100 个版本 / 59,406 行（占全后端 27.43%）**、
  环境变量膨胀至 **约 190 个**（示例配置仅列 29 个），该文自述为"严重的
  代码腐化与技术债危机"。范式先例：`docs/adr/ADR-0026-plan-execution-scaling.md:277`
  ——"P0 修复定位为 P1 队列上线前的**止血过渡**，不是终态方案"。

配套：需求入口三问已由 #1184 落在 `.github/ISSUE_TEMPLATE/{feature_request,epic}.md`
（人侧入口），本条是 agent 侧常驻入口，两者同源不同面。

## Alternatives

- **只写"第一性原理 / 长期主义复利"名称**：否决——只有名字的原则无法判定是否
  被遵守，且会成为 `AGENTS.md` 中唯一不可验证的条目；名称与问法同写才既可检索
  又可执行。
- **写成"禁止临时方案/止血"**：否决——与既有实践冲突。本仓库大量**正确**使用
  止血（ADR-0028 阶段 1、ADR-0029 env 权宜之计、ADR-0026 P0 自认过渡）；
  要防的是"不标注"，不是止血本身。
- **新增 `##` 章节或独立哲学文档**：否决——撞 `check_governance_surface.py`
  S9 根章节白名单（`AGENTS.md` 仅允许 `{总原则, 硬不变量, 开始任务时, 按需入口, 提交前}`），
  且常驻宣言=负复利。
- **做成门禁（CI 校验"是否标注止血"）**：否决——语义判定不可机械化，强制只会
  生产套话；且 #855 已裁决不重建行为验证层（测量不产生约束力）。
- **升格为 ADR**：否决——本条是既有范式（ADR-0026/0033）的升格，非方向级新决策，
  按 `AGENTS.md`「非平凡变更必须附 Agent Note；方向级决策使用 ADR」用 Note 即可。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --check`
  → `[OK] 治理面结构检查通过（阻塞项全绿：S1–S12、S5x）`；
- `venv/bin/python tools/dev/check_governance_surface.py --self-test`
  → `[OK] self-test 通过：13 条规则各含红/绿样例双向验证`；
- S6 预算实测：`AGENTS.md` **70 → 72 行 / 4685 → 4934 字节**（上限 80 行 / 8000 B）；
- `venv/bin/python scripts/run_gates.py check:quick` 全绿；
- diff 人工复核：仅新增 2 行，未动其余章节与硬不变量；
- 关联 issue：[#1183](https://github.com/DUElost/stability-test-platform/issues/1183)。

## Revisit

判据与观测（#1267 补充；触发任一即重议，重议后另开 issue，不回填本 Note）：

### 1. residual 的触发信号（「总原则」无门禁保护）

实测事实：S11 锚串 **11 条全部锚在「硬不变量」节，「总原则」节 0 条**；
S6 只查体量（80 行 / 8000 B）、S9 只查章节名——故这两行可被静默删除或弱化，
`check:quick` 与 CI 仍全绿。

- **观测方式**：① 凡 diff 触及 `AGENTS.md` 时，人工复核该节是否被改写/删除；
  ② 定期 `git log -p --follow AGENTS.md | grep -n "要不要做\|止血必须标注"`
  确认锚文本仍在。
- **触发后二选一（含代价）**：
  - **A. 把该行文本加入 S11 锚串**（`tools/dev/check_governance_surface.py` 的
    `HARD_INVARIANT_ANCHORS` 列表）：改写/删除即 BLOCK。机制无额外代价——
    S11 的 `check_hard_invariants` 只是对**全文**跑锚串正则，**不做章节归属校验**
    （与 S9 的 `ROOT_HEADING_ALLOWLIST` 无耦合，「总原则」已在 S9 白名单内）；
    真实代价是语义性的：锚串按设计只锚"几乎不该变"的不变量，锚住总原则会与
    "原则允许演进"冲突；
  - **B. 把该行文本移入「硬不变量」节**：位置即语义，符合锚串设计意图；
    代价是须满足该节入场标准"对任何 Requirement 都成立"——**仅第二行
    （止血标注）够格**，第一行（要不要做/本质判断）不够。
- 判据不是"看起来有没有用"，而是**是否已发生事故**：出现"本条被静默弱化"
  或"未标注止血合入后爆雷"任一实例，才动手。

### 2. 套话警戒（两个相反方向）

本次实测基线（#1267，窗口 2026-08-25 起）：

```bash
files=$(git log origin/main --since="2026-08-25" --diff-filter=A --name-only \
  --format="" -- 'docs/notes/*/*.md' | grep -v archived | grep . | sort -u)
echo "$files" | wc -l                                    # 250（新增 Note 总数）
for f in $files; do git show "origin/main:$f" 2>/dev/null \
  | grep -qE "止血|过渡|权宜|技术债|腐化" && echo "$f"; done | wc -l   # 12（≈4.8%）
```

- **症状 B「标注泛滥」**：该比例升到 **30–50%+**（"过渡/止血"成为口头禅，
  第二行退化为免责声明）→ 收紧为"标注必须附终态出口的排期或关联 Issue 号"；
- **症状 A「填了不看」**：比例长期≈0 **且**期间确有"临时方案被当终态"的
  实际损失 → 降级为注释行或移入按需文档；
- 基线数字本身不判好坏，只看趋势；重算请用上方命令并在结论中标注窗口。

**无自动门禁**：本项依赖人工抽样与趋势判断，归 residual（#855：测量不产生
约束力，不为低频事件建常驻设施）。
