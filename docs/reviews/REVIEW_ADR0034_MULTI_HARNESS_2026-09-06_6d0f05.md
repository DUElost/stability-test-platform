# ADR-0034 多 Harness 执行契约只读审查（v0.2 + v0.3 两轮闭环）

- 审查对象：`docs/adr/ADR-0034-multi-harness-execution-contract.md`（Proposed v0.2 → v0.3）
- 审查基线：v0.2 = `a6869878`（PR #858/#859）；v0.3 = `55e9a71e`（PR #860/#861）
- 附带审查：PR #853 约定文件规范化重构（旧锚 `cc18c116` → 新树）内容去向与引用完整性
- 审查日期：2026-09-06
- 产出方：Claude Code（会话 resume `6d0f05`），独立交叉核对 agent ×2
- 状态：结论已交人工评审；建议动作见文末

---

## 1. 总体结论

- **#853 规范化重构无实质信息丢失**，无安全边界缺口；引用 174 条相对链接 0 断链；25 节旧内容去向矩阵全覆盖。
- **ADR-0034 v0.2 正确回应上轮审查 G3–G6**；v0.3 是实质加固版，两维正交状态模型与事实来源分层使设计质量显著高于 v0.2，**达到人工评审定稿线**。
- 剩余问题全部为轻微文档级修补：1 处一行级归因错误、1 处登记半修、2 处建议级新观察、3 处非本 PR 遗留文档项。无阻断项。

## 2. 审查方法

三轨并行：

| 轨 | 内容 | 执行 |
|---|---|---|
| A | ADR-0034 本体逐节语义审查，对上轮 G1–G6 与方案书 48 节逐点核对 | 主审 |
| B | 旧 AGENTS.md（285 行）/ CLAUDE.md（115 行）/ DOC-MAP.md 逐节 → 新树去向矩阵（10 组高风险项逐条给结论） | 独立 agent |
| C | 16 文件 174 条相对链接可达性 + ADR/note 登记核对 + gh issue/PR 实证（#854/#855/#857、#853/#856/#858/#859） | 独立 agent |

## 3. 确认项（通过核验）

### 3.1 #853 重构去向（节选，完整矩阵见审查过程记录）

- 派生视图命令**逐字保留**于 `docs/development/repository-workflow.md:30-37`；元文件串行化在 `AGENTS.md:36` + `harness-adapters.md:48` + `repository-workflow.md:25`
- 状态机/终态协议 → `docs/design/07-execution-protocol.md`；脚本契约 → `docs/development/script-versioning.md`（409 硬守卫、逃生阀限制、不可变门禁、三处注入全收）
- 生产边界接收文档为旧文**超集**（`testing.md` 新增 #123 mock 陷阱等）；安全关键项 8/8 保真（生产库红线提升为 `AGENTS.md:11-12` 首屏总原则）
- env 键分层出处成立（`.env.example` 权威 + `environment-variables.md` 精要，doc 头部明示）
- 引用完整性：0 断链；issue/PR 实证全过；AGENTS.md 63 行/3815B 实测一致

### 3.2 v0.3 实质增强

1. **§2.2 Registry root = `$(git rev-parse --git-common-dir)/ai-work/`**——linked worktree 经 common dir 天然解析到同一位置，不依赖「主 checkout 不换位」隐式假设；落 `.git` 内天然免跟踪；`registry.lock` 与数据同目录满足原子 rename FS 约束。方案书 §17 硬约束全部落实。
2. **§2.3 liveness × integration 两维正交**——overlap = integration ∈ {NO_PR, PR_OPEN, READY}，liveness 不参与；反例（finish 后 STALE 的在途 PR 仍是集成风险窗口）论证成立。修正方案书 §19「仅 ACTIVE 参与」与 v0.2 状态机的语义错误。
3. **§2.3 事实来源分层表**——declaration→Registry / diff→Git / PR lifecycle→GitHub / verification→CI；**MERGED 只能由 GitHub PR 状态确认**，`finish` 语义收窄为「停止编码且已开 PR」（NO_PR→PR_OPEN），`ai_work` 不得单方面写终态。
4. **§2.5 TTL 分期**——P1 声明式 CLI 无可靠心跳源，`last_seen` 仅 advisory、STALE 不改语义不退出集成窗口；P2 heartbeat 后升格。
5. **§2.8 Scope MVP 边界**（repo-relative file/directory 归一化、禁 glob/ownership/locking、Role 非文件 ownership 边界）+ **§2.9 test_impact{none,direct,indirect} × CI 证据 → coverage-mismatch advisory**（Contract 先定义、实现后置 P3）。
6. **§2.10 contract 文档分家**——`docs/development/ai/execution-contract.md` 为执行细则唯一权威源，ADR = 方向裁决记录，AGENTS.md 保持 minimal bootstrap 不空壳化。
7. Alternatives 补两行否决记录（overlap 仅看 ACTIVE / P1 即硬 TTL），决策闭环完整。

## 4. 发现清单

### 4.1 上轮发现核对（v0.2 → v0.3）

| # | 发现 | 状态 |
|---|---|---|
| A-3 | `ADR-0034:20`「S1-S11 门禁归 PR #853」归因串位——S11 由 PR #856 引入（09-06 `8aad18d1`），#853 合入时基线为 S1–S10 | **未修，仍在**（一行级） |
| A-4 | README 看板行版本滞后 | **半修**：看板行已同步 v0.3（#861）✓；主登记表「当前 ADR 清单」**仍无 ADR-0034 行**（对照 Proposed ADR-0011 在主表先例） |
| A-5 | drift 判定细则（声明载体 + 强制随附物豁免）未留痕 | 部分：§2.9 定义 coverage-mismatch；**Agent Note 等强制随附物与 drift 比对的豁免仍未留痕** |
| B-1 | P0 接线清单漏 `repository-workflow.md` | **未修**：P0 仍只列 harness-adapters.md + 基线 note |
| B-4 | 状态机 STALE 路径 / 推进机制 | **超越式解决**（两维模型 + GitHub 权威） |

### 4.2 v0.3 新观察（建议级）

- **N-1 僵尸条目无出口路径**：liveness 永 advisory + 终态只能人工/GitHub 推进，「declare 后从未 finish、进程消失、worktree 删除」的 NO_PR 僵尸永远留在 overlap 集合（§2.3 NO_PR 参与 overlap），§2.5 明确不剔除不降级，但未定义「人工裁决的落点动作」。建议 §2.5 补一行：人工经显式 `abandon`（或归档）清理，`status` 提供候选清单。
- **N-2 细则双份漂移风险（§2.10 引入）**：ADR §2.2–2.9 已写入细则，P0 又要建 execution-contract.md 承载完整规范——本仓库 ADR 惯例是持续版本化非冻结，两份细则并存会漂。建议 P0 建 contract 文档时一次性把细则从 ADR 迁入、ADR 正文收为决策摘要 + 指针，不复制基线。

### 4.3 遗留提醒（非 ADR-0034 PR 范围，上轮已报仍开着）

| # | 内容 | 位置 |
|---|---|---|
| A-1 | 两层钟 schema 门过期句（文档说谎）：仍写「`minimum:1` 门保持关闭」，与 2026-08-04 已开 `1→0` + `stall≥1` 约束矛盾 | `docs/design/2026-08-step-stall-detection.md:88-89`（正确事实仅存 `pipeline_engine.py:101-108` docstring） |
| A-2 | 容量公式去向缺口：`effective_slots = min(空闲健康设备 − 活跃, 健康上限, STP_MAX_CLAIM_SLOTS)`（#483，migration `q2r3s4t5u6v7`）无常驻文档出处 | 仅 09-04 审计 note 留痕；建议落一处常驻开发文档 |
| B-5 | 「backend/.env 的 AGENT_SECRET 是陈旧值，控制面与 20 台 Agent 都不认」具体告警未随迁 | `docs/operations/production-diagnostics.md`（现只余通用「不得互相代用」） |

## 5. 建议动作

1. **ADR-0034 Accepted 前**（一行级）：修 A-3 归因（#853 → 现状 S1-S11 含 #856）；补 N-1 僵尸出口句；A-5 豁免规则在 §2.9 或 P0 留半行。
2. **README**：主表补 ADR-0034 行，或显式决定「等 Accepted 与 supersede 标注同 PR」——不要无声遗漏。
3. **P0 PR 时**：N-2 细则一次性迁移方式；B-1 接线补 `repository-workflow.md`；A-5 豁免规则入 contract 文档初稿（`docs/notes/` 等强制随附物）。
4. **独立文档 PR**：A-1 / A-2 / B-5 三项非 ADR 遗留可随 P0 或另行处理。

## 6. 上轮 G1–G6 回应终态

| 上轮审查点 | v0.3 终态 |
|---|---|
| G1 声明载体（声明如何到 CI） | P3 drift gate 本地 advisory 化绕开（可辩护）；CI 化时 carrier 另行裁决——建议 Revisit 留痕 |
| G2 Agent Note 豁免 | 未处理（见 A-5） |
| G3 活性锚 git 而非 update 纪律 | §2.3/§2.5 分期：integration 维度承担风险窗口判定（GitHub 权威），`last_seen` P1 advisory / P2 升格——结构性解决 |
| G4 与 2026-09-04 约定显式裁决 | ✓ 取代对象声明 + P0 supersede 标注 + 两处内建转移条款 |
| G5 required vs advisory | ✓ P3 advisory 先行 + 转 required 独立裁决 + 守 ~2min 合入路径预算 + 不建 merge queue |
| G6 Registry 定位（非冲突规避主干） | ✓ §2.4 diff 优先、派生视图 ground truth、Registry 从不上锁 |
