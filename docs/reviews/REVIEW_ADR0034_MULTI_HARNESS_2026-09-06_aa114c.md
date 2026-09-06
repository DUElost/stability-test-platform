# ADR-0034 多 Harness 执行契约只读审查报告（Proposed v0.3）

- 审核日期：2026-09-06
- 审查类型：系统性只读审查与架构审计（未修改任何生产代码）
- 审查对象：
  - `docs/adr/ADR-0034-multi-harness-execution-contract.md`（Proposed v0.3，commit `864da462`）
  - `docs/notes/process/2026-09-06-adr-0034-draft.md`（起草与修订历程）
  - `docs/adr/README.md`（ADR 索引，commit `778a51dd`）
- 会话 Resume 标识：`aa114c`（Conversation ID: `2357bf61-1c1b-4139-b5be-378318aa114c`）

---

## 一、 审查结论与总体定性

**ADR-0034 v0.3 无阻断性缺陷（No Blockers），无 A 级缺陷，架构自洽性与工程边界已达到工业级标准，建议正式予以 Accepted 裁决。**

相比 v0.2，v0.3 并非颠覆性重构，而是对照规范基准完成了一次极高密度的**精准契约加固（Contract Hardening）**。它彻底纠正了 v0.2 中“状态机与活跃度混淆导致的 overlap 漏报盲区”、“Worktree 共享存储边界与锁协议模糊”、“本地工具越权判定终态”等核心逻辑隐患，并前瞻性地确立了**“ADR 裁决记录、Execution Contract 活态规范、AGENTS.md 极简启动契约”**的三层分离治理模式。

---

## 二、 门禁与机械校验记录

审计期间对仓库当前主干（commit `55e9a71e`）进行了全量门禁实测，结果均在场且为绿灯：

| 门禁 / 校验项 | 实际运行命令 | 校验结果 | 备注 |
|---|---|---|---|
| **快速静态门禁（6 gates）** | `venv/bin/python scripts/run_gates.py check:quick` | **PASS (0)** | ruff、eslint、tsc、knip、compileall、gov-surface 全绿（耗时 ~20s） |
| **治理面结构门禁（L0）** | `python3 tools/dev/check_governance_surface.py --check` | **PASS (0)** | S1–S11、S5x 阻塞项全部通过 |
| **治理面红绿双向自测** | `python3 tools/dev/check_governance_surface.py --self-test` | **PASS (0)** | 12 条规则的正反样例双向验证全部通过 |
| **常驻入口预算控制（S6）** | `check_governance_surface.py:check_resident_budgets` | **PASS** | `AGENTS.md` 64 行 / 3815B（≤80行/8KB）；`CLAUDE.md` 27 行 / 978B（≤60行/6KB） |
| **硬不变量在场防护（S11）** | `check_governance_surface.py:check_hard_invariant_anchors` | **PASS** | 11 条原文正则锚点对当前 `AGENTS.md` 100% 命中 |
| **README 索引一致性** | 检查 `docs/adr/README.md:96` | **PASS** | PR #861 已将里程碑看板同步为 `ADR-0034（**Proposed** v0.3）` |

---

## 三、 v0.3 六大核心加固点（Contract Hardening）逐项核验

### 1. 状态模型升维：Liveness × Integration 两维正交（§2.3）——【核心纠偏】
* **v0.2 缺陷剖析**：v0.2 将进程活性与集成进度线性混为一体（`ACTIVE → finish → ACTIVE(等集成)`），并错误断言“仅 ACTIVE 参与 overlap 检测”。真实反例：Execution A 编码完成提交 PR，关闭 terminal 或 Harness 进程退出变为 `STALE`；此时其 PR 仍挂在 GitHub 等待自动合入，仍在修改核心文件 `foo.py`。若新起的 Execution B 修改 `foo.py` 时仅查 `ACTIVE`，则此严重冲突窗口被完全漏报。
* **v0.3 纠偏核验**：
  * 彻底正交化为两个维度：
    * `liveness ∈ {ACTIVE, STALE}`：仅代表进程心跳与执行者会话活跃度，**永远纯为 Advisory**，绝不改变业务语义，绝不将记录移出冲突检测窗口；
    * `integration ∈ {NO_PR, PR_OPEN, READY, MERGED, ABANDONED}`：代表 Git/GitHub 事实集成进度。
  * **Overlap 判定集合收敛为：`integration ∈ {NO_PR, PR_OPEN, READY}`，Liveness 完全不参与过滤**。
  * **核验结论**：完全正确。冲突的根源在于“未合入主干的代码变更”，而不在于“执行者进程当前是否在存活打字”。

### 2. Registry 存储拓扑与锁协议严密化（§2.2）
* **存储锚点**：收敛至 `$(git rev-parse --git-common-dir)/ai-work/`。
  * 避免了 v0.2 中“主 checkout 固定绝对路径”的脆弱假设（各 worktree 之间无主从地位，路径硬编码在跨环境迁移时失效）；
  * 所有 linked worktree 经 `git-common-dir` 天然解析至主 `.git/` 内部唯一的共享目录，天然不污染工作区，天然免于 `git status` 脏检查，无需在 `.gitignore` 增补例外。
* **并发写控制**：严格固定 `registry.yaml`（数据）与 `registry.lock`（锁文件）。
  * 写入协议：`flock(registry.lock) → 同目录临时文件 + fsync + 原子 rename`；
  * 明确保留红线：**仅在本地 POSIX 文件系统成立，严禁落地于 NFS/CIFS**。
  * **核验结论**：完全符合分布式/并发本地系统的原子落盘最佳实践。

### 3. 事实来源分层与防篡改约束（§2.3）
* **四层权威矩阵确立**：
  * **Registry**：仅记录执行侧自声明的意图（Scope、Role、PR 号、时间戳）；
  * **Git**：代码实际变更的唯一真理（Diff 优先于 Registry 声明，§2.4）；
  * **GitHub**：PR 生命周期的唯一事实源（**`MERGED` 状态只能由 GitHub PR 确认**）；
  * **CI**：验证结果事实源。
* **越权限制**：明确 `finish(PR #N)` 语义仅为“执行者已停止编码并已提 PR（`NO_PR → PR_OPEN`）”；`ai_work` 工具严禁单方面自封 `MERGED`，落终态必须核对 GitHub PR 真实状态。
* **核验结论**：从根源杜绝了“本地工具因乐观幻觉单方面标记合入”导致的假一致性风险。

### 4. TTL 与心跳机制的分期落地（§2.5）
* **P1（MVP 无常驻 Daemon）**：`status/update` 执行时被动更新 `last_seen`。TTL（24h 量级）**纯为 Advisory**，超时仅在终端输出黄色提示，绝对不自动剔除记录、不自动降级业务状态。
* **P2（Adapter 心跳就位）**：当 Harness 封装层具备主动心跳时，`last_seen` 升格为可靠判定源。
* **核验结论**：务实且克制。避免了因为缺乏统一后台守护而把“上午 declare、全天编码”的正常长任务误判为失效。

### 5. Scope MVP 边界的极简化守卫（§2.8）
* 语法严格收窄为**归一化（normalized）的仓库相对路径（repo-relative path）**，仅允许单文件或目录粒度；
* 明确排除了 Glob 通配符、访问控制/所有权锁、自动化任务拆分、语义 Scope；
* 重申核心不变量：**Role 不是文件 ownership 边界**，分片只是冲突规避的参考（hint），绝不限制跨片修改自由。
* **核验结论**：守住了 MVP 的最小复杂度，防止前期在路径模式匹配上消耗过多精力。

### 6. 质量证据链前置：`test_impact` 与 `coverage-mismatch`（§2.9）
* 声明契约扩展：`declare` 增加 `test_impact ∈ {none, direct, indirect}`；
* 证据链合成思路：`Harness 声明 × CI 运行事实`。若声明 `none` 但 Diff 触及核心逻辑，或声明 `direct` 但 CI 零测试记录，则触发 `coverage-mismatch` 提示（Advisory）；
* 分期安排：P1 仅在 Schema 预留字段，判定算法推迟至 P3 Drift Gate。
* **核验结论**：在契约层面将测试纪律提升为一等公民，与 #855（行为验证补全）理念完全对齐。

---

## 四、 契约权威源分离创新审查（§2.10）

v0.3 提出的文档权威层级极具治理洞察力：

```text
docs/development/ai/execution-contract.md   ← Execution Contract 唯一权威源（Living 契约细则）
        ↑ 最小引用
AGENTS.md / CLAUDE.md / .cursor / .codex    ← 极简启动契约（Bootstrap，保留硬不变量与指针）
        ↑ 决策背景与理由
docs/adr/ADR-0034-*.md                      ← 架构决策记录（Decision Record，历史冻结）
```

* **解决的核心痛点**：ADR 是“历史决策记录”，一旦 Accepted 就不应频繁修改；但执行契约细则（Schema、状态流转边界、命令参数）在多 Harness 实践中需要“持续演进与版本化”。
* **治理收益**：
  1. `execution-contract.md` 成为唯一的执行真理；
  2. `AGENTS.md` 维持在 64 行超轻量水平，只需增加一行指向 `execution-contract.md` 的指针，既不空壳化，也不复制领域细则（守住 S6 门禁）；
  3. ADR-0034 负责记录背景、理由与 Alternatives， Accepted 后封存。

---

## 五、 发现与后续建议（Findings & Actionable Advice）

以下发现均不构成阻断（Non-blocking），建议在 ADR 转为 Accepted 后的 **Phase 0** 或 **Phase 1** 中作为实施细节吸纳：

### 1. 终态流转中的 `ABANDONED` 写入路径需补全（中-低优先级，P1 实施关注）
* **现状**：§2.3 终态集合包含 `MERGED` 与 `ABANDONED`。`MERGED` 已明确绑定 `update` 与 GitHub PR 核对，但 §2.2 列出的 CLI 命令仅有 `declare / status / update / finish`，未显式定义如何将一个任务置为 `ABANDONED`（例如在中途放弃、或 PR 被关闭不合入时）。
* **建议**：在 `execution-contract.md`（P0）或 `ai_work.py`（P1）中增加 `--abandon` 参数（如 `ai_work finish --abandon` 或 `ai_work update --status ABANDONED`），允许人工明确宣布作废并解除 overlap 窗口。

### 2. `coverage-mismatch` 的 CI 证据层级与 2 分钟注意力预算对齐（中-低优先级，P3 关注）
* **现状**：§2.9 提到若声明 `direct` 但 CI 无对应测试运行记录，则报 `coverage-mismatch`。但本仓库主干 PR 路径出于 ~2 分钟注意力预算考量，默认不跑全量 backend tests（由夜间 backstop 兜底）。
* **建议**：在后续 P3 定义 `coverage-mismatch` 规则时，明确“CI 证据”的统计口径包含本地 `run_gates.py check:pr` 运行凭证或全量 CI，避免在轻量级 PR 门禁阶段产生假阳性噪音。

### 3. P1 `STALE` 判定作为派生视图不持久化（低优先级，P1 实施关注）
* **现状**：§2.3 将 `STALE` 描述为字段，而 §2.5 说明 P1 下无守护进程、超时仅在 `status` 提示。
* **建议**：P1 实现中，`liveness` 字段在数据文件中仅记录时间戳与初始状态，`STALE` 作为 `status` 命令根据 `last_seen + TTL` 实时计算出的派生提示（Derived Status），不写回持久化文件；待 P2 Adapter 心跳就绪后再做持久化升格。

### 4. 路径解析命令的鲁棒性加固（低优先级，P1 实施关注）
* **工程提醒**：在不同 Git 版本或深层子目录下，裸跑 `git rev-parse --git-common-dir` 有时可能返回相对路径（如 `../../.git`）。
* **建议**：`ai_work.py` 实现时，统一采用 `git rev-parse --path-format=absolute --git-common-dir`（Git ≥ 2.31）或在 Python 端调用 `os.path.abspath` 进行归一化，确保跨子目录 cwd 解析一致。

### 5. `docs/adr/README.md` 主表补录（低优先级，文档同步）
* **现状**：PR #861 已同步了里程碑看板中的 v0.3 版本号，但 `docs/adr/README.md` 主表 `## 当前 ADR 清单`（行 50–85）仍止于 ADR-0033。
* **建议**：在 ADR-0034 转为 Accepted 的 P0 PR 中，顺带将 ADR-0034 补入主表清单。

---

## 六、 终审定论

ADR-0034 经历了从 v0.1 初稿、v0.2 选择权原则纠偏、到 v0.3 规范加固的严谨推演。当前版本**在哲学原则上高度自洽，在底层文件系统与并发模型上极其扎实，在分期推进路径上高度克制**。

**结论：建议直接裁定 Accepted，依既定路线开启 Phase 0！**
