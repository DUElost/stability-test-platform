# 中心存储重排 D 步方案评审一稿（#2188 / zcode · 5aba0）

## 范围与独立性

- 任务：#2188 承载的「方案评审」段（流程第二环：先定方案 → **方案评审** → 落地 → 效果确认 →
  才修订 ADR-0025）。评审对象 =
  [`2026-09-15-center-storage-and-merge-locus-proposal.md`](../notes/architecture/2026-09-15-center-storage-and-merge-locus-proposal.md)
  全文（重点 §2.3 D 步设计，2026-09-16 裁决输入），并按其 §6 清单逐条作答。
- Harness：zcode；会话 `c0254afc-5bb7-4c0a-b157-c4d569d5aba0`；Execution
  `review-2188-dstep-zcode`（role=`review`，test_impact=`none`）。
- **审查基线：`85225bf035e9`**（本 PR 基点 = `origin/main`）。本仓库为多会话共享主工作树，
  评审期间并行会话的 fetch/切换使取证基点在 `50a00c0d` / `3882b341` 间迁移（事故与处置见附录注）；
  本稿引用的 6 个关键文件（`dedup_scan.py` / `dedup_extract.py` / `cron_scheduler.py` /
  `event_uploader.py` / `models/plan_run_artifact.py` / `models/device_log_event.py`）在
  `50a00c0d..85225bf0` 全跨度 `git diff --stat` 为空，结论不受基点迁移影响。
- 独立性边界：只读提案、权威文档、源码、模型与 issue；**未**改代码 / `docs/adr/` /
  issue 结论；唯一写产物 = 本稿与 #2188 的评审结论评论。
- 本稿是**裁决输入**：§6 第 9 条的 W2 接受与否由 owner 拍板，本稿给出明确建议、代价与义务清单。

## 总评

**建议：接受 W2（写侧登记）+ 每 host 分片清单，D 步照推——但第一版载荷按本稿 R-1 收窄为
「仅 R1 scan 产物账」，并附 5 项落地义务（含 1 项 P1 结构性缺口：`_meta/` 必须进 purge 桶清单）。**

§6 前 8 条全部可答：6 条的直接答案已在提案文本或裁决记录里，2 条需补设计决策
（存量 URI 迁移策略、迁移阶段 3 的备份范围），均不阻断 D 步、只约束 A 步落地 PR。
「否决 W2 → 只做 A / 不推 D」的取舍逻辑经代码复核**成立**（W1 循环、W3 两套真值，无第三条路），
但本稿不建议走否决分支——理由与义务见 §6.9 专节。

## §6 逐条回答

| # | 问题 | 回答 | 依据 |
|---|---|---|---|
| 1 | 是否正面回应 §2.0 JIRA 外链硬约束？ | **是**。§2.0 已列为最重要约束；B 的 `delivery` 改名被标记为直接撞线（收益打折）；A/D 不动 `jira/{run}/`；E-5（外链有效率 = 100%）是验收位。**待补**：A 步拆 `dedup/{run}/` 影响存量 `plan_run_artifact.storage_uri`，迁移策略未定 → 见 R-3（建议 TTL 淘汰式，不改写存量行） | §2.0/§2.1 A 行/E-5；`models/plan_run_artifact.py:34`（storage_uri 含 `dedup/{run}` 前缀） |
| 2 | 五项基线真的可测？隔离环境造得出等价规模吗？ | **可测**。E-1/E-1b/E-3 已于 09-15 只读采得（#2188 评论）；E-2 的 DB 对账清单可由控制面 TTL 查询产出；E-4/E-5 口径（时间戳取哪对日志、JIRA 抽样量）需在落地 PR 前定。**365 GB 等价规模无需在隔离环境复刻**——E-1 对 A 应改用「稳态占用」口径（见 R-5），生产只读采集已有先例且被接受 | §1.3；#2188 基线评论（2026-09-16T02:24Z） |
| 3 | 迁移三阶段每步有回滚点？阶段 3 备份范围清楚？ | 阶段 0/1/2 有（无副作用 / 停双写 / 切回旧读）。**阶段 3 备份范围文档自己标注「需在阶段 1 定义」而未定义** → 落地 PR 必须补。另对 A 建议裁剪骨架：A 的目的恰是去双份，阶段 1「双写」不适用，应改为 **TTL 淘汰式迁移**（见 R-3）——新 run 写新路径、存量按保留期自然消亡，回滚 = 停止新路径写入 | §4 |
| 4 | E-1 阈值是否先验合理？ | 阈值不由本评审拍定——owner 已裁「先采数再定」（D-4 阈值行），本评审遵守。但口径要改：**A 的机制是 TTL 分层削峰，不是单 run 体积减小**，E-1 应从「同规模 run 占用降幅」改为「`devices/` 族稳态占用 vs 现状」（短 TTL 生效后的均衡值），否则 A 永远达不到按双份消除设想的降幅 → 见 R-5 | 裁决记录 D-4；§5 E-1 备注「若仅做 A 预期收益有限」 |
| 5 | 是否与在窗 Execution 冲突？ | **否**。`ai_work.py status` 前检 + `declare` 成功（在窗 LIVE 仅 #1520 project_registry 域，与本稿 docs/reviews 范围不相交）；本稿不改 `docs/adr/README.md` 与 ADR-0025 正文 | Execution `review-2188-dstep-zcode` |
| 6 | 是否引出 ADR-0025 之外的新决策？载体？ | 两处：① `devices/` 与 `jira/` 分 TTL 改变 retention 语义——实现先落地，**语义收编进 ADR-0025 修订**（不新立 ADR），落地 PR 的 Agent Note 须显式标注为修订的已绑输入；② W2 使清单成为 Agent↔控制面契约——版本纪律引用 **ADR-0033 §5.4**（不得新增工具私有 env 键），schema 演进规则落在 D 设计文档，随 ADR-0025 修订一并收编 | §2.1 A；§2.3.4 未决②；ADR-0033 v1.2 §5.4 |
| 7 | I-13 方案 B 与 ADR-0033 的依赖顺序是否成立？ | **成立且已裁决**。§3.1 重评充分（B 是 ADR-0033 Phase 2/3 的产物、包存储条件落地三触发均未现、私有路径键被禁）；owner 已裁 B0 并落地 ADR-0027 v1.8 清单第 7 条 + 启动 WARN。无补充 | §3.1；裁决记录 D-5 的 B 段 |
| 8 | 与 ADR-0032 v0.8 是否正交？ | **正交**。v0.8 文档自认「中心存储重排属 ADR-0025 域，与本修订正交」；其正文无 `run_scan_sync` / `_register_scan_artifacts` / glob 依赖（grep 零命中），机制面无耦合。按其要求做了主题查重（本 declare） | v0.8 文档 L233；本稿基线 grep |
| 9 | **W2 + 每 host 分片？四项未决如何回答？** | **接受 W2 + 每 host 分片**；四项未决的答案见下节。否决分支的逻辑成立但不采用 | 下节 |

## §6.9 专节：W2 与四项未决

### W2：接受（裁决输入）

三选项经代码复核无新路径：

| 选项 | 复核结论 |
|---|---|
| W1 控制面单写 | 循环成立：`plan_run_artifact` 行正是 R1 glob 的**产物**（`_register_scan_artifacts_from_nfs` 写入），从它派生清单 = 从发现结果派生发现依据，bootstrap 不存在。回调登记变体 = 新契约，代价 ≥ W2 还多一跳 |
| **W2 写侧登记** | **唯一能消掉 R1 的选项**：声明替代事后发现 |
| W3 混合 | 保留兜底 = 保留 R1 的 glob + 文件名约定路径，两套真值永存，最贵 |

**接受的代价必须诚实入账**：W2 使 D 从「控制面单侧、不动目录树、可独立先行」升格为
**契约动作**（动 Agent 写路径）。裁决记录「D 先行」的成本前提（改动面小）**部分失效**；
收益端同步上修（R1 的 glob + 文件名约定整段消失；R2 按 R-1 可顺带 DB 化）。净效果仍为正，
建议 owner 重申 D → A 时按新成本口径确认，而非沿用 09-15 的「D 最便宜」印象。

### 四项未决

**① 权威还是缓存 → 清单是写侧的「登记账」；读者按权威对待，glob 降级为事故恢复工具。**

- 「缓存 + 保留 glob 兜底」不成立：兜底在位 = R1 代码路径在位 = D 的结构判据
  （不再遍历中心盘、文件名约定依赖消失）永远不满足，D 收益归零。
- 「权威」必须配三条失败语义，否则重演「写产物成功但登记失败 = 静默丢交付物」：
  (a) **分片写入是上送动作的完成标志**——先写产物文件、后写分片，分片写失败则整个上送动作按失败
  处理、整段重试（写与登记都幂等：同 URI 覆盖写、分片重写）；「文件在、分片缺」的窗口被压缩到
  「写者中途崩溃」，由重试自愈；(b) 分片在 = 交付完成，读者不二次验证文件存在性
  （存储层丢文件是事故，走告警不走兜底）；(c) 分片事后丢失（存储事故）→ **一次性**运维 rescan 工具
  重建（即今天的 glob 逻辑降级为该工具的实现），不是常驻读者路径。
- 与 §2.3.3 生命周期硬约束相容：purge **永不读清单**（DB 行仍是「哪些目录属于此 run」的唯一索引）；
  `plan_run_artifact` 行独立持久，可反向重建已登记部分的清单；「已写未登记」的残余窗口与今天的
  「已写未 glob」窗口同构，不引入新风险。
- **新增义务（P1，R-2）**：`_meta/{run}/` 必须进 `purge_run_storage_dirs` 的桶清单——
  现清单写死 `("devices", "dedup", "jira")`（`cron_scheduler.py:289`，`jobs/` 另行展开），
  新族不入清单 = retention 永不清 `_meta/`，E-2（残留 = 0）结构性必挂。先文件后行顺序不变，附测试。

**② 版本与降级 → 分片带 `schema_version`；「这一版不写」的检测搭既有通道；降级显式过渡、带终态出口。**

- 分片文件头带整数 `schema_version`（演进用）。
- 「还没写」vs「这一版不写」**不得**靠超时/时机推断，也不得新增工具私有 env 键（ADR-0033 §5.4）。
  两个候选机制，D 设计定稿时二选一：
  (a) scan 动作的既有回执/step_trace 增加清单字段（如 `manifest_shards`）——字段缺 = 旧 Agent；
  (b) 契约切换日 + 分片存在性判定，混编 run（滚动升级中同 run 新旧 Agent 并存）按 host 粒度兜底。
- 降级路径 = 对「无清单信号」的主机沿用现 glob 登记，**显式标注为过渡**：终态出口 =
  全队切换后删除 glob 登记路径，退出判据 = 「glob 兜底命中计数 = 0 跨整观察窗」；
  并落**契约测试**锁定主路径不变量（见 R-4）。

**③ 粒度与容量 → 每 host 分片（`_meta/{run}/{host_id}.json`）+ 片内每产物一条；载荷收窄后容量顾虑消失。**

- 每 host 分片维持 §2.3.3 的无锁并发模型（文件名即分片键的显式化），且让 ② 的 per-host 判定成为可能。
- **载荷收窄（R-1，本评审对 §2.3.4 建议的唯一实质修正）**：第一版清单只承载 **R1 的 scan 产物账**
  （host / 平台分区 / family 内相对键）。R2 需要的「事件目录位置」**DB 已有**：DLE 行自带
  `remote_path`（`models/device_log_event.py:35`），且 retention「先文件后行」保证行在文件在、
  行是 extract 的既定真值（「extract 只认 DLE remote_path」）——R2 的跨 run glob 可改为
  **按 event_id 查 DLE** 的 DB 查询，无需清单承载。前提（可修复形态的代表 id 必存在历史 DLE 行）
  经读码推断成立：glob 救不了的形态（该 id 从未上送过）今天也是坏链，不因 D 变差；
  **落地设计前以生产只读查询复核一次**。若复核不成立，R2 位置索引回到第一版载荷——
  只影响清单大小，不动 W2 结论。
- 量级核对：E-1 基线 10,725 文件 / 21 run ≈ 500 文件/run，分片 ≈ 48 台 × 小文件/run，
  聚合读成本远低于现「遍历 + 正则 + 逐条查库去重」。

**④ 与 §2.0 外链约束 → 清单不承载引用职责：记 family 内相对键，不记完整 URI；不出现「规范名」字段。**

- 第一版清单的读者（scan 完备性登记）与 JIRA 引用（`jira/{run}/`）无交集；清单**不记录任何
  `jira/` 路径**，§2.0 约束对 D 天然满足。
- scan 产物只记 **family 内相对键 + host + 平台分区**，完整路径由控制面拼装——A 步未来把
  `dedup/` 拆 `report/scan|merge` 时只动控制面一处的 family 前缀，清单与历史 URI 不受牵连，
  也不给未来重命名提供「第二真值」。

## 新增发现（评审产出；R-1/R-2 为落地义务，其余约束对应落地 PR）

- **R-1（P1，载荷收窄）**：D 第一版清单只承载 R1 scan 产物账；R2 改查 DLE（依据与前提复核见未决③）。
- **R-2（P1，purge 桶）**：`_meta/` 进 `purge_run_storage_dirs` 桶清单 + 测试；漏掉 = E-2 结构性必挂。
- **R-3（P1，A 步前置）**：存量 URI 迁移策略 = TTL 淘汰式（新 run 写新路径、存量按保留期消亡、
  过渡期 retention 同时覆盖 `dedup/` 与 `report/` 两族、存量行不改写）；A 落地 PR 必须写明
  双族并存期与备份范围（§4 阶段 3 的未定义项一并补）。
- **R-4（P2，回归锚）**：结构判据要落成契约测试——断「清单读者不遍历中心盘」与
  「`_HOST_PREFIX_RE` 文件名约定依赖不在主路径」（断不变量而非行为快照），防兜底路径无声回归为常驻读者。
- **R-5（P3，口径）**：E-1 对 A 改用稳态占用口径；基线按 #2188 评论自认的时点漂移在 A 落地前重采。

## 与台账的关系 / 后续

- owner 对 §6.9 拍板（建议：接受 W2 + 载荷收窄）→ D 设计定稿（吸收 R-1/R-2/R-4）→ 落地
  （Agent Note + check:quick）→ 效果对比 → ADR-0025 修订。流程不可倒置（§Decision 1）。
- R-1 的前提复核若不成立 → R2 位置索引回到第一版载荷，其余结论不变。
- 本评审不触发 ADR-0025 修订、不改 R1/R2 现行行为（§2.3 边界同）。

## 附录：证据命令与结果（PR 基点 `85225bf0`）

```bash
git diff --stat 50a00c0d..85225bf0 -- backend/services/dedup_scan.py \
  backend/services/dedup_extract.py backend/scheduler/cron_scheduler.py \
  backend/agent/event_uploader.py backend/models/plan_run_artifact.py \
  backend/models/device_log_event.py                                     # 空 = 关键文件全跨度无漂移
sed -n '60,135p;160,175p;955,995p' backend/services/dedup_scan.py   # R1 glob+RE+约定 / scan_completeness(DB) / R2 跨 run glob
sed -n '240,300p' backend/scheduler/cron_scheduler.py          # purge 单一索引声明 + 桶清单写死 ("devices","dedup","jira")
sed -n '385,418p' backend/agent/event_uploader.py              # Agent 写侧：event_id 隔离、extract 只认 DLE remote_path
grep -n "remote_path" backend/models/device_log_event.py       # :35 行↔目录映射在 DB
.venv/bin/python tools/dev/ai_work.py status && declare ...    # 前检无冲突，declare 成功
```

> **事故注（共享工作树并发会话）**：评审中段并行会话在本主工作树内切换分支，本 Execution 的
> 提交一度落在本地 `main`（`d5d351c1`）、分支引用停在旧基点。处置：独立 worktree
> （`/tmp/stp-2188-review`）基于 `origin/main` 重建分支 + cherry-pick，本地 `main` 复位回
> `origin/main`，全程未触碰并行会话的分支。判据沉淀：**多会话共享树上，任何 git 写操作前先
> `git branch --show-current` 验落点；交付类工作直接用独立 worktree**。
