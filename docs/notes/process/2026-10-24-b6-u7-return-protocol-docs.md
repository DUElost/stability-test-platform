# B6-U7：执行协议与运维文档同步（第一 PR）

Status: implemented
Class: process

## Decision

按 [#3732](https://github.com/DUElost/stability-test-platform/issues/3732) 与
[#3723 当前正文 v1.7.19](https://github.com/DUElost/stability-test-platform/issues/3723) §3.7 第 1–5、7 条，
仅同步文档，不重新设计 B6。基线为 `origin/main@78d89ea96d6cbf86d9f100d9e2d78c4c9ef0e2c7`，包含 U4。
依据为 [ADR-0061 Accepted v1.1](../../adr/ADR-0061-device-return-contract.md)。

- 执行协议新增 §9，覆盖终态六值、实际版本角色、有界保护、平台终止优先、统一核验、欠账生命周期、U4 与交错兼容。
- 紧急释放手册分激活前后，写明 409 LEASE_IS_RETURN_DEBT 与 200/MAINTENANCE 的区别；开关机手册只增激活后排障，原过渡正文与出口不变。
- 数据模型补四项 B6 表/列与 MAINTENANCE 用途，四份历史 ADR 只加修订指针，运维索引同步。
- 所有新增 B6 行为均标明自 B6 激活起生效。合入不代表部署或阶段 B 初始化；激活清单由 U7 第二 PR 在 Owner 批准步骤后另行交付。

范围为上述九份文档及本 Note。没有修改代码、冻结包、测试、生产配置或共享启动契约。

### 2026-10-24 规划者核对返修

按 [规划者核对](https://github.com/DUElost/stability-test-platform/pull/3788#issuecomment-6383779648) 的四项要求继续原分支。
起始 PR HEAD 与远端分支均为 `e859bdeeaa69f84ca3197f9892c2eebef41a4c30`，origin/main 仍为上述 U4 合入 SHA；
恢复原 Execution `b6-u7-3732-docs`，保留原十文件 scope。

1. 开关机手册改为 grep -E 读取安装目录的两个日志文件，异常按设备 id 匹配；agent_post 消息只作旁证。
   清偿成功不写日志，成功判据改读控制面专项 source=discharge 与条目 closed_reason=discharged，并核对处理时刻。
2. init 检查查法改用 runs steps 接口的最新状态或 step_trace 只读查询；移除手册中的 trace 条数要求。
3. 紧急释放激活前命令恢复原回环地址，两个激活后写入口沿用回环地址；删去 SOP 中的 STP_API_BASE 定义。
   本次派单已澄清「不得出现主机地址」指内网主机地址，回环地址属于既有手册写法。
4. 执行协议唯一性补齐 source_kind 与 COALESCE，与数据模型和部分唯一索引逐项一致。
5. 同步处理非阻塞项：ADR-0043 与 ADR-0057 的修订说明移动到完整元数据列表之后，文字未改。

本轮只改两份 SOP、执行协议、上述两份 ADR 与本 Note，不改数据模型、代码、测试或冻结包。

## Alternatives

- 合入后立即按 B6 描述生产：不采用；#3723 §3.7 v1.7.19 明确区分合入与激活。
- 提前删除一台一 PlanRun、人工核验与交付拦截：不采用；过渡出口是阶段 B 生效。
- 把 init 检查混入终态 disarm_results：不采用；U4 v1.7.17 已裁定为独立入口与 trace，终态只含本 job 足迹。
- 重写四份历史 ADR 的决策：不采用；本轮只加 ADR-0061 的后续修订说明。
- 本轮补激活/回退命令或调整代码：不采用；前者等待 Owner 批准，方案与代码冲突时按「退回：U7」处理。

返修放弃首轮的 journald/rg 与事件流查法：打包 unit 将输出追加到文件，现有日志格式不输出 api_client 的 context，
清偿成功无日志；PlanRun 事件流只收失败 trace，无法作为 init 通过的读取位置。没有为补查法新增日志或 API。

## Verification

以下首轮记录绑定 `e859bdeea`；其中日志查法、事件流查法与命令变量的结论由本轮返修记录更正。

- `git fetch origin main`：exit 0；基线与 U4 合入 SHA 相同。
- `./scripts/project_python.sh tools/dev/ai_work.py status --risk`：exit 0，开工时在窗 0；GitHub 开放 PR 查询为空。
- Registry：`b6-u7-3732-docs`，issue 3732，test-impact=none；scope 为九份目标文档与本 Note（逐个 --scope 登记）。
- 11 段 SQL：干净环境经项目 Python 入口内联验证，testcontainers `postgres:16` 新建隔离库并用 main 的 Base.metadata 创建表后执行全部 SELECT；exit 0，紧急释放 6 段、开关机排障 5 段全部成功，容器已停止。未加载生产 env，未连接生产库。
- 原过渡正文与出口：从 origin/main 读取原文件，排除新增流程时点提示后逐字节比对；原有 5039 bytes 原样保留。
- `env -i PATH="$PATH" HOME="$HOME" ./scripts/project_python.sh scripts/run_gates.py check:quick`：exit 0，16 gates 通过，含治理面 S1–S15/S5x；未配置 DATABASE_URL，schema-at-head 明确跳过，不代表迁移已验收。
- B6 代码入口对账：main 的 25 个文件、89 个函数/类符号均存在；curl 路径、响应字段、返回码与 journalctl unit 已按 main 路由/schema/service 核对。一次性提取脚本初版误把历史引用和迁移 id 当作函数，收窄到新增 B6 代码入口后 exit 0。
- 文档链接核对：10 个文件、70 个相对链接及其锚点通过，exit 0；实际 diff 只有指定十份 Markdown，新增内容无主机地址字面量或完整设备序列号。
- `git diff --check`：exit 0（初稿头部行尾空白已修正）。本轮没有运行完整 Agent/backend/root 测试套件；required CI：pending，以最终 PR head 的结果为准。

### 2026-10-24 返修验证

- main 代码对账：打包 unit 的 StandardOutput/StandardError；`backend/agent/main.py` 的日志格式；
  `ReturnDebtConsumer._drain` 的带设备 id 异常；`api_client._post_with_retry` 的 agent_post 日志；
  `backend/api/routes/runs.py::list_run_steps`；`device_return_debt` 的部分唯一索引。
- `env -i PATH="$PATH" HOME="$HOME" TESTING=1 ./scripts/project_python.sh -`（内联 SQL 验证）：exit 0；
  两份 SOP 的全部 13 段 SELECT（紧急释放 6、开关机 7）在新建 testcontainers PostgreSQL 16 的只读事务中执行成功，
  `SHOW transaction_read_only` 为 on，容器已停止；未连接生产库。
- `env -i PATH="$PATH" HOME="$HOME" ./scripts/project_python.sh -`（内联静态核对）：exit 0；
  grep -E 用合成日志验证设备 1 不误匹配设备 10，agent_post 三种消息只作旁证；`bash -n` 核对 3 段激活后命令语法。
  激活前释放命令与 main 逐字相同，开关机原过渡正文 5039 bytes 保留，ADR 修订说明文字未改、已移动到元数据之后。
- 文档链接与代码入口内联核对：exit 0；10 个文件、70 个相对链接及锚点通过，`list_run_steps` 在 main 存在；
  本轮 diff 正好为上述六份 Markdown。`git diff --check`：exit 0。
- `env -i PATH="$PATH" HOME="$HOME" ./scripts/project_python.sh scripts/run_gates.py check:quick`：exit 0，16 gates 通过；
  schema-at-head 因未配置 DATABASE_URL 明确跳过，不代表数据库迁移验收。
- 新 HEAD 的 required CI 与信息性检查：pending；前一 HEAD 的成功不转移到本轮。

### 本轮改动句子的 ADR-0059 自查

以下条款编号均指 ADR-0059；后文 93 项是首轮历史清单，本节替代其中被返修的对应项。

1. 「在目标 Agent host 用设备 id 查询清偿异常日志」：D2 第 1、2 条；D3 第 5 条；D4 第 1、4、6 条。
   grep -E 的设备 id 后加数字边界，避免把设备 1 与设备 10 的异常混同。
2. 「agent_post_(retry|failed|conflict) 的日志行不含目标设备，只作请求异常的旁证」：D2 第 2、4 条。
   「清偿成功不写日志；本次是否成功以控制面该专项的 source=discharge 与所涉条目的 state=closed、closed_reason=discharged 为准」：
   D2 第 2、4 条；D4 第 6 条。两句为代码行为与证据来源的描述，附只读 SQL 查法。
3. 「对下一次已获授权的 job，读取 GET /api/v1/runs/{job_id}/steps 响应中 name=return_check.power-cycle 的最新状态」：
   D2 第 2、4 条；D3 第 5 条；D4 第 1、2、4、6 条。通过/失败/尚未确认有明确预期；替代查询只读且只取最新状态。
4. 「执行者必须在控制面宿主上运行本文的 curl 命令」：D2 第 1、2 条；D3 第 1 条。
   「在控制面宿主上以管理员身份请求释放，reason 填本次非空处置原因」与
   「在控制面宿主上以管理员身份调用人工放行接口」：分别按 D3 第 5 条、D4 第 1、4、5、6 条自查；
   操作前的授权警告与操作后的预期保留，激活前命令逐字恢复。
5. 「部分唯一索引 uq_device_return_debt_open_source 在 state='open' 时约束
   (device_id, family, source_kind, COALESCE(source_job_id, 0)) 唯一」：D2 第 4 条，描述既有机器约束，不新增规则。
6. 两份 ADR 只移动已有修订说明：原文与原决策不改，按 D1 的增量范围自查，没有新增规则句。

### 写作约定 §5 自查

本轮对全部改动句子使用 [writing-conventions.md](../../development/writing-conventions.md) §2–§4，
包括作为执行协议的 07 与事实性模型/ADR 指针；两份 operations SOP 属其 §1 明确范围。
下面按句子或继承强度的清单项列出规则，条款编号均指
[ADR-0059](../../adr/ADR-0059-contract-and-sop-writing-conventions.md)。这是本 PR 的作者自查记录，没有新增 CI 或散文 lint。

- [x] D2 第 1、2 条：使用 PlanRun、job、控制面、Agent host 等推荐名和限定语；区分专项状态 source 与欠账条目 source_kind。
- [x] D3 第 1、2、3、5 条：显式规则使用必须/不得/可以与执行者；预算清单由导语统一声明强度；SOP 以流程导语承载强度。
- [x] D4 第 1–6 条：单步动作、条件前置、独立停止条件、操作前警告、变更预期与可核对的查询/页面/命令。
- [x] D2 第 3、5 条：没有新增术语对照条目或中心术语表。四份 ADR 仅追加描述性修订指针，原决策正文未改。

#### 07-execution-protocol.md

1. 执行协议契约（Execution Protocol）：#3723 §5 的激活证据发布前，运维执行者必须沿用本文标明的激活前分支。（D2 第 1、2 条；D3 第 1 条）
2. 1. Job 状态机（权威）：Agent 对晚到完成必须先续约/恢复为 RUNNING（token 匹配）再 complete；或 grace 到期 → FAILED。（D2 第 1、2 条；D3 第 1 条）
3. 1. Job 状态机（权威）：B6 激活前随后释放租约；自 B6 激活起，控制面必须经 §9.5 的统一入口决定释放或转换为欠账租约（ADR-0061 D3 第 5 条）。（D2 第 1、2 条；D3 第 1 条）
4. 3. Abort（保租约 ACK）：自 B6 激活起：Agent 必须保留解除的执行机会（§9.2），再经 `/complete` 回报 ABORTED 与解除结果。控制面必须在同一终态事务内经统一入口处理租约（§9.5），随后聚合（ADR-0061 D3 第 1–5 条）。（D2 第 1、2 条；D3 第 1 条）
5. 7. Agent 运行时要点：- `PipelineEngine.cancel`：B6 激活前杀进程组（含 abort / fencing）；自 B6 激活起，普通取消保留正在执行的 teardown， 确认失租时 Agent 不得继续解除（§9.2；ADR-0061 D3 第 3、7 条）。（D2 第 1、2 条；D3 第 1 条）
6. 9.1 终态载荷与足迹（自 B6 激活起生效）：Agent 必须在 `POST /api/v1/agent/jobs/{job_id}/complete` 的可选字段 `disarm_results` 中， 按专项回报本 job 在 init 中开始执行建立足迹步骤的专项。（D2 第 1、2 条；D3 第 1 条）
7. 9.1 终态载荷与足迹（自 B6 激活起生效）：Agent 不得把 `return_check.<family>` 的 init 检查条目放入终态 `disarm_results`。（D2 第 1、2 条；D3 第 1 条）
8. 9.1 终态载荷与足迹（自 B6 激活起生效）：控制面必须保持 job 测试结论与解除结果正交。（D2 第 1、2 条；D3 第 1 条）
9. 9.1 终态载荷与足迹（自 B6 激活起生效）：控制面不得因解除失败改写 `status_reason` 或翻转 job 终态。（D2 第 1、2 条；D3 第 1 条）
10. 9.2 解除执行与有界保护（自 B6 激活起生效）：Agent 必须在建立足迹后的每条结束路径上尝试解除，包括自然到点、abort、manual_exit、init/patrol 失败、 barrier 超时与优雅停机。（D2 第 1、2 条；D3 第 1 条）
11. 9.2 解除执行与有界保护（自 B6 激活起生效）：Agent 处理 manual_exit 时可以跳过结果收取。（D2 第 1、2 条；D3 第 1 条）
12. 9.2 解除执行与有界保护（自 B6 激活起生效）：Agent 不得因此跳过解除。（D2 第 1、2 条；D3 第 1 条）
13. 9.2 解除执行与有界保护（自 B6 激活起生效）：Agent 不得因普通取消、重复 abort、`ABORT_LOCAL` 或设备重连而丢弃排队的解除或终止执行中的解除。（D2 第 1、2 条；D3 第 1 条）
14. 9.2 解除执行与有界保护（自 B6 激活起生效）：Agent 必须按 job 冻结的实际步骤版本选择解除义务：旧 `powercycle_finish@1.0.x` 仍承担解除， `powercycle_finish@1.1.0` 只收取，`powercycle_disarm` 承担解除。（D2 第 1、2 条；D3 第 1 条）
15. 9.2 解除执行与有界保护（自 B6 激活起生效）：版本不明时 Agent 必须保守保留完整 teardown， 并记录 `unknown_legacy_version`。（D2 第 1、2 条；D3 第 1 条）
16. 9.2 解除执行与有界保护（自 B6 激活起生效）：同 job 混用旧 finish 与新 disarm 时，Agent 必须按 pipeline 顺序执行全部启用的解除步骤。（D2 第 1、2 条；D3 第 1 条）
17. 9.2 解除执行与有界保护（自 B6 激活起生效）：控制面与 Agent 必须遵守以下预算与证据约束：。（D2 第 1、2 条；D3 第 1 条）
18. 9.2 解除执行与有界保护（自 B6 激活起生效）：全部步骤共用排队预算 `Q=7200s`；`E_i` 为冻结的正整数 `timeout_seconds`，历史缺失或非法值取 `300s`，记录 `legacy_missing_wall_clock`。（D2 第 2、4 条；D3 第 3 条）
19. 9.2 解除执行与有界保护（自 B6 激活起生效）：`T0` 取最早有效服务器退出触发；`D_job=T0+min(Q+ΣE_i,86400s)`，求和覆盖本次路径全部启用 teardown。（D2 第 2、4 条；D3 第 3 条）
20. 9.2 解除执行与有界保护（自 B6 激活起生效）：控制面首次收到步骤执行证据时冻结 `D_i=min(收到时刻+E_i,D_job)`；重试不重置 `E_i`，各次排队累计扣减 Q。（D2 第 2、4 条；D3 第 3 条）
21. 9.2 解除执行与有界保护（自 B6 激活起生效）：Agent 排队存活证据与脚本实际 PROGRESS 分开取证；本地证据至少每 `15s` 推进，解除期间协调心跳间隔最多 `30s`。 控制面以接收时刻判定两类证据的 `60s` 新鲜度；执行另要求执行心跳有效。extend-batch 不是唯一证据通道。（D2 第 2、4 条；D3 第 3 条）
22. 9.2 解除执行与有界保护（自 B6 激活起生效）：Agent pump 对带 seq 的 PROGRESS 只计严格增加，对无 seq 的每条 PROGRESS 都计入；重复 seq 不刷新进度。 控制面不得用 `updated_at`、续租成功或 Agent 自报时间代替存活或进度。（D2 第 2、4 条；D3 第 3 条）
23. 9.2 解除执行与有界保护（自 B6 激活起生效）：Agent 在每个解除步骤启动前必须单次核对 `extend_lock`（超时 `5s`、不重试）；409 表示失租，记 `not_executed/lease_lost` 并停止解除。 网络失败不阻止启动。Agent 取得 permit 并完成该核对后必须立即启动。Agent 不得等待阶段证据确认；本地执行窗口取 E_i 与最近 D_job 剩余时间的较小者。（D2 第 2、4 条；D3 第 3 条）
24. 9.2 解除执行与有界保护（自 B6 激活起生效）：控制面只在 host 心跳、租约有效期、token、boot 与设备占用均有效且证据新鲜、未到截止时保护 RUNNING；普通等待没有豁免。 abort reaper、recycler 的 running/patrol/step-trace 候选与最终行锁/CAS 必须复读最新事实并使用同一政策。（D2 第 2、4 条；D3 第 3 条）
25. 9.2 解除执行与有界保护（自 B6 激活起生效）：控制面在 `job_instance.disarm_guard` 持久化锚点，Agent 在 LocalDB 活跃记录保存截止与序号。 控制面与 Agent 不得因重启、重复/乱序回报或重复 abort 延长截止。 控制面遇到服务器时钟回拨必须停用保护并记录 `clock_regressed`。Agent 无法恢复可信锚点时不得重建宽限。（D2 第 2、4 条；D3 第 3 条）
26. 9.2 解除执行与有界保护（自 B6 激活起生效）：任一适用截止到达时，Agent 必须停止尾段并记录义务。Agent 不得靠换步骤延长 job。 保护因预算、证据或所有权失效后，控制面必须按无 guard 的现行规则判定回收与基础活性投影。控制面不得继续借 guard 到期时间推迟。 控制面不得复活 UNKNOWN 或终态 job 来补偿解除缺口。（D2 第 2、4 条；D3 第 3 条）
27. 9.3 平台终止与兼容推导（自 B6 激活起生效）：Agent 与控制面必须先识别平台墙钟/停滞终止（124/125）及信号中断（143/-15/137/-9）， 将已开始的解除记为 `incomplete`，优先于成功 JSON、回读与普通失败。（D2 第 1、2 条；D3 第 1 条）
28. 9.3 平台终止与兼容推导（自 B6 激活起生效）：控制面对无 `disarm_results` 的旧回报必须按实际步骤角色与 trace 的 `original_ts` 推导： STARTED 无终态取 `incomplete`，真实 COMPLETED 最多取 `executed_unverified`，没有解除开始证据取 `not_executed`。（D2 第 1、2 条；D3 第 1 条）
29. 9.3 平台终止与兼容推导（自 B6 激活起生效）：控制面不得从 `job.status` 猜测设备已归还；未接入专项的 `incomplete` 同样转欠账，普通 `failed` 才沿用 D8 第 4 条的未验证过渡。（D2 第 1、2 条；D3 第 1 条）
30. 9.4 四入口的统一核验（自 B6 激活起生效）：Agent 汇总与控制面必须使用共享 `probe_result_policy`，按平台、型号、权限模型、APK SHA、脚本版本与包 SHA 精确匹配附真机报告的登记组合，并核对 `boot_completed=1`。（D2 第 1、2 条；D3 第 1 条）
31. 9.4 四入口的统一核验（自 B6 激活起生效）：控制面必须先按各入口的租约/token 规则核对所有权，再对不可信的 `confirmed/readback_failed` 分别处理：。（D2 第 1、2 条；D3 第 1 条）
32. 9.4 四入口的统一核验（自 B6 激活起生效）：普通 `/complete` 照常落终态与测试结论，该专项改按 trace 推导，不因核验失败整份返回 422。（D2 第 2、4 条；D3 第 3 条）
33. 9.4 四入口的统一核验（自 B6 激活起生效）：迟到确认不关闭条目，不因此新增 4xx。（D2 第 2、4 条；D3 第 3 条）
34. 9.4 四入口的统一核验（自 B6 激活起生效）：清偿与 init 检查对该专项返回 422 `UNVERIFIED_PROBE_RESULT`，包含 family、reason、expected/observed 与报告引用； 被拒专项不改业务事实，同一回报的合法专项照常处理。非特权值 `executed_unverified/failed/incomplete/not_executed` 照常接收。（D2 第 2、4 条；D3 第 3 条）
35. 9.4 四入口的统一核验（自 B6 激活起生效）：控制面必须对每个被替换或被拒的专项写 `unverified_probe_result` 诊断审计，保留入口、原值、替换值、原因与报告引用； 计数按审计聚合，同一回报的只读重放不重复写入。（D2 第 1、2 条；D3 第 1 条）
36. 9.5 欠账生命周期（自 B6 激活起生效）：控制面必须使终态处理、初始化、清偿、放行与退役在设备行锁下串行；涉及 job 的锁序为 Job → Device → Lease。（D2 第 1、2 条；D3 第 1 条）
37. 9.5 欠账生命周期（自 B6 激活起生效）：未退役设备有未关闭条目时，控制面必须保留恰一条 ACTIVE 租约；全部条目共用一条 `MAINTENANCE` 欠账租约 （holder=`platform`、reason=`归还欠账`），无自动到期回收。（D2 第 1、2 条；D3 第 1 条）
38. 9.5 欠账生命周期（自 B6 激活起生效）：控制面必须在终态同一事务内经 `handle_job_lease_release` 或 `handle_job_lease_release_sync` 原地转换 JOB 租约并推进代次， 使旧 token 失效；没有欠账时才释放。（D2 第 1、2 条；D3 第 1 条）
39. 9.5 欠账生命周期（自 B6 激活起生效）：reconciler 与紧急释放不得释放欠账租约。（D2 第 1、2 条；D3 第 1 条）
40. 9.5 欠账生命周期（自 B6 激活起生效）：- **阻断派发**：控制面必须以 `device_return_debt` 原因拒绝欠账设备进入派发或链式继承；claim 的 ACTIVE 租约互斥继续有效。（D2 第 1、2 条；D3 第 1 条）
41. 9.5 欠账生命周期（自 B6 激活起生效）：- **人工放行**：Owner 或管理员可以经 `POST /api/v1/devices/{device_id}/return-override` 指定专项（省略或空列表为全部）。（D2 第 1、2 条；D3 第 1 条）
42. 9.5 欠账生命周期（自 B6 激活起生效）：执行者必须填写 `reason_category` 与 `evidence_ref`。（D2 第 1、2 条；D3 第 1 条）
43. 9.5 欠账生命周期（自 B6 激活起生效）：- **带欠账退役与取消退役**：只有欠账租约且无活跃 job/其它 ACTIVE 租约时，控制面可以执行退役，在同一事务内释放欠账租约，条目仍 open。（D2 第 1、2 条；D3 第 1 条）
44. 9.6 U4 init 残留检查（自 B6 激活起生效）：Agent 必须在普通 job 任何建立足迹的计划步骤前， 用登记脚本及最低版本（`powercycle_disarm@1.0.8`）运行 probe。（D2 第 1、2 条；D3 第 1 条）
45. 9.6 U4 init 残留检查（自 B6 激活起生效）：confirmed 收到该专项 422 时，Agent 必须改报 executed_unverified 后继续；有界重试仍未送达则以 `init_check_unreported` 失败。（D2 第 1、2 条；D3 第 1 条）
46. 9.6 U4 init 残留检查（自 B6 激活起生效）：init 检查未通过时，Agent 不得启动计划 teardown，终态 `teardown_status=SKIPPED`； patrol 断点恢复时不重跑 init 检查，仍执行 teardown。（D2 第 1、2 条；D3 第 1 条）
47. 9.7 U4 自动清偿（自 B6 激活起生效）：控制面先核对欠账 token（不符 409），再执行 §9.4 核验与 §9.5 清偿；Agent 收到 409 后必须丢弃该 token 的缓存且不再为其重试。（D2 第 1、2 条；D3 第 1 条）
48. 9.8 版本交错兼容（自 B6 激活起生效）：执行者不得在控制面与 Agent host 版本交错期间开展 B6 归还验收。（D2 第 1、2 条；D3 第 1 条）

#### 05-data-model.md

1. device_return_debt（自 B6 激活起生效）：控制面必须在设备行锁下串行处理条目与占用；涉及 job 时锁序为 Job → Device → Lease。（D2 第 1、2 条；D3 第 1 条）

#### device-lease-emergency-release.md

1. 设备租约紧急释放：执行者必须按 #3723 §5 的生效证据选择流程：激活前执行 §1，自 B6 激活起执行 §2。（D2 第 1、2 条；D3 第 1 条）
2. 设备租约紧急释放：执行者必须先确认当前 Requirement 明确授权本次 写入。（D2 第 1、2 条；D3 第 1 条；D4 第 3 条）
3. 设备租约紧急释放：执行者不得手工 `UPDATE` `device_leases`，也不得修改 `device` 表或 Agent 文件。（D2 第 1、2 条；D3 第 1 条；D4 第 3 条）
4. 1. B6 激活前：本节仅适用于 B6 激活前；自 B6 激活起，执行者不得用本节的直接释放预期处置欠账。（D2 第 1、2 条；D3 第 1 条）
5. 前置检查：6. 如果 `lease_type` 不是 `JOB`，停止本流程并报告。激活前流程不处理 `SCRIPT` 或 `MAINTENANCE` 租约； 激活后的欠账租约处置见 §2。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
6. 2. B6 激活后（自 B6 激活起生效）：警告：执行者不得手工改写租约或归还状态，也不得把释放全部欠账租约当作日常处置。（D2 第 1、2 条；D3 第 1 条；D4 第 3 条）
7. 2. B6 激活后（自 B6 激活起生效）：1. 核对当前 Requirement 的生产写授权。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
8. 2. B6 激活后（自 B6 激活起生效）：2. 如果没有该授权，停止本流程。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
9. 2. B6 激活后（自 B6 激活起生效）：3. 按 `production-diagnostics.md` 的只读连接方式查询目标设备的 ACTIVE 租约：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
10. 2. B6 激活后（自 B6 激活起生效）：4. 如果没有 ACTIVE 租约，停止本流程，按 §1 第 5 步排查非租约占用。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
11. 2. B6 激活后（自 B6 激活起生效）：5. 如果租约为 `MAINTENANCE` 且 holder/reason 匹配上述欠账载体，停止紧急释放流程，转 `开关机手册 §5` 读取归还状态与未关闭条目。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
12. 2. B6 激活后（自 B6 激活起生效）：6. 如果租约类型不是 JOB 且不符合欠账载体，停止本流程并报告状态异常。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
13. 2. B6 激活后（自 B6 激活起生效）：7. 如果 JOB 未终态、结束时间为空或结束不足 5 分钟，停止本流程；在途 job 按正常协议收尾。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
14. 2. B6 激活后（自 B6 激活起生效）：8. 用 §1 第 10 步的 PromQL 查询回收器可用性。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
15. 2. B6 激活后（自 B6 激活起生效）：9. 如果结果不为 0（包括无结果），停止本流程并按 §1 第 11 步报告。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
16. 2. B6 激活后（自 B6 激活起生效）：执行者不得把 HTTP 200 当作设备已归还； `data.status=MAINTENANCE` 表示设备仍由 ACTIVE 欠账租约占用。（D2 第 1、2 条；D3 第 1 条；D4 第 3 条）
17. 2. B6 激活后（自 B6 激活起生效）：10. 以管理员身份请求释放，`reason` 填本次非空处置原因：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条；D4 第 5 条）
18. 2. B6 激活后（自 B6 激活起生效）：11. 用第 3 步的 SQL 复读 ACTIVE 租约。 预期：释放后无原 ACTIVE 租约，或同一租约 id 已变为 MAINTENANCE。后一种情况下，执行者不得按 §1 第 14 步期待设备变 ONLINE。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条；D4 第 5 条）
19. 2. B6 激活后（自 B6 激活起生效）：12. 如果出现欠账租约或 409 `LEASE_IS_RETURN_DEBT`，停止重复释放，转开关机手册 §5 的清偿或人工放行流程。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
20. 2. B6 激活后（自 B6 激活起生效）：13. 如果返回 RELEASED，执行 §1 第 13–16 步的租约与心跳回查。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
21. 2. B6 激活后（自 B6 激活起生效）：只有 Owner 或管理员可以执行放行，API 使用管理员鉴权；操作与审计回查见开关机手册 §5.3。（D2 第 1、2 条；D3 第 1 条）

#### powercycle-chain-disarm-runbook.md

1. 开关机专项的中止与"链路解除"运维手册：自 B6 激活起，执行者必须按 §5 排障， 以登记组合的设备侧回读与欠账事实判断归还；原过渡内容及其「阶段 B 生效」出口保留到另一个文档收尾 PR。（D2 第 1、2 条；D3 第 1 条；D4 第 3 条）
2. 5.1 读取归还事实（自 B6 激活起生效）：警告：执行者不得把 job 已终态、设备 ONLINE、单个停止标志或接口 200 当作已归还。（D2 第 1、2 条；D3 第 1 条；D4 第 3 条）
3. 5.1 读取归还事实（自 B6 激活起生效）：1. 按 `production-diagnostics.md` 的只读连接方式，查询目标 job：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
4. 5.1 读取归还事实（自 B6 激活起生效）：2. 查询设备的归还状态与欠账占用：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
5. 5.1 读取归还事实（自 B6 激活起生效）：3. 查询未关闭条目及来源：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
6. 5.1 读取归还事实（自 B6 激活起生效）：4. 如果设备未退役且有未关闭条目却没有 ACTIVE 租约，停止处置并报告占用不变量异常。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
7. 5.1 读取归还事实（自 B6 激活起生效）：5. 如果没有未关闭条目但专项仍为 debt，停止处置并报告状态不一致。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
8. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：1. 用 §5.1 第 2、3 步区分退役、既有 JOB 占用、欠账 MAINTENANCE 租约与未接入专项。 退役设备保留 open 条目但没有欠账租约；JOB 占用期间不清偿；本批只有 power-cycle 接入。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
9. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：2. 如果已有获批留存的 `POST /api/v1/heartbeat` 响应，读取 `return_debts` 中该 device_id 的专项与令牌字段，记录时隐藏令牌值。未留存响应时，将「欠账下发是否送达」记为未验证，并交 Agent host 维护者补证。该字段未下发时 Agent 不动作；下发入口为 `backend/api/routes/heartbeat.py::_process_heartbeat_with_db`，条目由 `backend/services/device_return.py::open_debts_for_host_sync` 组装。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
10. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：3. 在目标 Agent host 查询清偿诊断日志：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
11. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：4. 对照最近一次清偿回报的正式值与 HTTP 返回码判断下一次触发：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
12. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：5. 如果设备长期不可达或专项尚未接入，转现场恢复设备后清偿，或转 §5.3 人工放行。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
13. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：6. 查询组合核验的诊断审计：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
14. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：7. 如果原始值为 absent/not_installed，或平台、型号、权限模型、APK SHA、探针包身份、boot_completed 不满足可信组合， 保留欠账并转 §5.3 或交规划者补验证；解除成功得到 executed_unverified 不关闭条目。 probe 的 absent 不是「无足迹」证明；已安装应用的设备先 disarm 后可信 clear 才能 confirmed，未安装应用的设备没有 APK SHA，最多未验证。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
15. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：执行者不得伪造 verified 标记、修改登记组合或反复创建清理 PlanRun 绕过欠账。（D2 第 1、2 条；D3 第 1 条）
16. 5.2 清偿没有发生或组合不可信（自 B6 激活起生效）：执行者不得用紧急释放接口清除欠账占用；该接口返回 409 `LEASE_IS_RETURN_DEBT`。（D2 第 1、2 条；D3 第 1 条）
17. 5.3 人工放行与后续 init（自 B6 激活起生效）：警告：只有 Owner 或管理员可以执行人工放行；执行者必须先取得本次生产写授权。（D2 第 1、2 条；D3 第 1 条；D4 第 3 条）
18. 5.3 人工放行与后续 init（自 B6 激活起生效）：1. 用 §5.1 第 3 步确定本次放行的专项与证据引用。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
19. 5.3 人工放行与后续 init（自 B6 激活起生效）：2. 如果没有本次写授权、原因类别或证据引用，停止本流程。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
20. 5.3 人工放行与后续 init（自 B6 激活起生效）：3. 以管理员身份调用人工放行接口；`$STP_API_BASE` 为已确认的控制面根 URL，`$AUTH` 按 `honor-flash-runbook.md`「鉴权（运维 curl 模式）」取得：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条；D4 第 5 条）
21. 5.3 人工放行与后续 init（自 B6 激活起生效）：4. 用 §5.1 第 2、3 步复读状态与剩余条目。 预期：所选专项为 unverified/override；关闭最后一个条目才释放欠账租约，部分放行仍保留 ACTIVE 租约。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条；D4 第 5 条）
22. 5.3 人工放行与后续 init（自 B6 激活起生效）：5. 查询本次放行审计：（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条）
23. 5.3 人工放行与后续 init（自 B6 激活起生效）：6. 在下一次已获授权的 job 事件流中读取 `init.return_check.power-cycle`。 预期：至多一条 STARTED、恰一条终态 trace；confirmed/executed_unverified 为 COMPLETED，其余为 FAILED，子执行保存在终态 metadata/output。 init 检查失败时计划 teardown 为 SKIPPED；patrol 断点恢复不重跑检查但照常 teardown。（D2 第 1、2 条；D3 第 5 条；D4 第 1、2、4、6 条；D4 第 5 条）

四份 ADR 的新增说明均为「后续修订（自 B6 激活起生效）」及对应条款链接，没有改变原决策句。
运维 README 的新增索引与模型字段说明为行为/结构事实，不增加操作规则；涉及机器枚举的原样写法按 D2 第 4 条处理。

## Revisit

返修完成后继续保持 Draft，等待规划者按新完整 HEAD 核对；Owner 控制 Ready。SOP 中的命令与 SQL 没有在生产执行。

#3723 当前正文、ADR 或代码入口变化时，重绑基线并核对本轮文档；若方案与代码不符，先在 #3723 退回 U7。
Owner 批准激活步骤后，由第二 PR 交付清单；激活并发布生效证据后，再按清单删除过渡内容与激活前分支。
