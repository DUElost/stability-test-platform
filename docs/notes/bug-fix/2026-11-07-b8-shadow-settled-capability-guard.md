# B8 影子终态分区 settled 出口与能力守卫（#3875）

Status: implemented
Class: bug-fix

## Decision

规格为 [#3875](https://github.com/DUElost/stability-test-platform/issues/3875)、
[影子下发守卫](https://github.com/DUElost/stability-test-platform/issues/3875#issuecomment-6621527518)
及 [digest 过渡裁定](https://github.com/DUElost/stability-test-platform/issues/3875#issuecomment-6704534279)。

一次 additive 迁移给 `partition_mode` 增加可空 `shadow_settled_at`，不回填历史分区。
控制面在持有 PlanRun 行锁时确认：run 已终态、最后一轮存在、全部轮次已关闭、
距离最后关闭时间超过一个完整报表等待期、且本次读取没有新迟到输入，才写入时间。
迟到输入仍按 ADR-0062 D5 第 4 条带入补充轮；补充轮的关闭时间重新起算等待期。
读取清单失败不作无迟到输入处理。

候选查询过滤 settled；推进及 SAQ 工作线程再次检查 settled，以覆盖并发快照和残留任务。
已写入的时间不覆写，服务层拒绝为 settled 分区登记新轮。
运行模式字段与现行 scan/upload/merge/extract 的行为保持独立，影子开关为 0 时不执行新路径。

能力判断是**过渡**做法：复用 `compute_desired_artifact_digest(kind=ARTIFACT_KIND_CODE)`
与 `resolve_agent_code_sync_status`，只向摘要与控制面运行时载荷一致的 host 下发。
摘要缺失、不一致或 desired 计算失败时，该期望项为 `MISSING`，原因是
`capability_unverified`，并记录专用日志。原因保存在本轮期望集，可按该原因单独统计 host 数；
重试不累加业务计数。不硬编码摘要或版本名单，现行 scan_now 不经过能力守卫。

已知限制：本方案沿用既有摘要持久化语义（空报保留旧值），没有宣称修复其与实例身份的绑定问题。
每次 Agent 载荷变化都会使尚未同步的主机暂时缺失；这是规划者裁定接受的保守行为。

## Alternatives

- 协议版本门槛：G3 前后版本均为 `2.0.0`，无法区分。
- 仓库提交号比较：现有 revision 是溯源文本，部署布局也不保证有 Git 历史。
- digest 固定名单：随发布失效，且违反本单裁定。
- 每个检查周期继续扫描 settled 分区：保留 CIFS 开销，违背退出候选判据。

## Verification

- 控制面测试覆盖 settled 后无清单 I/O、残留候选/任务不重开、未完成条件留在候选、
  迟到输入进入补充轮且重置等待期、坏清单不提前 settled。
- 能力测试比较真实 desired digest，覆盖缺失、不匹配、匹配、desired 变化，以及现行命令不经过守卫。
- 临时 PostgreSQL 17 回放迁移链，验证存量行初始为空、可写 settled 时间、降级保留存量行。
- 已执行的命令与结果在 PR 中记录；未完成的项目明确标 pending。

## Revisit

终态出口由规划者另立单元：Agent 在心跳显式上报能力并提升协议版本，随后替换本单的 digest
过渡守卫。该单元也需处理能力与当前实例/当前心跳的绑定；本单不新增 Agent 上报字段。

settled 之后的到达观测位置尚待确认：当前 Agent 直接写 CIFS，控制面没有报表到达通知；
在禁止再次读取轮次清单的条件下，控制面无法自行观测后到达报表。该项 pending，不作为已完成验收。
