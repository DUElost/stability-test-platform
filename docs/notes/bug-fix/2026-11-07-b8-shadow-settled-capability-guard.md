# B8 影子终态分区 settled 出口与能力守卫（#3875）

Status: implemented
Class: bug-fix

## Decision

规格为 [#3875](https://github.com/DUElost/stability-test-platform/issues/3875)、
[影子下发守卫](https://github.com/DUElost/stability-test-platform/issues/3875#issuecomment-6621527518)
及 [digest 过渡裁定](https://github.com/DUElost/stability-test-platform/issues/3875#issuecomment-6704534279)、
[当前心跳绑定](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6704562784)、
[Agent 上送观测](https://github.com/DUElost/stability-test-platform/issues/3875#issuecomment-6704602109)、
[strict 边界与同轮恢复](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6704918122)、
[v1.23 发送连接能力](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6731254627)。

一次 additive 迁移给 `partition_mode` 增加可空 `shadow_settled_at`，给 `host` 增加可空
`agent_artifact_digest_reported_at`，均不回填历史值。
控制面在持有 PlanRun 行锁时确认：run 已终态、最后一轮存在、全部轮次已关闭、
距离最后关闭时间超过一个完整报表等待期、且本次读取没有新迟到输入，才写入时间。
迟到输入仍按 ADR-0062 D5 第 4 条带入补充轮；补充轮的关闭时间重新起算等待期。
周期轮、最后一轮及补充轮的迟到输入读取沿用告警、跳过坏清单的行为，继续推进轮次。
只有准备 settled 时才 strict 复查清单；读取失败只推迟 settled 并记录专用日志，
不抛出到 sweep，不回滚同一次推进中的其他动作。复查新发现迟到输入时也不 settled。

候选查询过滤 settled；推进及 SAQ 工作线程再次检查 settled，以覆盖并发快照和残留任务。
已写入的时间不覆写，服务层拒绝为 settled 分区登记新轮。
运行模式字段与现行 scan/upload/merge/extract 的行为保持独立，影子开关为 0 时不执行新路径。

摘要匹配用于预约前的**过渡预筛**。它复用 `compute_desired_artifact_digest(kind=ARTIFACT_KIND_CODE)`
与 `resolve_agent_code_sync_status`，只给在线、摘要与控制面运行时载荷一致、且摘要上报时间
不早于最近心跳的 host 预约。摘要时间仅在非空 digest 心跳中更新，使用写入
`last_heartbeat` 的同一个 `now` 与同一事务。空 digest 保留旧摘要，预筛根据时间拒绝。
任一条件不满足或 desired 计算失败时，该期望项为 `MISSING`，原因是
`capability_unverified`，并记录专用日志。原因保存在本轮期望集，可按该原因单独统计 host 数；
重试不累加业务计数。不硬编码摘要或版本名单，现行 scan_now 不经过能力守卫。

Agent 在 Socket.IO 握手声明 `capabilities: ["scan_round_v1"]`。最终发送资格取决于
目标本地 SID 的声明。发送使用同一个 SID，并绕过 Redis 适配器。Agent 断开时清除该 SID 的声明。
缺少本地 SID 或声明时，控制面在轮次锁内写入 `MISSING / capability_unverified`，
清除该次预约，并记录 `shadow_scan_capability_unverified_at_send`。
预筛失败不写预约。发送被拒绝后只清除与本次匹配的预约，保留已关闭轮次和并发更新。
host 达标后可以在同一轮恢复下发，清除缺失原因。收到有效报表后覆盖变为 `RECEIVED`。

摘要列的既有读者（例如漂移显示）仍沿用空报保留旧值的语义。
每次 Agent 载荷变化都会使尚未同步的主机暂时缺失；这是规划者裁定接受的保守行为。

Agent 在影子报表文件、映射与清单写入完成之后记录 `shadow_round_report_uploaded` 日志，
正文包含 plan_run、fact_round_id、host、platform、报表文件名与 UTC 上送完成时间；
同时累加 `shadow_round_report_uploaded_total`，复用现有心跳 extra 计数器通道，进程重启清零。
失败的上送不计数，现行报表不计入该计数器。Agent 不读取 settled，上送和心跳沿用现有载荷。
这是**过渡观测**，并非控制面接收回执；分析时比较上送完成时间与 settled 时间，单列后到达报表。

## Alternatives

- 协议版本门槛：G3 前后版本均为 `2.0.0`，无法区分。
- 仓库提交号比较：现有 revision 是溯源文本，部署布局也不保证有 Git 历史。
- digest 固定名单：随发布失效，且违反本单裁定。
- 发送前重读心跳：新连接可以在首次心跳之前接管 host，此时数据库摘要仍属于上一连接。
- 每个检查周期继续扫描 settled 分区：保留 CIFS 开销，违背退出候选判据。

## Verification

- 控制面测试覆盖 settled 后无清单 I/O、残留候选/任务不重开、未完成条件留在候选、
  迟到输入进入补充轮且重置等待期、坏清单不提前 settled。
- 坏的已关闭轮清单不阻止周期轮和最后一轮建立、接收及关闭；终态不能 settled，清单恢复后
  下个检查周期才 settled，严格读取错误不冒泡为 sweep 失败。
- 能力未验证的 host 在同一轮下一周期达标后恢复下发，记录下发时间、清除原因，收到报表后
  覆盖变为 `RECEIVED`，不创建替代轮。
- 能力测试比较真实 desired digest，覆盖缺失、不匹配、匹配、desired 变化、旧 Agent 空报后留存
  摘要被拒绝、在线与同次心跳约束，以及现行命令不经过守卫。
- v1.23 测试覆盖读到摘要快照后接管连接、等待另一台 ACK 时接管连接、旧 Agent 先连后发心跳、
  有能力的 Agent 重连后同轮恢复，以及无本地 SID 时影子拒绝、现行仍可通过 room 发送。
- Agent 测试验证连接握手声明 `scan_round_v1`。
- 同一反例中，settled 后 Agent 真实上送并记日志、计数，打桩断言控制面不读取任何轮次清单，
  不重开分区；Agent 测试另覆盖失败不计数、现行报表不计数及心跳接线。
- 临时 PostgreSQL 17 回放迁移链，验证存量行初始为空、可写 settled 时间、降级保留存量行。
- U9 历史迁移往返测试按反射得到的旧 `host` schema 建立夹具，避免在降级后的表中插入当前
  ORM 的新增列；保留原有租约到期边界与幂等验证。本单不为此改动历史迁移或增加迁移。
- 已执行的命令与结果在 PR 中记录；未完成的项目明确标 pending。

## Revisit

若需要控制面实时感知报表到达，由规划者另立接收回执单元，不恢复对 settled 分区的轮询。
Agent 改动与 G4、#3889 按 v1.23 一起滚动。机队全部声明能力后，按批次人工门禁决定影子激活。
