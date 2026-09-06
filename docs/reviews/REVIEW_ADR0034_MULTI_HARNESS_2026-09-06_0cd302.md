# ADR-0034 多 Harness 执行契约只读审查

- **状态**：Living（审查快照；不代改被审文档）
- **日期**：2026-09-06
- **会话 resume**：`d1e97a3c-bee0-4d71-b735-1de9cf0cd302`（后缀 `0cd302`）
- **审查对象**：[`docs/adr/ADR-0034-multi-harness-execution-contract.md`](../adr/ADR-0034-multi-harness-execution-contract.md)（现行 **Proposed v0.4**）
- **关联合入**：
  - [#858](https://github.com/DUElost/stability-test-platform/pull/858) v0.1
  - [#859](https://github.com/DUElost/stability-test-platform/pull/859) v0.2
  - [#860](https://github.com/DUElost/stability-test-platform/pull/860) v0.3 Contract hardening
  - [#861](https://github.com/DUElost/stability-test-platform/pull/861) README M7 → v0.3
  - [#862](https://github.com/DUElost/stability-test-platform/pull/862) v0.4 八源 synthesis 综合修订
  - [#863](https://github.com/DUElost/stability-test-platform/pull/863) R6/R18 人工裁决落地
- **关联 Issue**：[#855](https://github.com/DUElost/stability-test-platform/issues/855)、[#857](https://github.com/DUElost/stability-test-platform/issues/857)、[#854](https://github.com/DUElost/stability-test-platform/issues/854)
- **综合裁决权威映射**：[`REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`](./REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)（R 编号）
- **交接文档**：起草 note [`2026-09-06-adr-0034-draft.md`](../notes/process/2026-09-06-adr-0034-draft.md)；基线 [`2026-09-05-ai-harness-convention-baseline.md`](../notes/process/2026-09-05-ai-harness-convention-baseline.md)；现行并行约定 [`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)；[`harness-adapters.md`](../development/ai/harness-adapters.md)；[`repository-workflow.md`](../development/repository-workflow.md)
- **性质**：全只读静态审查。未运行测试、未改被审 ADR 正文语义、未落地 `ai_work.py` / `execution-contract.md`
- **审查基线**：`main` @ `1715eee6`（含 #862 / #863）

---

## 1. 结论摘要（现行：v0.4）

**总评：v0.4 已消化八源 synthesis（含本会话 `_0cd302`）的关键阻断项，相对 v0.3 从「有条件可评」提升到「可进入 Accepted 人工终审」。**

方向、事实分层、ROI 边界仍一致；残留多为措辞/分期债，不宜再挡 Accepted，但 P0 `execution-contract.md` 必须把 transition table 写死。

| 维度 | 判定 |
|------|------|
| 决策方向与 ROI（少协同、多开发） | **通过** |
| v0.4 相对 v0.3 / 八源 R 项收口 | **通过** |
| 正文内部可实施性 | **通过**（剩 `finish` argv 小张力，交 P0 表） |
| 仓库交叉引用 / 索引一致性 | **通过**（主表 + M7 v0.4 + DOC-MAP） |
| 是否可进入 Accepted 终审 | **是** |
| Accepted 后立刻开工 P1？ | **否**（先 P0；P1 等启动判据） |

---

## 2. 方法与范围

1. 通读 ADR-0034 v0.4 全文与起草 note（含 v0.1→v0.4 修订段）。
2. 对照 #862 / #863 与 [`REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`](./REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md) R 编号落点。
3. 核验相对链接、README 主清单 / M7、DOC-MAP、现行 AGENTS→repository-workflow→09-04 指针（Accepted 前应保持）。
4. 复核本文件先前 v0.3 审计项在 v0.4 的处置状态。
5. 对照合入路径注意力预算与 09-04「不为 N=2 引入 WIP 公告」裁定。

未做：Harness 实测复跑、GitHub checks 行为验证、实现 PoC。

---

## 3. v0.3 审计项在 v0.4 的处置

| v0.3 审计项（本文件前版） | v0.4 处置 |
|---------------------------|-----------|
| READY 谁写 | ✅ `update` 依 GitHub checks **派生刷新**，可回退 `PR_OPEN` |
| ABANDONED 权威 | ✅ 仅 `finish --abandon`；integration 侧用 `CLOSED`（PR 关未合） |
| STALE 持久 vs 提示 | ✅ **查询时派生、不持久化**；持久层只存 `last_seen` |
| 「从不上锁」歧义 | ✅ 「不对业务文件/scope 上锁；`registry.lock` 仅保护原子写」 |
| `aee/` 路径 | ✅ `backend/agent/aee/` |
| `test_impact` 摩擦 | ✅ P1 **允许缺省=indirect** |
| README 主表缺行 | ✅ 已补；M7 看板 **v0.4** |
| DOC-MAP | ✅ 已挂 |
| `execution-contract.md` / `ai_work.py` 不存在 | ✅ 仍属 P0/P1 预期缺失，非缺陷 |

---

## 4. v0.4 实质增益（通过）

| 项 | 判定 |
|----|------|
| Registry root 固定 `--path-format=absolute` | 通过（审查机 git 2.47.3 ≥ 2.31） |
| overlap = `declared ∪ derived(diff)`，冲突以 derived 为准 | 通过；09-04 反例入文 |
| 三维 lifecycle×liveness×integration，且标明**实现选择非冻结条款** | 通过；与 Contract v1 两维 + B3 裁决对齐（#863） |
| `status` 严格只读 | 通过 |
| 原子写九步全序（含 parent dir fsync） | 通过 |
| P0：细则一次平移进 contract、ADR 收缩；接线含 repository-workflow；入 S2/S6 | 通过 |
| coverage 证据=夜间全量/合并后，非 PR 轻量 checks | 通过 |
| #855 三段触发；#847 对账；P1 启动判据；Competition 不入 Contract | 通过 |
| G2：symlink 方向 CLAUDE→AGENTS + 根契约双边验收 | 通过 |
| 选择权原则 / Registry 非调度器 / 不建 merge queue / 并发 ≈2–3 | 通过（保留） |

---

## 5. 残留问题（非阻断）

### 5.1 P2 — 措辞张力（Accepted 前顺手或放 P0 transition table）

1. **`finish`「只写 lifecycle、不碰 integration」vs `finish(PR #N)` 可写 `PR_OPEN`**  
   建议钉死：
   - `finish` **必**写 `lifecycle→FINISHED`；
   - **若**带 PR 号且当前 `NO_PR`，**才**写 `integration→PR_OPEN`；
   - 永不写 `MERGED`/`CLOSED`。

2. **无 PR 的 `finish` 是否合法**  
   P0 需明确三态 argv：`finish` / `finish --pr N` / `finish --abandon`。

3. **Alternatives 仍写 `liveness=ACTIVE` / `Phase 1`**  
   历史否决行命名过时；统一成 `LIVE`/`P1` 即可。

### 5.2 P3 — 已知分期债

4. ADR §2 仍很厚，与 §2.10「细则进 contract」并存——**P0 平移后必须收缩**，否则再成双源。
5. synthesis 附录 B 三项范围外（step-stall 文档、`effective_slots` 出处、`AGENT_SECRET` 告警）——另立 docs PR。
6. 起草 note `Status: implemented` 仍易与 ADR Accepted 混淆——过程记录风格，非契约错误。

---

## 6. 交叉面

| 项 | 状态 |
|----|------|
| 相对链接（基线 / 09-04 / synthesis / harness-adapters） | ✅ |
| README 主表 + M7 v0.4 | ✅ |
| DOC-MAP | ✅ |
| 现行 AGENTS / repository-workflow 仍指 09-04 | ✅（Accepted 前正确） |
| synthesis R 编号；R6/R18 已裁决（#863） | ✅ |

---

## 7. Accepted / 开工建议

| 问题 | 建议 |
|------|------|
| 现在能否 Accepted？ | **可以进入人工终审 Accepted** |
| Accepted 后第一件事 | **只做 P0**（建 `execution-contract.md`、平移细则、薄入口接线、supersede 09-04），不要顺手写 `ai_work.py` |
| P1 何时开工 | P0 合入 **且** 满足启动判据（连续两周 worktree≥3，或 ≥2 次跨 Harness 撞车返工） |
| 仍不要做 | merge queue、Competition 入 Contract、P1 硬 TTL、把三维宣传成「冻结条款」 |

---

## 8. 附录：v0.3 审查快照（历史，已被 §3 处置表吸收）

> 以下保留 v0.3 审查时点结论，便于对照演进。审查基线当时为 `55e9a71e`（#861）。

**当时总评**：v0.3 是合格的 Contract hardening（修掉 overlap 只看 ACTIVE；状态权威分层更清；守住人选择 Harness / Registry 非调度器 / 不建 merge queue），但尚不宜当作 Accepted 后立刻开 P1 的完整规格。

**当时阻断/条件项**（现均已在 v0.4 收口，见 §3）：

- READY / ABANDONED / STALE 写入与持久化语义未钉死
- 「从不上锁」与 `registry.lock` 措辞冲突
- README 主清单缺 ADR-0034 行；起草 note 仍锚 v0.1
- `test_impact` 强制分类的协同税风险（无缺省策略）

**当时一句话**：v0.3 方向正确；把 READY/ABANDONED/STALE 写入语义钉死并补 README 主表后更接近可 Accepted。

---

## 9. 一句话（现行）

v0.4 把 v0.3 的关键语义坑和登记面债基本清完；剩 `finish` 三态 argv 的小张力，交给 P0 transition table 即可。比 v0.3 更接近可 Accepted。
