# R01–R15 覆盖、已关闭修复与多 Harness 模式三问确认

- 日期：2026-09-11
- Harness：Cursor
- 会话：`3c889cae-90c3-4718-a07b-2339ec4e188e`（后六位 `4e188e`）
- 本地审查起点：`f9a21b0f5b7738cc684a6d1e3215074f7c005a72`
- 查询时远端 `origin/main`：`bd921d2f2bb856b66a91f796eab7e09ef8293aed`
- GitHub 仓库：`DUElost/stability-test-platform`
- 性质：针对三个问题及补充组合治理问题的独立证据化确认稿，供后续多 Harness
  综合审查使用
- 上位入口：[全面只读审查总纲](./PROJECT_REVIEW_PLAN.md)、
  [ADR-0034](../adr/ADR-0034-multi-harness-execution-contract.md)、
  [AI Execution Contract](../development/ai/execution-contract.md)
- 过程记录：[Agent Note](../notes/process/2026-09-11-three-question-confirmation-4e188e.md)

> 本文不是最终综合裁决，不构成全面审查交付完成、缺陷修复完成、动态验证通过或
> 生产验收通过的声明。本文没有修改代码、Issue、PR 或 ADR。

## 0. 三问结论

| 问题 | 结论 |
|---|---|
| R01–R15 之外是否还有未覆盖面 | **有，但主要不是新的模块分区。** R01–R15 已覆盖主要静态模块分类；尚未闭合的是跨区端到端、动态故障、容量长跑、混合版本、真机与生产验收等横切轨。无需机械新增 R16。 |
| 已关闭 Issue 的修复是否准确、有效、可持续 | **样本整体有实质收益，但不能对全部 CLOSED Issue 整体背书。** 17 项分层目的抽样中，13 项在声明范围内属根因修复，2 项修复不完整，1 项明确止血，1 项真实环境待验证。该样本不能外推为全仓百分比。 |
| 现行多 Harness 修复模式是否准确、有效、可持续 | **方向正确、闭环不完整、可持续性有条件成立。** worktree、Registry、PR、strict checks 与 FIFO 解决了并行编码和 Git 集成问题，但没有自动解决语义复核、组合回归、生产验收、优先级和 Revisit 收割。 |

第一性原理判据不是 Issue 或 PR 的状态，而是：

```text
真实错误路径被堵住
  + 系统不变量被自动化证据锁住
  + 相邻调用方与组合行为未被破坏
  + 对应运行环境完成适配级验证
  + 残余风险有 owner、期限和终态出口
```

长期复利判据是同类缺陷复发率、验证成本和概念数量下降，而不是 CLOSED、PR、ADR、
规则、文档或 Harness 数量增加。

## 1. 范围、方法与证据边界

### 1.1 已检查的证据

- `PROJECT_REVIEW_PLAN.md`、`DOC-MAP.md`、ADR-0034、Execution Contract、
  repository workflow、CI workflows 与关键实现/测试；
- R01–R15 GitHub 总表：#891、#910、#945、#961、#979、#996、#1015、
  #1031、#1055、#1086、#1125、#1201、#1230、#1266、#1302；
- GitHub 当前 Issue、PR、branch protection、Actions、#1035 与 #1246；
- 17 个已关闭 Issue 的 Issue/PR/代码/测试/follow-up 分层抽样；
- 三个关键反例：#901、#1123、#1273。

### 1.2 GitHub 快照

查询时间为 2026-09-11 10:38–10:48（UTC+8），查询不是跨接口事务快照：

| 项目 | 数量 |
|---|---:|
| Open Issue | 227 |
| Closed Issue | 366 |
| Merged PR | 718 |
| 2026-09-06 至 2026-09-11 新建 Issue | 243 |
| 上述新建 Issue 中已关闭 | 168 |
| 2026-09-06 至 2026-09-11 新建 PR | 229 |
| 上述新建 PR 中已合入 | 218 |

密集审查期新增 243 个 Issue、其中 75 个尚未关闭，是短期 intake 高于收口能力的
积压信号；不能据此直接外推长期生产率。

### 1.3 限制

- 未逐一动态验收全部 366 个 closed Issue；
- 未连接生产数据库、Redis、NFS、真实设备或主机；
- 未执行迁移、部署、真机、混沌或全量测试；
- 本地 `main` 落后 `origin/main` 65 个提交，关键 #901/#1123 文件经
  `git diff HEAD..origin/main` 核对无变化；
- 工作区存在其他 Execution 的未提交改动，本文不把这些改动包装成远端事实；
- 下文 17 项为风险导向的分层目的抽样，不是随机统计样本。

因此，已有报告中“准确性约 85%”“有效性约 90%”“84% 修复带测试”等数字，在没有
稳定抽样框、分母、评分规则和复核记录前，不应作为项目级质量结论。

## 2. Q1：R01–R15 之外还有哪些审查面

### 2.1 分类覆盖与交付完成必须分开

R01–R15 已按控制面、Agent、前端、部署和治理覆盖主要静态模块分类，15 个 GitHub
总表均存在。总纲 §6 仍明确把以下内容列为全面审查完成的硬条件：

1. 执行链端到端收口；
2. 日志链端到端收口；
3. 基线差异与修复后的增量补审；
4. 未修复项、待验证项和排除项的独立保留。

因此，准确表述应是“R01–R15 分区静态审查和 Issue 登记已开展”，而不是“全面审查
已经完成”。

### 2.2 未闭合横切面

| 级别 | 横切面 | 最小验收 |
|---|---|---|
| P0 | 执行链收口 | Plan/Suite → 快照/派发 → lease/claim → Agent → complete ACK → 聚合 → 实时/页面；覆盖 abort、超时、断连重启、重复和迟到回报 |
| P0 | 日志链收口 | 异常发现 → 本地事实 → 上报/上传 → scan/merge → 归档/提取 → 报告/JIRA/页面；覆盖存储不可用、晚到、补偿、跨平台和 prune |
| P0 | 统一基线与增量补审 | 记录各区审查 SHA，修复触及旧结论时重新检查不变量与调用方 |
| P0 | 生产与规模验收 | 生产部署、20-host 主链、恢复与真实操作证据；与代码审查分轨 |
| P1 | 真实依赖故障 | Redis/PostgreSQL 黑洞、连接拒绝、NFS 卡死、磁盘满、队列积压、进程 SIGKILL |
| P1 | 重连风暴 | Agent 与前端重连退避加入 jitter，并在多实例同时恢复场景验证 |
| P1 | 混合版本 | 新控制面 + 旧 Agent 并行 claim/dispatch、升级门禁与回滚 |
| P1 | 真机与外设 | USB 掉电、ADB daemon 僵死、Fastboot/刷机半途断电、AEE 抓取 |
| P1 | 客户端写幂等 | 重试 POST/PATCH 不重复创建或重复触发副作用 |
| P2 | 长跑与资源泄漏 | 7×24 soak、FD/线程/锁表/集合增长、恢复时间与资源上限 |
| P2 | 时间语义 | NTP 前跳/回跳、时区、naive datetime、超时统一使用单调时钟 |
| P2 | 历史升级与回滚 | 代表性旧 revision → head、旧 Plan/脚本引用、配置/依赖/代码回滚兼容 |
| P2 | 容量与背压 | 设备、Job、日志、通知、SAQ 和数据库连接的容量预算与降级行为 |

### 2.3 可复现主张与需收窄主张

本轮独立复核支持：

- 后端 API 路由约 104 个写端点，未发现客户端提供的 `Idempotency-Key` /
  `X-Request-Id` 统一契约；
- Agent 与前端重连业务代码没有 jitter；
- 没有 toxiproxy 或同类真实混沌测试基础设施；
- `backend/main.py` 的 Redis 业务连接缺少统一的 socket connect/read timeout；
- `backend/realtime/log_writer.py` 的 per-job lock 映射无淘汰；
- 多个脚本仍使用 `time.time()` 做超时截止判断。

“系统完全没有依赖超时”不成立：readiness、Agent HTTP 等路径已有 timeout。应收窄为：
**关键依赖缺少统一断死契约，Redis 业务连接未设置明确 socket timeout，且没有真实
网络黑洞验证。**

### 2.4 是否新增 R16

不建议机械新增 R16–R20。上述缺口应分别进入：

- 总纲 §6 跨区收口轨；
- 动态故障/容量/soak 验证轨；
- 产品与运维验收轨；
- 既有 ADR 实施轨或按共同不变量建立的治理主题；
- pre-R #827、全景风险 #777 等平行台账的去重汇聚。

新增编号不能替代横切验证，反而可能制造第二套分类和台账漂移。

## 3. Q2：已关闭 Issue 修复质量

### 3.1 抽样方法

抽样覆盖：

- 用户关注的安全、并发、状态机、存储、脚本、CI 修复；
- R01–R15 各区域至少一个代表项；
- 高风险与低风险、近期修复和 follow-up 修复链；
- 正向根因修复、合理边界、止血和真实环境待验证。

### 3.2 抽样分类

| 分类 | 数量 | 代表 |
|---|---:|---|
| 在声明范围内属于根因修复 | 13 | #1215、#1214、#890、#1028、#1172、#1074、#1203、#938、#958、#966、#986、#1042、#1195 |
| 修复不完整 | 2 | #901、#1123 |
| 明确过渡止血 | 1 | #1273 |
| 真实环境待验证 | 1 | #1256 |

“根因修复”只对对应 Issue 的已声明错误路径成立，不自动表示生产采用、整个 Epic、
相邻治理包或真实环境已经完成。

### 3.3 值得复用的长期资产

| Issue | 可持续原因 |
|---|---|
| [#1215](https://github.com/DUElost/stability-test-platform/issues/1215) | 审批/执行使用带 expected status 的数据库条件 UPDATE，以 `rowcount == 1` 作为唯一胜者，避免进程内锁冒充分布式权威 |
| [#1214](https://github.com/DUElost/stability-test-platform/issues/1214) | 在数据进入日志/LLM/回执前通过统一结构化入口脱敏，减少散落字符串补丁 |
| [#890](https://github.com/DUElost/stability-test-platform/issues/890) | leader election 的数据库异常 fail-closed，并同步 ADR；策略覆盖一族后续任务 |
| [#1074](https://github.com/DUElost/stability-test-platform/issues/1074) | 中心存储已配置时发布失败显式失败，不再回退本机并伪报成功 |
| [#1203](https://github.com/DUElost/stability-test-platform/issues/1203) | 清理与执行所有权绑定，迟到旧执行者不能删除继任者占位 |
| [#1042](https://github.com/DUElost/stability-test-platform/issues/1042) | 本地持久 outbox + 稳定事件身份 + 控制面幂等，符合“瞬时通信不承载业务事实” |

这些修复的共同点是：恢复单一事实源、显式错误语义或所有权不变量，并以契约级回归
覆盖，而不是只让当前调用成功。

### 3.4 两个“关闭但修复不完整”的确定反例

#### F01：#901 并发 refresh 仍可多次签发

[Issue #901](https://github.com/DUElost/stability-test-platform/issues/901) 的验收包含
“重放/并发刷新回归通过”。当前实现：

1. `refresh()` 先调用 `is_revoked()`；
2. 两个请求可以同时看到未撤销；
3. `revoke()` 用 `ON CONFLICT DO NOTHING RETURNING` 返回是否首次插入；
4. `refresh()` 忽略该布尔返回值，无论首次消费是否成功都继续签发 token。

因此存在可达交错：A/B 均通过预检查，A 成功插入，B 冲突返回 `False`，A/B 仍都
签发新会话。后续 #1039 的浏览器 Web Lock 只能减少单客户端竞争，不能成为服务端
安全不变量的权威。

判定：**#901 已关闭，但其并发验收未兑现。** 最小出口是仅允许原子消费成功者签发，
并补并发 refresh、refresh/logout 竞争测试。

#### F02：#1123 在真正取消时提前释放互斥

[Issue #1123](https://github.com/DUElost/stability-test-platform/issues/1123) 的验收要求
“重试不与上一轮副作用重叠”。当前 `_run_sync_exclusive()`：

1. 获取进程内 guard；
2. `await asyncio_to_thread(fn, ...)`；
3. 在外层协程 `finally` 中释放 guard。

取消外层协程会立即进入 `finally`，但 Python 无法终止已经运行的同步线程。于是 guard
已释放，旧线程仍运行，重试可以进入同一个 key。现有测试覆盖手工占位、等待、正常
结束和函数异常，没有覆盖“真实线程已启动后取消”的交错。

判定：**#1123 已关闭，但直接违反自身验收；该缺陷在单进程即可发生，不必等待多
worker。** 最小出口是把 guard 生命周期绑定到实际同步工作完成，并覆盖取消、提交
失败和 worker 异常出口。

### 3.5 明确止血与待验证

- [#1273](https://github.com/DUElost/stability-test-platform/issues/1273)：TRUNCATE
  `DeadlockDetected` 三次退避重试可降低测试噪声，但 Note 已明确未定位泄漏会话/
  后台线程。它是有边界、有价值的止血，不是终态根因修复。
- [#1256](https://github.com/DUElost/stability-test-platform/issues/1256)：部署模板和
  静态测试形成了可执行约束，但“干净主机可部署”仍需要真实环境记录。

### 3.6 修复是否准确、有效、可持续

| 维度 | 判定 |
|---|---|
| 准确性 | 样本多数对准真实错误路径；#901/#1123 证明 closed 状态和局部测试仍可能遗漏验收交错 |
| 有效性 | required checks 与定向回归显著降低已知复发；PR 轻量 CI、Mock 和静态验证不能外推到全量、真机或生产 |
| 可持续性 | CAS、单一脱敏入口、持久 outbox、所有权约束、ADR/Note 可形成复利；过渡止血、follow-up 波浪和未收割 Revisit 会形成负复利 |

项目级不能用单一百分比描述质量。更可靠的指标是：

- 同类缺陷 30 天复发率；
- closed 后被重开或产生同根因 follow-up 的比例；
- 高风险修复的验收条件兑现率；
- main 全量逃逸率和恢复时长；
- 过渡项按期删除/升格/显式延期的比例；
- 生产类 Issue 关闭时对应版本和运行证据的完整率。

## 4. Q3：多 Harness 修复模式

### 4.1 应保留的骨架

| 机制 | 价值 |
|---|---|
| Issue 溯源与可失败验收 | 给 Requirement 建立边界与完成判据 |
| 独立 worktree/branch | 物理隔离文件状态，降低并行污染 |
| Registry issue 查重、scope 与 derived diff | 提前暴露双领、范围漂移和集成窗口 |
| strict branch protection | 每个 PR 基于当前 main 重验，降低旧基线直接合入 |
| FIFO auto-merge | 串行化主干集成，减少同时推进导致的组合不确定性 |
| Agent Note / ADR | 保留决策、替代、验证与重议条件 |

这些机制解决的是并行开发协调和 Git 集成，不是产品语义的自动正确性证明。

### 4.2 当前闭环缺口

#### W1：FIFO 红队首缺少解毒出口

[Issue #1246](https://github.com/DUElost/stability-test-platform/issues/1246) 已记录
#1205 的 required check 持续失败，阻塞 12 个以上后续 PR。队列脚本只处理队首，
队首非 SUCCESS 时退出；缺少告警、人工隔离/让位和恢复顺序。

严格 FIFO 本身不是错误；错误是发生红队首时只有静默停摆。自动跳过同样不能直接
采用，因为它改变顺序语义。最小方案是：

1. 持续失败 10–15 分钟告警；
2. 人工确认后转 draft 或加受审计 quarantine 标签；
3. 后续 PR 才能前进；
4. 原 PR 修复后按明确规则重新入队；
5. 不绕过 required checks，不手工 merge。

若改变 FIFO 方向语义，应先修订 ADR-0034/工作流契约。

#### W2：PR 绿色不是全量验证

当前 required checks 为：

- `lint`
- `CodeQL`
- `pr-typecheck`
- `pr-compileall`
- `pr-agent-tests`
- `pr-migrate-empty-db`

PR 路径明确不运行完整 `backend/tests/`、完整 Vitest/前端 build 和 Docker build，
由夜间 main backstop 承担。该取舍有同步注意力预算依据，但意味着：

- PR 绿色只能证明轻量门禁通过；
- main 可在夜间检查前携带全量回归；
- Harness 在合入即退出时，上下文和责任链早于深度验证结束。

不建议对所有 PR 强塞全量检查。应按风险触发影响面测试，并在密集合入期增加
coalesced main checkpoint，将深度失败发现时延控制在约一小时，而不是固定等到夜间。

#### W3：高风险变更没有强制独立语义复核

branch protection 当前要求 0 个 approving review，未启用 Code Owner review；
PR-Agent 是 advisory。不能据此断言作者没有自审，但可以确认系统没有强制独立复核。

建议：

- 鉴权、授权、事务、状态机、迁移、执行协议、部署与 CI 权限变更：独立复核 100%；
- 普通低风险 PR：保留快速路径，并进行随机抽检；
- 第二 Harness 可提供独立技术意见，但方向裁决与风险接受仍由人负责。

#### W4：自由认领容易优化关单数而非风险下降

2026-09-06 至 09-11 新增 243 个 Issue、218 个 PR 合入，说明编码吞吐很高；复杂主链
问题仍可能被大量简单 P2 淹没。Harness 自行认领应受人类维护的全局优先级约束：

- 安全、数据正确性、主链不可完成优先；
- P0 必须稀缺，不能覆盖绝大多数开放项；
- 设计风险先确认触发前提，不自动转实现单；
- 未定义共同语义时先做组合设计，孤立 path-level bug 继续走小 PR。

#### W5：“合入 main → 需求结束”过早

结束条件必须按交付类型分层：

| 类型 | 结束条件 |
|---|---|
| 代码缺陷 | 合入 + 定向回归 + 适用的 main checkpoint + Issue/Registry 核销 |
| 生产/部署缺陷 | 上述条件 + 对应版本/环境的运行证据 |
| 真机/硬件问题 | 上述条件 + 设备矩阵或 signoff |
| 风险接受 | 决策者、残余风险、检测来源、升级触发和复议日期 |
| 临时止血 | 伤害已受控 + 根因承载项 + owner + review_by + 可测删除条件 |

### 4.3 建议终态流程

```text
Finding
→ 去重、反证与问题确认
→ 严重度、交付类型、共同不变量、可失败验收
→ Issue 或组合治理主题
→ 开发者选择 Harness + declare
→ 独立 worktree 修根因
→ 定向回归 + 风险分层 CI + 必要的独立复核
→ strict FIFO 集成；红队首可见且有人工隔离出口
→ main 批次全量 checkpoint
→ 按交付类型做环境验收
→ Issue/Registry 核销
→ 14/30 天复发与 Revisit 收割
```

### 4.4 治理包与 ADR 的边界

“同类 Issue ≥3”可以触发统一设计审查，但不能自动推出新 ADR。Issue 数量只是症状，
新 ADR 必须满足至少一项：

- 存在互斥的长期语义选择；
- 改变跨模块、跨进程、部署或迁移边界；
- 现有 ADR 无法承载该决策；
- 局部修复不能表达或保护所需不变量。

治理包的价值是共享共同不变量、依赖关系和联合验收，不是把所有代码塞进一个巨型
PR。推荐形态是“一次设计裁决 + 有依赖顺序的小 PR 链 + 一组组合回归”。

## 5. 补充问题：临时止血、组合治理与 ADR 边界

### 5.1 总体裁决

已关闭和未关闭 Issue 中**明确存在**临时止血、修复不完整、局部修复与系统能力未
闭环的情况。后续治理必须先区分形态，不能把所有小修都称为治标，也不能把所有
follow-up 都升级为 ADR：

| 类型 | 判据 | 关闭要求 |
|---|---|---|
| 根因修复 `Root` | 在声明范围内恢复真实不变量，并有可失败回归 | 合入、适用验证和核销 |
| 合理防御 `Defense` | timeout、CAS、有限重试或背压本身属于终态设计 | 写清保证边界，禁止扩张承诺 |
| 临时止血 `Transitional` | 已控制损害，但根因或必要边界仍未解决 | owner、`review_by`、残余风险、可测删除条件 |
| 修复不完整 `Incomplete` | 当前基线存在违反原验收的反例 | 重开原单或建立 residual follow-up |
| 风险接受 `Accepted` | 有权者明确接受边界并定义检测与升级触发 | 不得表述为风险已消失 |
| 待验证 `Pending` | 静态证据成立，真实环境或规模证据不足 | 保持开放或显式标注待验 |

第一性原理上，组合治理的触发不是“同目录 Issue 很多”，而是多个 Issue 共享一个
尚未定义或尚未被保护的系统不变量，且继续点修会增加锁、重试、状态、超时和特殊
分支的数量。长期复利要求治理后概念数量、复发率和验证成本下降。

### 5.2 已关闭项中的止血与不完整修复

以下状态于 2026-09-11 10:55（UTC+8）经 GitHub 复核：

| Issue | 当前状态 | 判定 | 终态出口 |
|---|---|---|---|
| [#901](https://github.com/DUElost/stability-test-platform/issues/901) | CLOSED | **修复不完整** | 服务端只允许原子消费成功者签发；补并发 refresh 与 refresh/logout 竞争 |
| [#1123](https://github.com/DUElost/stability-test-platform/issues/1123) | CLOSED | **修复不完整 + 架构边界** | guard 生命周期绑定实际同步工作完成；补真实线程取消交错，再裁决跨进程模型 |
| [#1273](https://github.com/DUElost/stability-test-platform/issues/1273) | CLOSED | **明确止血** | 定位持锁连接/后台线程，收敛生命周期并评估删除 TRUNCATE 重试 |
| [#1101](https://github.com/DUElost/stability-test-platform/issues/1101) | CLOSED | **平台 workaround** | 保持最小 PAT 权限、合入后核销；上游语义变化或替代机制出现时移除 |
| [#1085](https://github.com/DUElost/stability-test-platform/issues/1085) | CLOSED | **合理防御但复利有限** | 动态预算可保留，但不能代替取消、背压与实际副作用完成语义 |
| [#1039](https://github.com/DUElost/stability-test-platform/issues/1039) | CLOSED | **客户端缓解** | Web Lock 只改善多标签体验；服务端安全终态仍由 #901 residual 承担 |

其中 #901、#1123 不是普通 Revisit，而是当前实现直接违反原 Issue 验收，应重开原单
或建立明确关联的 residual follow-up。#1273 可以作为有界止血保留，但没有 owner、
到期日和删除条件的 Revisit 会成为技术债死信。

### 5.3 建议组合治理主题

组合治理主题只定义共同不变量、依赖顺序和联合验收；不要求一个巨型 PR，也不自动
创建新 Epic 或 ADR。

#### G1：取消、失租与物理副作用所有权（最高优先级）

关联：

- 已关：[#1123](https://github.com/DUElost/stability-test-platform/issues/1123)、
  [#1085](https://github.com/DUElost/stability-test-platform/issues/1085)
- 未关：[#799](https://github.com/DUElost/stability-test-platform/issues/799)、
  [#811](https://github.com/DUElost/stability-test-platform/issues/811)、
  [#1222](https://github.com/DUElost/stability-test-platform/issues/1222)

共同不变量：

> coroutine、task 或 HTTP 请求结束，不等于线程、进程、刷机工具或设备副作用已经
> 停止；失去 lease/ownership 的执行者不得继续驱动设备或释放继任者资源。

联合验收应覆盖同步 worker 生命周期、进程树 TERM→KILL、lease lost 后设备隔离、
未确认停止时禁止重新派发、取消接口诚实返回及继任者保护。先直接修 #1123；只有
改变跨进程所有权、隔离和重新派发政策时才需要方向级裁决。

#### G2：PlanRun 终态与聚合权威

关联未关项：

- [#789](https://github.com/DUElost/stability-test-platform/issues/789)
- [#762](https://github.com/DUElost/stability-test-platform/issues/762)

共同不变量：

> 每个 Job 终态最多计入一次，PlanRun 最终必须可收敛；冲突事件不能永久堵塞队头。

应统一事务、计数、409 retain、重放和回收语义，并做并发联合测试。若改变终态计数
不变量，修订执行协议，不另建平行 ADR。

#### G3：会话消费与多标签安全

关联已关项：

- [#901](https://github.com/DUElost/stability-test-platform/issues/901)
- [#1039](https://github.com/DUElost/stability-test-platform/issues/1039)

共同不变量：

> 一个 refresh token 在服务端最多成功消费一次；客户端协调只能优化体验，不能成为
> 安全权威。

优先直接修 #901。只有引入 token family、轮转宽限期或改变复用策略时，才修订
ADR-0024。

#### G4：通知投递完整语义

关联：

- 已关：[#1117](https://github.com/DUElost/stability-test-platform/issues/1117)、
  [#1120](https://github.com/DUElost/stability-test-platform/issues/1120)、
  [#1122](https://github.com/DUElost/stability-test-platform/issues/1122)
- 未关：[#1166](https://github.com/DUElost/stability-test-platform/issues/1166)、
  [#1167](https://github.com/DUElost/stability-test-platform/issues/1167)

共同不变量：

> 渠道接受、明确失败和结果未知必须分开；重试有唯一 owner，并跳过已确认成功通道。

这是点修已进入系统语义层的典型案例。应更新背景后完成现有 ADR-0036 的裁决与实现，
不再新建通知 ADR。

#### G5：控制面多实例能力

关联：

- 已关：[#890](https://github.com/DUElost/stability-test-platform/issues/890)
- 未关：[#720](https://github.com/DUElost/stability-test-platform/issues/720)、
  [#1114](https://github.com/DUElost/stability-test-platform/issues/1114)、
  [#1121](https://github.com/DUElost/stability-test-platform/issues/1121)

#890 的 fail-closed 是正确的局部根因修复，但不等于多实例能力完成。应沿 ADR-0027
实施轨统一 leader、sticky、RunConsole、Socket.IO 和跨实例取消语义。

#### G6：Agent 身份与主机信任

关联：

- 未关：[#906](https://github.com/DUElost/stability-test-platform/issues/906)、
  [#908](https://github.com/DUElost/stability-test-platform/issues/908)
- 已关：[#1263](https://github.com/DUElost/stability-test-platform/issues/1263)

共同不变量：

> 持有共享 secret 不等于可信主机身份；SSH、Agent API 与主机凭据必须形成一致的
> 信任链。

已有 ADR-0035，不应再立竞争 ADR；需要的是实施顺序、迁移、吊销和兼容验证。

#### G7：脚本运行身份、目录与状态生命周期

关联：

- 已关：[#1028](https://github.com/DUElost/stability-test-platform/issues/1028)、
  [#1030](https://github.com/DUElost/stability-test-platform/issues/1030)
- 未关：[#761](https://github.com/DUElost/stability-test-platform/issues/761)、
  [#810](https://github.com/DUElost/stability-test-platform/issues/810)、
  [#814](https://github.com/DUElost/stability-test-platform/issues/814)

共同不变量：

> 一次执行只能消费与该 Job/Run 明确绑定的状态、目录和结果，不能依赖“最新目录”
> 或跨 Job 状态。

应先形成脚本运行时契约，再按不可变规则发布新版本；禁止每个脚本继续独立发明状态
键、目录搜索和判死逻辑。

#### G8：脚本资产生命周期与不可变性

关联未关项：

- [#735](https://github.com/DUElost/stability-test-platform/issues/735)
- [#790](https://github.com/DUElost/stability-test-platform/issues/790)

G7 管运行正确性，G8 管发布资产。优先落实 ADR-0020/0033 的不可变和退役边界；
只有需要改变“已发布版本不可原地修改或删除”的长期原则时，才需要新 ADR。

#### G9：测试数据库安全与测试资源生命周期

关联已关项：

- [#1295](https://github.com/DUElost/stability-test-platform/issues/1295)
- [#1300](https://github.com/DUElost/stability-test-platform/issues/1300)
- [#1273](https://github.com/DUElost/stability-test-platform/issues/1273)

不能因为都在测试目录就合成一个实现包：

- #1295/#1300 保护“测试不得触达生产资源”的安全不变量；
- #1273 处理“测试自身连接/线程必须收敛”的资源生命周期。

可共享验收批次，但应分别定位根因；不得用测试库名规则替代无生产权限的真正隔离。

#### G10：维护窗口跨入口一致性

关联已关项：

- [#960](https://github.com/DUElost/stability-test-platform/issues/960)
- [#1249](https://github.com/DUElost/stability-test-platform/issues/1249)

两个入口均已修复，仍需一次联合验收，证明 API、SAQ、Ansible、claim 和 dispatcher
共同尊重同一 `maintenance_until` 事实。通过后该主题可关闭，不必新建 ADR。

### 5.4 独立流程治理项：FIFO 红队首

[Issue #1246](https://github.com/DUElost/stability-test-platform/issues/1246) 当前仍
OPEN。它不属于业务代码组合包，而是集成系统缺少恢复出口。

最小治理：

1. 红灯持续 10–15 分钟告警；
2. 人工确认后 quarantine、转 draft 或关闭让位；
3. 后续队列才允许前进；
4. 原 PR 修复后按明确规则重新入队；
5. 全程不绕过 required checks、不手工 merge。

若改变严格 FIFO 的方向语义，应修订 ADR-0034；只补告警和可见性不需要新 ADR。

### 5.5 新 ADR 的第一性原理触发条件

不能采用“同类 Issue ≥3 就自动立 ADR”。数量只能触发统一设计审查，新 ADR 还必须
满足至少一项：

- 存在互斥的长期方案选择；
- 改变跨模块、跨进程、部署或迁移协议；
- 现有 ADR 无法清晰承载；
- 局部修复无法表达或保护共同不变量。

当前建议：

| 主题 | ADR 动作 |
|---|---|
| 会话消费 | 先直接修 #901；改变 token family/宽限才修订 ADR-0024 |
| 通知投递 | 完成现有 ADR-0036，不建第二份 |
| 多实例 | 落实 ADR-0027 |
| Agent 身份 | 落实 ADR-0035 |
| 脚本运行与资产 | 优先 ADR-0020/0033 与 design 契约 |
| PlanRun 终态 | 优先修订现有执行协议 |
| 同步副作用/取消所有权 | **新 ADR 候选**；先确认现有执行协议及 ADR-0018/0027 是否足以承载 |
| FIFO 告警 | 直接实现；改变 FIFO 语义才修订 ADR-0034 |

### 5.6 防止代码腐化的执行纪律

1. 每个 Issue 明确标记 `Root / Defense / Transitional / Incomplete / Accepted /
   Pending`，分类不必新增 Registry 字段；
2. `Transitional` 必须有 owner、`review_by`、残余风险与可测删除条件；
3. 同根因出现第二、第三个补丁时暂停散装修复，先明确共同不变量；
4. 组合治理采用“一次设计裁决 + 有依赖的小 PR 链 + 联合回归”，禁止巨型 PR；
5. CLOSED Issue 出现确定反例时必须重开或建立 residual follow-up；
6. 根因到位后删除失去必要性的 fallback、重试、双轨与临时 guard；
7. 评价治理效果看同类复发率、概念数量和维护成本是否下降，不看关单数量。

建议优先顺序：

```text
G1 取消/失租/物理副作用
  > G2 终态收敛
  > G3 会话原子消费
  > G4 通知 ADR-0036
  > G5/G6 多实例与身份
  > G7 脚本运行契约
  > 其余治理主题
```

## 6. 后续多 Harness 综合审查输入

后续汇聚时，建议逐项裁决为“认可 / 反证 / 条件成立”，并保留：

1. 固定 SHA 或 GitHub 查询时间；
2. 代码、测试、Issue、PR、Actions 的证据等级；
3. 是否有可复现反例；
4. 修复范围与不能外推的环境；
5. 共同不变量、最小出口和明确不做什么；
6. 是否需要现有 ADR 修订、新 ADR、design note 或直接修复；
7. 临时止血的 owner、`review_by` 与删除条件。

优先复核：

1. #901 并发 refresh 原子消费；
2. #1123 真实线程取消交错；
3. #1273 泄漏连接/后台线程根因；
4. 总纲 §6 执行链与日志链跨区收口；
5. #1246 红队首告警与人工隔离语义；
6. 高风险 PR 的影响面测试和独立复核；
7. #1035 在 2026-10-15 前的 Registry 经济性裁决。

## 7. 本轮验证记录

实际执行：

- GitHub Issue/PR/branch protection/Actions 只读查询；
- `git rev-parse HEAD origin/main`；
- `git diff HEAD..origin/main --` #901/#1123 关键文件，结果为空；
- `python3 tools/dev/ai_work.py status --risk`；
- 源码、测试、ADR、workflow 与 Agent Note 静态核验。

未执行：

- pytest、Vitest、全量 CI、迁移、Docker build；
- 生产数据库/Redis/NFS/真实设备/主机操作；
- GitHub 写操作和代码修复。

命令成功不等同于上述未执行验证通过。本文的确定缺陷结论来自当前代码可达交错与
已有隔离反例，不外推为已观察到生产事故。
