# ADR-0034 多 Harness 执行契约只读审查（v0.2 → v0.4 三轮闭环）

- 审查对象：`docs/adr/ADR-0034-multi-harness-execution-contract.md`（Proposed v0.2 → v0.3 → v0.4）
- 审查基线：v0.2 = `a6869878`（PR #858/#859）；v0.3 = `55e9a71e`（PR #860/#861）；v0.4 = `1715eee6`（PR #862 八源综合修订 + #863 R6/R18 人工裁决）
- 附带审查：PR #853 约定文件规范化重构（旧锚 `cc18c116` → 新树）内容去向与引用完整性
- 审查日期：2026-09-06
- 产出方：Claude Code（会话 resume `6d0f05`），独立交叉核对 agent ×2；v0.4 轮为八源多 Harness 评审之一（synthesis 权威映射见 `REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`）
- 状态：第一/二轮发现已全部被 v0.4 采纳或正确处置；第三轮结论见 §7

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

## 5. 建议动作（v0.3 轮）

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

---

# 第三轮：v0.4 审查（2026-09-06，八源多 Harness 评审之一）

- 本轮背景：v0.3 后共 8 份独立只读审查（`_0cd302`/`_2873a2`/`_4f6e4b`/`_6d0f05`/`_9261bd`/`_9c124`/`_aa114c`/`_d4a277`，本文件即 `_6d0f05`）→ synthesis 综合裁决（R1–R22 权威映射）→ 用户人工裁决（R6/R18）→ v0.4（#862/#863）。
- 本文件 §1–6 为 v0.2/v0.3 两轮闭环结论（已入库）；以下为第三轮增量审查。

## 7. 第三轮总体判断

**v0.4 是经多源评审循环后的高成熟度版本，第一/二轮全部发现正确关闭（3 项进 synthesis 附录 B 范围外遗留、余者全部采纳落地），新增机制经得起推敲。达到 Accepted 前定稿线。** 剩余为 2 个 P0 contract 阶段覆盖的实现语义观察点（低-中）+ 1 个检索链小项 + 附录 B 3 项仍开。

## 8. 前轮发现 → v0.4 落点核验（第一/二轮关闭确认）

| 前轮发现 | synthesis 编号 | v0.4 落点 | 状态 |
|---|---|---|---|
| A-3 S11 归因串位 | R15 | §1「S1–S10 归 #853、S11 归 #856」 | ✅ 已修 |
| A-4 README 主表无 0034 行 | R14 | README 主表补行（v0.4 摘要行） | ✅ 已修 |
| A-5 drift 豁免未留痕 | R16 | §2.7 P0 行补「drift 对 Agent Note 等强制随附物的豁免规则」 | ✅ 已修 |
| B-1 P0 漏 repository-workflow.md | R10 | §2.7 P0 补接线（harness-adapters + 基线 note + repo-workflow） | ✅ 已修 |
| B-3 §2.6 论证缺口 / §1 措辞 | R21 + 9c124-B3 | §2.6 补「审阅瓶颈未证伪、Registry 提升审计面信息完备性、任务排队主策略不变」；§1 改「同引擎多会话形态」 | ✅ 已修 |
| N-1 僵尸无出口 | R2（7 源共振） | §2.3 僵尸候选清单 + `finish --abandon` | ✅ 已修 |
| N-2 细则双份漂移 | R9（4 源共振） | §2.7 P0「一次性平移、ADR §2 收缩为决策要点 + 指针」 | ✅ 已修 |
| B-4 状态机路径 | R2/R3 | liveness 派生化 + GitHub checks 派生 READY | ✅ 结构性解决 |
| A-1/A-2/B-5（非 ADR 遗留） | synthesis 附录 B ①②③ | 建议另立 docs PR | ⏳ 仍开（待独立 PR） |

## 9. v0.4 新增机制深审（确认）

1. **§2.2 `--path-format=absolute`（R1，6 源独立实测共振）**——修正属实：裸 `--git-common-dir` 在主 checkout 返回 cwd 相对路径（根 `.git` / 子目录 `../../.git`）、linked worktree 返回绝对路径，行为不一致。固定 absolute + 删除「common dir 外替代落点」防多 Registry 分裂，正确。**Registry 按克隆隔离（per-clone）**与派生视图口径一致，自洽。
2. **overlap 数据源 = `declared ∪ derived(diff)`（R5）**——用 09-04 实测反例（声明 `docs`、实际触及 `backend/`/`.github/`）支撑，把「声明不可信」的根本缺陷从架构上兜住：derived 由工具现算，声明只在零 diff 时单独生效。v0.4 最重要的结构改进。
3. **三维状态模型标注「实现选择而非冻结条款」（R6 人工裁决）**——lifecycle 独立字段（FINISHED 只写本字段、不碰 integration，解耦 PR 先后）；integration 增 CLOSED（PR 关未合，GitHub 事实），ABANDONED 移入 lifecycle 且仅显式人工；liveness **查询时派生、不持久化**（R3）——STALE 建模从根上消除「持久 STALE 谁来清」类问题。裁决背景段完整追溯 Contract v1（两维）→ B3 收窄 → 二选一 → 选 lifecycle。
4. **`status` 严格只读（R4）**——观察不改变被观察状态；写命令只刷自身 `last_seen`。防「status 即心跳」污染。
5. **README 派生刷新 + checks 重跑回退 PR_OPEN + 不区分 FIFO 队首（R2）**——与既有 enable-auto-merge 机制对齐。
6. **§2.9 CI 证据口径 = 夜间全量/合并后记录（R13）**——精确修正：PR 路径有意不含全量测试，按 PR checks 判 `direct` 会常态误报。与 ~2min 合入路径预算联动。
7. **test_impact P1 允许缺省 = indirect（R12）**——「不为分类摩擦付协同税」，否决「推迟 P3」保历史采集。
8. **§2.7 P1 启动判据（R22）**——「连续两周 worktree ≥3 或 ≥2 次跨 Harness 撞车返工，未触发维持派生视图用法」。配合 §4 Alternatives 首条改窄口径（「真实缺口比直觉窄」），工具必要性论证收敛到诚实边界。
9. **§3 G2 双边验收（R19）**——验收从「scoped 真身可见」扩为「scoped 真身 + 根启动契约同时可见」，明确 #857 下 scoped symlink 不自动解决根契约供给。
10. **§5 #855 三段触发**（merge=可引用 / Accepted=方向生效 / P0=可开工）。

## 10. 第三轮新观察点（低-中，均可在 P0 contract 阶段覆盖）

- **O-1（中）——`declared ∪ derived` 的过期声明残留面**：union 语义下，A「声明了 foo.py 但最终决定不改」（声明过期）且 derived 非空时，foo.py 仍留在 overlap 集合并对 B 制造提示。v0.4 预埋了「冲突时以 derived 为准」但未定义冲突谓词。建议 P0 contract 定义声明生命周期：`update` 可覆写声明、或「declared 未在 derived 中体现的部分以『未落地声明』单独提示而非参与 overlap」。零 diff 时声明单独生效是对的（唯一不可替代位），但「部分落地」场景的残留需要规则。
- **O-2（低）——lifecycle=ABANDONED × integration=PR_OPEN 组合**：abandon 后记录退出 overlap，但若 PR 未关，其 derived diff 仍在集成窗口。合理语义是 abandon 隐含执行者不再维护该 PR（应同时关 PR 或转手 = 新 execution 重新 declare）。transition table 已列入 P0 contract 必备目录，此处仅提示该组合行需显式定义。
- **O-3（低）——DOC-MAP 检索链**：8 份源 review 未在 DOC-MAP Living 列表逐份登记（只经 ADR 行 → synthesis → 8 源两步可达）。多源 review 用 synthesis 收敛后逐份登记必要性下降，属可接受惯例演化；建议在 synthesis 或 DOC-MAP 注明「源文件经 synthesis 索引」以免未来误判漏登记。

## 11. 第三轮建议动作

1. **Accepted 前无需再改 ADR 正文**（观察点属 P0 contract 目录内细节），可直接走人工评审。
2. **P0 PR 必备目录请带上 O-1（声明生命周期/残留规则）与 O-2（ABANDONED×PR_OPEN 转移行）**——两者恰好落在「transition table + 谓词定义」清单内，补目录条目即可。
3. **范围外遗留仍开**（synthesis 附录 B ①②③ + 本轮 O-3）：建议与 P0 分轨的独立 docs PR——A-1（stall 过期句，文档说谎优先级最高）、A-2（容量公式出处）、B-5（AGENT_SECRET 告警）、O-3（可选）。
4. **流程评价**：八源 synthesis → 人工裁决 → 修订循环运作良好（R 编号唯一映射、共振计数、裁决后补记、范围外遗留单列），与本仓库「Living 审查」文化一致。
