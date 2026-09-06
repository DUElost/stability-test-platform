# ADR-0034 多 Harness 执行契约只读审查（v0.3）

- **状态**：Living（审查快照；不代改被审文档）
- **日期**：2026-09-06
- **会话 resume**：`d1e97a3c-bee0-4d71-b735-1de9cf0cd302`（后缀 `0cd302`）
- **审查对象**：[`docs/adr/ADR-0034-multi-harness-execution-contract.md`](../adr/ADR-0034-multi-harness-execution-contract.md)（**Proposed v0.3**）
- **关联合入**：PR [#858](https://github.com/DUElost/stability-test-platform/pull/858)（v0.1）、[#859](https://github.com/DUElost/stability-test-platform/pull/859)（v0.2）、[#860](https://github.com/DUElost/stability-test-platform/pull/860)（v0.3 Contract hardening）、[#861](https://github.com/DUElost/stability-test-platform/pull/861)（README M7 → v0.3）
- **关联 Issue**：[#855](https://github.com/DUElost/stability-test-platform/issues/855)、[#857](https://github.com/DUElost/stability-test-platform/issues/857)、[#854](https://github.com/DUElost/stability-test-platform/issues/854)
- **交接文档**：起草 note [`2026-09-06-adr-0034-draft.md`](../notes/process/2026-09-06-adr-0034-draft.md)；基线 [`2026-09-05-ai-harness-convention-baseline.md`](../notes/process/2026-09-05-ai-harness-convention-baseline.md)；现行并行约定 [`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)；[`harness-adapters.md`](../development/ai/harness-adapters.md)；[`repository-workflow.md`](../development/repository-workflow.md)
- **性质**：全只读静态审查。未运行测试、未改被审文件、未落地 `ai_work.py` / `execution-contract.md`
- **审查基线**：`main` @ `55e9a71e`（含 #861）

---

## 1. 结论摘要

**总评：v0.3 是一次合格的 Contract hardening。** 相对 v0.2 修掉了「overlap 只看 ACTIVE」的真实语义错误，状态权威分层更清楚，且仍守住「人选择 Harness / Registry 非调度器 / 不建 merge queue」。可作为 Proposed 继续评审。

**尚不宜当作 Accepted 后立刻开 P1 实现的完整规格**：存在若干实施歧义（`READY` / `ABANDONED` / P1 下 `STALE` 持久化语义）与登记面残留（README 主清单缺行、起草 note 开篇仍锚 v0.1）。

| 维度 | 判定 |
|------|------|
| 决策方向与 ROI（少协同、多开发） | **通过** |
| v0.3 相对 v0.2 的语义加固 | **通过** |
| 正文内部可实施性 | **有条件通过**（P1 歧义待钉死） |
| 仓库交叉引用 / 索引一致性 | **部分通过**（M7 已 v0.3；主表仍缺行） |
| 是否已可 Accepted → 开工 P1 | **否**（先微修歧义 + 索引；Accepted 后按分期 P0→P1） |

---

## 2. 方法与范围

1. 通读 ADR-0034 全文（含附录 A）与起草 note（含 v0.1→v0.3 修订段）。
2. 对照 `git show 864da462`（v0.3 diff）与 `778a51dd`（README 版本同步）。
3. 核验相对链接目标、`execution-contract.md` / `ai_work.py` 是否已存在、README 主清单与 M7 看板、AGENTS / repository-workflow / harness-adapters 现行指针是否仍指向 09-04（Accepted 前应保持）。
4. 对照仓库既有原则：[`2026-08-14-merge-path-attention-budget.md`](../notes/process/2026-08-14-merge-path-attention-budget.md)、串行 auto-merge、09-04「不为 N=2 引入 WIP 公告」裁定。

未做：Harness 实测复跑、GitHub checks 行为验证、实现 PoC。

---

## 3. v0.3 相对 v0.2 的实质增益（通过项）

| 加固项 | 判定 | 说明 |
|--------|------|------|
| Registry root → `$(git rev-parse --git-common-dir)/ai-work/` | 通过 | 多 worktree 经 common dir 汇合，优于「主 checkout 固定绝对路径」 |
| `registry.lock` + same-dir 原子写 | 通过 | 写协议可实施；`.git` 内天然免跟踪 |
| **liveness × integration 两维正交** | 通过 | 直接修正 finish 后 STALE 仍占集成风险窗口的反例 |
| overlap = `{NO_PR, PR_OPEN, READY}`，liveness 不参与 | 通过 | 与「合入前都是风险窗口」一致 |
| MERGED 只能由 GitHub 确认；`finish` = 停编码+已开 PR | 通过 | Registry 不再僭越合入事实 |
| P1 TTL 仅 advisory；P2 有 heartbeat 再升格 | 通过 | 避免声明式 CLI 假心跳误伤长任务 |
| Scope MVP / Role≠ownership | 通过 | 延续 09-04「分片非职责边界」，抑制协同税 |
| `test_impact` + coverage-mismatch（Contract 先、实现后） | 通过 | 分期清楚：P1 仅 schema，检测归 P3 advisory |
| §2.10 ADR ↔ `execution-contract.md` 分家 | 通过 | 方向对；文件属 P0 产物（当前不存在是预期） |
| Alternatives 补「仅 ACTIVE」「P1 硬 TTL」否决 | 通过 | 负向决策可审计 |
| 选择权原则 / 并发 ≈2–3 / 否决 merge queue | 通过 | v0.2 成果保留，与注意力 ROI 一致 |

---

## 4. 正文内部问题

### 4.1 P1 — 实施歧义（Accepted 前建议写清）

1. **`READY` 谁写、何时写**  
   定义是「required checks 全绿、进入 FIFO」。权威表写 PR lifecycle 归 GitHub，但未规定：`ai_work update` 轮询 checks 后写 `READY`，还是只读 GitHub、Registry 不存 `READY`。缺一句「`PR_OPEN→READY` 的写入方与核对源」。

2. **`ABANDONED` 权威来源**  
   `MERGED` 已锁 GitHub；`ABANDONED` 仅「明确放弃」。是 CLI 人工、关闭/撤回 PR、还是二者皆可？P1 schema 需要裁定。

3. **P1 的 `STALE` 是持久字段还是仅 status 提示**  
   §2.5：超时「不自动改写任何字段」；§2.3：liveness∈{ACTIVE,STALE}；Verification 又要求「STALE advisory」样例。易读成矛盾。更干净的写法：P1 **不持久化 STALE**，只在 `status` 输出「last_seen 可能陈旧」；或明确「可写 STALE 但不影响 overlap」。

### 4.2 P2 — 措辞易误导

4. **「从不上锁」与 `registry.lock` 并列**（§2.2）  
   本意应是「不对业务路径 / scope 上互斥锁」，但字面紧挨 flock 锁文件。建议改为「不对业务文件上锁（visibility-only）；`registry.lock` 仅保护 registry 文件原子写」。

5. **Alternatives「Phase 1」vs 正文「P1」**  
   同一分期两套叫法，宜统一为 P1。

6. **G2 试点顺序仍写 `` `aee/` ``**  
   宜 `` `backend/agent/aee/` ``（自 v0.2 残留）。

### 4.3 P3 — 注意力 / ROI 风险（设计可接受，实施需守）

7. **`declare` 强制 `test_impact`**  
   每会话多一次分类，有轻微协同税。Contract 把检测放到 P3 advisory 是对的；P1 若做成「缺字段就失败」会放大摩擦——建议 P1 允许缺省 + warning，或默认 `indirect`。

8. **ADR 正文仍承载完整执行语义**  
   §2.10 说细则应进 `execution-contract.md`，但该文件尚未创建（P0）。Proposed 阶段可接受；Accepted 后若不抽离，ADR 会变成第二契约源。

---

## 5. 交叉面与登记

| 项 | 状态 | 说明 |
|----|------|------|
| 正文版本锚 | ✅ | **Proposed（v0.3）** |
| README M7 看板 | ✅ | #861 已改为 v0.3 |
| README **主清单表** ADR-0034 行 | ❌ | 表仍止于 ADR-0033；仅看板提及 |
| 起草 note 开篇「Proposed v0.1」 | ⚠ | 已有 v0.3 段，但 Decision 首段仍锚 v0.1 |
| 起草 note `Status: implemented` | ⚠ | 表示「起草完成」易与 ADR Accepted 混淆 |
| `execution-contract.md` | ✅（预期缺失） | P0 产物，Accepted 后建立 |
| `tools/dev/ai_work.py` | ✅（预期缺失） | P1 产物 |
| ADR 相对链接 | ✅ | 基线 note / 09-04 / harness-adapters 均存在 |
| AGENTS → repository-workflow → 09-04 | ✅ | Accepted 前不 supersede，符合 ADR P0 分期 |
| harness-adapters「不定义 Role/Scope/Registry」 | ✅ | 与基线交接条款一致 |

---

## 6. 与「少协同、多开发」目标的一致性

v0.3 **加强了正确边界**，没有滑向编排器：

- overlap 变为集成窗口 hint，不是锁；
- Role / scope 明确非 ownership；
- TTL / STALE 不踢出窗口、不回收；
- 合入事实仍在 GitHub + 既有 FIFO auto-merge；
- 选择权始终在开发者；Registry = 自声明可见性。

主要残留风险不是「agent 互相同步」，而是 **声明仪式过重**（`test_impact`、两维状态、finish/update 纪律）。正文已把硬检测后置；实现时需继续 fail-open（diff 优先、缺省可容忍）。

---

## 7. 建议的下一步（供作者采纳；本审查不代改）

### 7.1 Accepted 前文档微修（可单独小 PR）

1. 澄清 `READY` / `ABANDONED` 写入权威与触发。
2. 澄清 P1 下 STALE 是否持久化。
3. 改写「从不上锁」措辞；`Phase 1`→`P1`；`aee/` 路径写全。
4. README **主清单补 ADR-0034 行**；起草 note 开篇 v0.1→v0.3（或注明 drafting complete / ADR still Proposed）。

### 7.2 Accepted 后按 ADR 分期

1. **P0**：建立 `docs/development/ai/execution-contract.md`，抽离执行细则；AGENTS / harness-adapters / 基线 note / 09-04 note supersede 接线（独立 docs PR，元文件串行化）。
2. **P1**：`ai_work.py` + registry schema（含 `test_impact` 字段）+ overlap 集合自测；不引入 heartbeat daemon。
3. **P2+**：Adapter / heartbeat / drift+coverage-mismatch advisory。

### 7.3 明确不要做（与 ADR Alternatives 一致）

- 不建 merge queue / 需求路由 / agent 间消息协议；
- 不以 liveness=ACTIVE 作为 overlap 唯一条件；
- P1 不对 TTL 做硬语义回收。

---

## 8. 一句话

v0.3 方向正确、关键语义加固成立；把 `READY`/`ABANDONED`/`STALE` 的写入语义钉死并补上 README 主表后，更接近可 Accepted 的状态。
