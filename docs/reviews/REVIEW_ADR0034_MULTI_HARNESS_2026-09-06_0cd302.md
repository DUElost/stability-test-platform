# ADR-0034 多 Harness 执行契约只读审查

- **状态**：Living（审查快照；不代改被审文档）
- **日期**：2026-09-06 起；**本版续写 2026-09-07**
- **会话 resume**：`d1e97a3c-bee0-4d71-b735-1de9cf0cd302`（后缀 `0cd302`）
- **审查对象**：[`docs/adr/ADR-0034-multi-harness-execution-contract.md`](../adr/ADR-0034-multi-harness-execution-contract.md)（现行 **Accepted v1.2**）
- **契约权威源**：[`docs/development/ai/execution-contract.md`](../development/ai/execution-contract.md)（Living v1.1）
- **综合裁决权威映射**：[`REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`](./REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)（R1–R30）
- **性质**：全只读确认 / 审查。本版两节新增均为只读核验，不代改生产行为。
- **现行审查基线**：`main` tip = **`3e072f62`**（Merge PR [#899](https://github.com/DUElost/stability-test-platform/pull/899) / Fixes [#880](https://github.com/DUElost/stability-test-platform/issues/880)）

---

## 1. 现行结论摘要（2026-09-07）

| 问题 | 判定 |
|------|------|
| ADR-0034 **计划内开发**（方向 + P0–P3）是否完结？ | **是**（见 §2） |
| 多 Harness 批次 **第一单 #880** 是否完结？ | **是**（见 §3） |
| 整条治理轨是否零遗留？ | **否**——#855 / #857 / #867 等仍 OPEN；P4 为观察项（见 §4） |

**一句话**：契约与分期工具链（含 dogfood）已就绪并开跑；#880 为首单实战闭环。后续是批次内其余 Requirement 与关联收尾，不是「ADR 开发未完」。

---

## 2. 上一轮只读确认：ADR-0034 开发工作是否完结（2026-09-07 上午）

### 2.1 判定

**计划内开发主路径已完结**（方向 Accepted；P0–P3 已交付；P4 观察项刻意不做）。  
不等于关联收尾 / 缺陷 issue 清零。

### 2.2 分期对照（相对 ADR §2.7）

| 期 | 状态 | 证据（合入） |
|----|------|----------------|
| 方向裁决 | **完结** | Accepted v1.0 [#865](https://github.com/DUElost/stability-test-platform/pull/865) → v1.1 [#866](https://github.com/DUElost/stability-test-platform/pull/866) → v1.2 [#877](https://github.com/DUElost/stability-test-platform/pull/877) |
| P0a | **完结** | `execution-contract.md`；[#866](https://github.com/DUElost/stability-test-platform/pull/866) |
| P0b | **完结** | supersede/接线 [#869](https://github.com/DUElost/stability-test-platform/pull/869)；收尾 [#874](https://github.com/DUElost/stability-test-platform/pull/874)；AGENTS 已指契约；09-04 note 已标被取代 |
| P1 | **完结** | `tools/dev/ai_work.py` Registry MVP [#878](https://github.com/DUElost/stability-test-platform/pull/878) |
| P2 | **完结** | Adapter [#879](https://github.com/DUElost/stability-test-platform/pull/879)；P2b 根 bootstrap [#892](https://github.com/DUElost/stability-test-platform/pull/892) |
| P3 | **完结** | drift gate advisory [#893](https://github.com/DUElost/stability-test-platform/pull/893)（合入说明：分期最后一块） |
| P4 Integration Planner | **未做（有意）** | 观察项：仅在「人已难判集成顺序」真实积累后启用 |
| G2 试点 | **完结** | scoped AGENTS.md + symlink [#870](https://github.com/DUElost/stability-test-platform/pull/870) |

当时 tip 核验窗口：`257a7e60` 一带（其后另有无关 gates 修复）。

### 2.3 当时已点名、但不算「ADR 开发未完」的 OPEN 项

| Issue | 性质 |
|-------|------|
| [#855](https://github.com/DUElost/stability-test-platform/issues/855) | 行为验证缺口补全——触发条件已满足，后续工作项 |
| [#857](https://github.com/DUElost/stability-test-platform/issues/857) | Claude 子目录 `@import` 缺陷 |
| [#867](https://github.com/DUElost/stability-test-platform/issues/867) | DOC-MAP / adr README 索引漂移 |
| [#880](https://github.com/DUElost/stability-test-platform/issues/880) | （当时 OPEN）ai_work codec——**本版 §3 已确认关闭** |

---

## 3. 本轮只读确认：多 Harness 批次第一单 #880（2026-09-07 下午）

### 3.1 用户陈述要点（待核验）

- PR [#899](https://github.com/DUElost/stability-test-platform/pull/899) 合入 `3e072f62`，#880 自动 CLOSED
- 优先 Requirement：修复 P1 MVP codec（`#` 开头 / 含 `:` 的 requirement id → declare 报 OK 后全命令崩）
- 修复 1+2+3：语义守门 `normalize_requirement_id`；codec `_quote` 对称化；corrupt 隔离落契约 §2.2
- 过程：CodeQL ReDoS 跟进；corrupt 样例构造修正
- Dogfood：declare → whoami → update --pr → READY → merge → reconcile MERGED → finish；Registry 两条完整生命周期

### 3.2 核验结果：**属实**

| 声称 | 核验 |
|------|------|
| PR #899 合入，merge = `3e072f62` | ✅ MERGED；当前 `main` tip 即此 commit；工作树干净、`main...origin/main` |
| #880 自动 CLOSED | ✅ `CLOSED` @ 合入后约 1s |
| 修复 1+2+3 在树 | ✅ `normalize_requirement_id` / `_quote` 去 `#` / `.corrupt-` 隔离；Agent Note [`2026-09-07-ai-work-registry-codec-880.md`](../notes/bug-fix/2026-09-07-ai-work-registry-codec-880.md) |
| CodeQL ReDoS 跟进 | ✅ tip 前 `6ce81f80` |
| `--self-test` 绿 | ✅ 本机核验通过 |
| Dogfood 两条完整生命周期 | ✅ Registry：`fix-825-gates-parity`、`fix-880-registry-codec`；均为 `FINISHED` + `MERGED` + `risk=no`（已出窗） |
| R01 台账 [#891](https://github.com/DUElost/stability-test-platform/issues/891) | ✅ OPEN（10 项为 #881–#890；与本单 #880 **无关**——#880 属工具链自身发现，见 [`4f6e4b`](./REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_4f6e4b.md) §9.2） |

### 3.3 批次含义

- **第一单完结**；工具链（declare / status / whoami / heartbeat / update / finish / drift）经实战验证齐备
- 多 Harness 批次已正式开跑的前提成立
- **不混入本单**：#855 / #857 / #867 及并行的 R01 台账 #891——属后续排期

---

## 4. 仍挂着的关联项（明确不挡「ADR 分期完结 / #880 第一单」）

| Issue | 状态 | 说明 |
|-------|------|------|
| [#855](https://github.com/DUElost/stability-test-platform/issues/855) | OPEN | 行为验证补全 |
| [#857](https://github.com/DUElost/stability-test-platform/issues/857) | OPEN | Claude 子目录 import |
| [#867](https://github.com/DUElost/stability-test-platform/issues/867) | OPEN | 索引同步漂移 |
| [#891](https://github.com/DUElost/stability-test-platform/issues/891) | OPEN | R01 架构审查台账（#881–#890；非 #880） |
| P4 | 观察 | Integration Planner——无真实积累前不建 |

---

## 5. 关联合入时间线（摘要）

| 阶段 | PR |
|------|-----|
| 草案 v0.1–v0.5 | #858–#864 |
| Accepted v1.0 | #865 |
| P0a / P0b / G2 / P1 / P2 / P3 | #866 / #869+#874 / #870 / #878 / #879+#892 / #893 |
| P1 判据修订（ADR v1.2） | #877 |
| **批次第一单 #880** | **#899 → `3e072f62`** |

---

## 6. 附录 A：v0.4 草案审查快照（历史，2026-09-06）

> 当时基线 `1715eee6`（#862/#863）；对象为 **Proposed v0.4**。结论已被后续 Accepted + 分期交付吸收。

**当时总评**：v0.4 已消化八源 synthesis（含本会话）关键阻断，可进入 Accepted 终审；剩 `finish` argv 等交 P0 transition table。

**当时维度表**：方向/ROI 通过；可实施性通过（小张力）；索引通过；可 Accepted=是；立刻 P1=否（先 P0）。

**v0.3→v0.4 已收口项**（摘要）：READY/ABANDONED/STALE 语义、不上锁措辞、aee 路径、test_impact 缺省、README 主表、DOC-MAP。

**当时开工建议**：Accepted 后先 P0，P1 等启动判据；不建 merge queue / Competition / P1 硬 TTL。  
→ **事后核对**：建议路径已走完（§2）；P1 判据后经 v1.2 增补「批次启动前预置就绪」。

---

## 7. 附录 B：v0.3 审查快照（历史，2026-09-06）

> 基线当时 `55e9a71e`（#861）。

**当时总评**：合格 Contract hardening，但尚不宜立刻开 P1。

**当时条件项**（均已在 v0.4+ 收口）：READY/ABANDONED/STALE 未钉死；「从不上锁」歧义；README 主表缺行；test_impact 强制分类摩擦。

---

## 8. 一句话（现行）

ADR-0034 计划内开发已完结；多 Harness 批次第一单 #880（PR #899 / `3e072f62`）只读核验属实并已出窗。继续推进批次其余 Requirement 与 #855/#857/#867/#891 收尾即可。
