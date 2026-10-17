# ADR-0061 v1.1 修订提案：解除排队、执行与整 job 的有界保护

- 状态：**已裁决**（2026-10-17，Owner 授权规划者裁决）。五项均接受，第 1、2、3 项按修改 M1–M3 落定，正文已并入
  [ADR-0061 Accepted v1.1](../adr/ADR-0061-device-return-contract.md) 的 D1 第 9 条、D2 第 7–9 条、D3 第 8 条与 D8 第 8 条。
  实施必须以 ADR-0061 v1.1 与 #3723 v1.7 为准；本文件保留为审议稿，标有「已按裁决修改」的原句不得作为实施依据。
- 决策者：Owner（授权规划者裁决）；裁决记录：[#3751](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234602253)；独立架构载体 [#3751](https://github.com/DUElost/stability-test-platform/issues/3751)，跟进实施 [U8 #3752](https://github.com/DUElost/stability-test-platform/issues/3752)。
- 关联：B6 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723)；实施候选见 [v1.7 规划稿](2026-b6-v17-planning-proposal.md)。
- 变更范围：替换 D3.8；明确 D2.5 的控制面核验；批准下述持久化与协议增量及其 Appetite 扩展。
- Accepted 正文已在 PR #3753 的裁决同步提交中更新为 v1.1。任何人不得把本文件当作实施授权；实施授权以 #3723 发布的 v1.7 为准。

## 1. 当前事实与必要性

刷新基线：`origin/main@945fcd4406cefa6299caa688376c9a54c2482868`；U3 #3748 head
`46ba633ee5e56afea836d9cb92bc2f16a68f95c4`；U5 #3747 head
`13a77c7fdd1097c70651a1961b20d0b8a063ded0`。

当前 `backend/core/job_timeout_config.py::disarm_grace_defers_reap` 只保护
`EXECUTING_DISARM`，上限从 abort 起算，且从全部解除步骤取最大墙钟。
Agent 等 permit 时仍为 `WAITING_EXECUTION_SLOT`。因此排队既不受保护，又侵占执行窗口。
既有隔离证据 `/tmp/b6-confirm-u3-46ba633ee/queue-reaper.log` 记录：abort+61s、有效 ACTIVE JOB 租约、
新鲜信号，真实 reaper 写 UNKNOWN；接着 extend-batch 返回 `job_not_running`。本轮只审阅日志和源码，未重跑该探针。

同目录 `adr0061-crosscheck.log` 记录平台 124/125 被推为 failed、真实 complete 接受未验证类别的
confirmed/readback_failed，以及 CLI 跨设备持锁。U5 对应日志记录旧 finish 漏选、等待上线没有轮询进度。
这些是绑定上述 head 的有限反例，不能代签将来的实现。

## 2. 决策候选：替换 D3.8

以下条款均为待 Owner 裁决的完整选定方案；任何人不得在裁决前据此编码。

1. 控制面必须分别核验解除排队存活和解除执行业务进度。控制面不得从 `updated_at`、租约续期成功或普通等待态推断解除正在推进。
2. 控制面必须为每个 job 冻结一个归还尾段预算：全部解除步骤累计排队预算 **Q=7200s**；每个步骤的执行预算 **E_i** 为该 job 冻结的显式正整数 `timeout_seconds`。
   Agent 必须让同一步骤全部尝试、重试退避和重新排队共用首次取得执行许可时确定的执行截止，重试不得重新获得 E_i。
3. 控制面必须把整个 job 的归还尾段绝对上限冻结为 **D_job=T0+min(Q+ΣE_i,86400s)**。
   Σ 包含本次结束路径上全部启用 teardown 步骤（含自然结束的结果收取），不得逐步重建 job 上限。
   T0 必须取该 job 最早有效的服务器端结束触发时刻：run/host abort 的持久化时刻、或首次接受正常/失败/manual_exit 的 teardown 进入报告时刻，取其中更早者。
   这里的 job 上限是从退出测试主体起到整个 job 收尾结束的绝对截止，不改变已配置的测试主体运行时长。
4. 控制面必须在同一 job 锁/CAS 下首次写入 `job_instance.disarm_guard` JSONB。
   该记录必须包含 `protocol_version=1`、T0、D_job、Q、剩余排队预算、冻结步骤 id/脚本/版本/包 SHA/E_i、
   当前阶段、步骤首次排队/执行时间、步骤执行截止、令牌代次、单调事件序号及两类证据的服务器接收时间。
   Agent 必须把服务器返回的锚点、截止和序号写入其现有 LocalDB 活跃 job 记录，重启后先核对服务器记录再恢复。
5. 控制面必须在每次阶段转换时扣减上一个排队区间耗时；当前排队截止必须为
   `min(当前排队起点+剩余Q,D_job,该步骤已存在的执行截止)`，最后一项仅在该步骤重试时适用。
   控制面必须在首次接受取得 permit 的报告时冻结 `D_i=min(服务器接收时刻+E_i,D_job)`。
   Agent 必须先取得该确认再启动解除子进程，并将本地执行窗口限制为返回截止的剩余时间。
   〔已按裁决修改（M2）：取消同步握手。Agent 取得 permit 后立即启动解除；控制面在首次收到执行阶段证据时冻结 D_i，见 ADR-0061 v1.1 D3 第 8 条第 7、8 项。〕
6. Agent 必须以 `WAITING_EXECUTION_SLOT + disarm_phase=queue` 表达解除排队，以
   `EXECUTING_DISARM + disarm_phase=execute` 表达执行；普通等待不得附带解除保护。
   控制面必须从冻结 pipeline、已开始的 init 足迹证据、当前步骤和有效 JOB 租约核验阶段身份。
   该增量不得改变 OperationScheduler 的准入算法或绕过 permit。
7. Agent 必须经现有 extend-batch 上报可选 `disarm_guard` 阶段对象；控制面必须在对应响应项返回权威截止、服务器时间和最后接受序号。
   〔已按裁决修改（M1）：阶段证据必须经协调心跳送达，extend-batch 可以同时携带；extend-batch 每 60 秒一次，不能单独满足 60 秒新鲜度。见 ADR-0061 v1.1 D3 第 8 条第 4、5 项。〕
   阶段对象必须携带步骤 id、阶段、当前 fencing token、协议版本、持久化递增 seq；执行阶段还必须携带来自实际脚本 PROGRESS 的递增进度序号。
   控制面必须使重复 seq 幂等且不刷新新鲜度；更旧 seq、旧代次和逆向阶段消息不得刷新事实或扩大预算。
8. 控制面必须在重复 abort、补到更早事件、重试、恢复和重放时只保留或缩短原截止。
   控制面不得用新的 abort 时间覆盖更早的 T0。Agent 进程重启后必须继续消耗原有预算，无法恢复可信锚点时不得重新创建一份宽限。
   控制面必须用数据库 UTC 时间确定事实，并保存最后接受的服务器时间；检测时间回拨时必须拒绝阶段保护并记录 `clock_regressed`，不得重建锚点。
   Agent 必须用服务器返回的剩余时间约束本地 monotonic 等待，服务器时钟回拨或通信重试不得增加本地剩余预算。
9. Agent 必须至少每 **15s** 报告一次有进展的 permit 等待轮询存活；执行阶段的上线轮询必须至少每 **15s** 产生一次已完成轮询的 PROGRESS。
   〔已按裁决修改（M1）：15 秒是本地证据的刷新间隔；送达间隔为任一 job 处于解除阶段时至少每 30 秒一次协调心跳，不受该间隔配置放大影响。〕
   控制面必须以固定 **60s** 分别校验 queue 存活和 execute 进度的新鲜度（现行协调心跳默认 30s，60s 相当于两个周期）。
   这两个值为本协议固定值，不随部署环境的协调心跳配置静默改变；实施者不得新增 env。
10. 控制面必须同时要求 host 心跳有效、JOB 租约未过期且 token/host boot/设备占用一致，才可以给予阶段保护。
    执行阶段必须同时具有真实执行心跳和真实业务进度；排队阶段只刷新排队存活，不刷新 `last_progress_at`。
    Agent 不得忽略失租、令牌失效、host 代次变化或其它 job 占用。
11. 控制面必须在 abort reaper、通用 running/patrol/trace recycler 的候选过滤和最终写入锁/CAS 内使用同一保护规则。
    最终写入必须复读最新已提交的 abort 主体、guard、阶段、信号与租约，不得只依赖 ORM 缓存或候选快照。
    控制面不得复活 UNKNOWN/终态 job；保护失效后必须恢复原安全回收和 D4/D5 义务交接。
12. 控制面对没有显式正整数墙钟的历史冻结步骤，必须使用固定 **E_i=300s** 并记录 `legacy_missing_wall_clock`；
    控制面不得从本机 env 推断远端预算，0/负数/坏值同样使用该保守历史规则。
    Agent 必须遵守该截止，可能被截断的解除必须记 incomplete；历史有更长内部等待不产生无限豁免。
    新建/编辑的已登记计划仍必须按 v1.0 D3.6 拒绝缺失或不足预算的配置；该兼容规则不得作为新配置默认值。

Q=7200s 是有限吞吐容忍度：permit=1、11 个 job、每个解除 E=660s 时，最后一个最坏排队 6600s 仍有独立执行预算。
更深队列或更长步骤不承诺全部执行，超过预算必须记 incomplete 并交接欠账。
自然结束默认模板的整个尾段上限为 7200+660+420=8280s；manual_exit 只解除时为7860s。
多步骤总预算超过24h时由 D_job 截断，而非靠新增步骤延长占用。

## 3. 决策候选：D2.5 控制面核验与结果优先级

1. 控制面对不满足可信类别/工具/探针包组合，却上报正式 confirmed 或 readback_failed 的结果，必须返回结构化
   **422 `UNVERIFIED_PROBE_RESULT`**；该策略为拒绝，控制面不得自动改成 executed_unverified。
   〔已按裁决修改（M3）：终态回报不得整份拒绝——outbox 会把 409/404 以外的 4xx 转入死信，测试结论会丢失。控制面照常落终态，
   不可信专项改按 step trace 推导；结构化 422 只用于清偿与 init 检查。见 ADR-0061 v1.1 D2 第 7、8 条。〕
   控制面必须在业务变更之前校验整份结果；拒绝不得关闭欠账、释放租约或部分写入终态。
2. 控制面必须由一份纯政策实现普通 complete、迟到补偿、清偿和未来 U4 init 确认的核验。
   可信登记必须来自共享 ONBOARDED 中绑定验证报告的 `(platform,model,permission_model,APK SHA,probe package SHA)` 精确组合；
   设备类别必须取控制面设备事实；脚本包必须取该 job 冻结步骤和 manifest SHA；实际 APK/权限事实必须取当前持有令牌的 Agent 真实采集并持久化的证据。
   缺失、冲突、未知或版本不匹配必须按未验证处理。载荷自报 `verified=true`、可覆盖参数和平台/model 字符串不得建立信任。
3. 控制面必须保留原始结果、原始读取诊断、实际脚本版本/包 SHA、APK SHA、类别、来源/token 代次、校验失败原因和报告引用，供重放与审计。
   拒绝的原始证据必须写入独立诊断审计，不得写入正式解除事实；现有 report/step trace/审计存储承担证据，不新增业务表。
4. Agent 在未知类别上确实执行完解除时可以回报 executed_unverified；动作未执行、失败或未完成时必须回报对应实际结果。
   Agent 和控制面不得仅从 confirmed/readback_failed 字符串猜测动作已完成。
   未验证的 probe 必须保持不通过；它既不证明已执行解除，也不得关闭欠账或放行 init。
5. Agent 和控制面必须先识别平台 wall/stall 终止及已开始后的强制中断，再考虑脚本 JSON/普通失败。
   平台受控退出码124/125和已有信号中断码必须推导为 incomplete，并保留终止来源；普通脚本失败必须保持 failed。
   未接入专项消费 incomplete 必须产生欠账；D8.4 对普通 failed 的 unverified 过渡保持不变。

## 4. 迁移、交错升级与回退

目标模型将排队存活、执行进度、固定截止和所有权拆开；持久事实存在 PostgreSQL，Redis 不承担权威。
本次拟新增的是 `job_instance.disarm_guard` 一个 JSONB 列、上述可选请求/响应字段以及 LocalDB 活跃记录字段；
不新增执行状态枚举、业务表、env、准入算法或阶段 B 开关。这超出现行 §8 的原模型额度，必须由 Owner 明确扩展 Appetite。

1. 实施者必须采用新增 nullable 列的迁移，不得改写已合入 revision；无 guard 的存量 job 必须在首次合法阶段请求时按历史规则冻结。
2. 新控制面遇到旧 Agent 缺字段时必须继续现行 v1.0 路径，不得伪造排队保护；新 Agent 遇到旧控制面无协议响应时必须保留旧路径并报告能力不足。
   执行者不得以这种交错状态开展 B6 激活验收。完整保证只在控制面、Agent、脚本同时满足本协议时成立。
3. Owner 必须继续冻结 B6 发布候选的生产部署，直到 U1–U5/U8 复核、U1–U8 合入、集成与指定真机证据满足批次闸门。
   实施者不得临时加入阶段 B 开关；Owner 要求持续部署时，规划者必须另提开关、迁移与回退裁决。
4. 执行者必须在受控维护窗口内完成新控制面与 Agent 升级，暂停新派发并排空旧运行 job 后核对协议能力，再恢复派发。
   本段只是激活候选顺序，任何人不得据此操作生产；具体窗口、暂停/恢复命令和回退授权必须在激活单逐项确定。
5. 回退执行者必须先停止新归还工作并按 Owner 授权处置欠账，再回退消费者；保留新增列用于审计，不在生产执行降级删列。
   旧消费者不得接管依赖新 guard 的在跑 job。操作步骤及能力不足时的停止条件必须由 U7 同步到激活清单。

## 5. 备选与取舍

- 只忽略 job_not_running/409：会掩盖真实失租，无法修正控制面回收，不采纳。
- 全部 WAITING 无条件豁免：把普通排队变成永久占用，不采纳。
- 持续刷新 updated_at：混淆存活与进展，且可无限延长，不采纳。
- 只有内存计时或使用 Agent 自报绝对时间：无法跨进程恢复并防重放，不采纳。
- 未验证结果自动归一化：需要另证解除动作完成，裸结果不足，选定结构化拒绝。
- 阶段 B 开关：涉及额外上线模型，本轮不加入；当前选择继续部署冻结。

## 6. Owner 裁决表

以下五项为一个协调方案。2026-10-17 已裁决（Owner 授权规划者，[记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234602253)）：五项均接受，第 1–3 项附修改 M1–M3。

1. D3.8 替换条文，Q=7200s、历史 E=300s、尾段24h上限、15s/60s证据参数。
2. `disarm_guard` JSONB、可选阶段协议和 LocalDB 字段；明确扩展 ADR-0061 §8 Appetite。
3. D2.5 选定422拒绝、精确验证组合和四入口共用政策。
4. 平台终止结果优先级与旧 job 实际版本兼容（批次规划稿 §3.0.2/6）。
5. 继续部署冻结、受控窗口内协调升级，暂不添加阶段 B 开关。

另有一项批次级裁决，不修改 ADR 正文：旧登记包 `powercycle_disarm` 1.0.0–1.0.2 的处置（[v1.7 §3.6.3](2026-b6-v17-planning-proposal.md)）。
Owner 已于 2026-10-17 选定方案 A：退役，并按 git 树对象恢复发布（[裁决记录](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234224273)）。上面五项仍 pending。

裁决前状态：**仅规划，不可编码**。裁决后的正式版本必须引用 Owner 原始决定，不能以规划者评论代签。
