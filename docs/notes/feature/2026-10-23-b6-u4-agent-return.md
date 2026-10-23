# B6-U4：Agent 清偿与 init 残留检查

Status: implemented
Class: feature

## Decision

Refs #3729，Refs #3723。按批次正文 v1.7.17 实现 U4，延续原 Execution B6-U4、分支 feat/3729-agent-return 与 draft PR #3773。
依据包含 ADR-0061 Accepted v1.1、v1.7.16 对第一次退回（6367520373）的入口政策裁定，以及 v1.7.17 对第二次退回（6367963867）的结果分离裁定。此次没有修改普通完成入口。

每个 job 进入 init、任何计划步骤开始之前，执行 ONBOARDED 登记的 powercycle_disarm@1.0.8 probe；不能可信确认无足迹时执行同版本 disarm。
正式结果复用 Engine 的 U8 汇总与共享政策。confirmed 或 executed_unverified 才继续；其它正式值以 init 失败结束。包不可用时以 package_unavailable 失败，不上报结果。
precheck 失败时没有计划步骤开始，不启动该 job 的计划 teardown；若计划 init 已开始，后续 teardown 沿用原执行链与 U8 有界保护。

init 结果只经 POST /api/v1/agent/jobs/{job_id}/return-check 上报，并以 init 阶段的 return_check.<family> trace 留痕。
probe、disarm、最终回报使用合成步骤的不同尝试序号，避免既有 trace_event_id 去重丢失证据。
汇总临时容器与 job 的解除结果分开；终态 disarm_results 只取本 job 的足迹专项。
控制面应用函数按 Job → Device → Lease 加锁，欠账读写均在 Device 锁内。
令牌先于政策核对；合法结果写 source=init_check；confirmed 关闭专项全部 open 条目；失败创建 source_kind=job/source_job_id=当前 job 的条目，cause 含 init_check 与正式值。
相同失败回报在条目关闭后重放也保持原关闭事实。不可信特权值按专项返回 422，只写既有诊断审计；同一回报的其它合法专项照常提交。
审计身份由当前 job 与令牌摘要提供，同令牌重放不增行，换令牌分别计数。check_init_probe_result 只增加可选诊断身份透传，政策判断不变。

心跳通过可选 return_init_check_available=true 声明入口；Agent 保存到现有 LocalDB 状态。构造心跳时清除旧声明，成功心跳缺字段时禁用上报；网络无响应不冒充旧控制面。
未声明时仍检查与解除，按阶段 B 生效前规则继续或失败。confirmed 的专项 422 降级为 executed_unverified 再报；未送达时沿用 api_client 的次数、超时与退避，耗尽后以 init_check_unreported 失败。

清偿模块消费 return_debts，只执行已接入专项。首次令牌、真实重连触发；failed/incomplete/not_executed 或未送达在至少 30 分钟后再试。
executed_unverified 与专项 422 不周期重试，重连或令牌变化可以再次尝试。重复 recovery 回调按连接代次合并。没有字段时清空待执行缓存。
执行前、取得 permit 后及每个专项开始前检查本地活跃 job；整个动作与回报持有设备 permit。409 删除令牌缓存并保留拒绝摘要，避免心跳旧令牌再次触发。

## Alternatives

- 结果核验复用 _record_disarm_for_step 与 U8 共享契约，未复制可信组合或核验规则。v1.7.16 唯一共享政策修改是让 init 的非特权正式值与清偿一样通过。
- 按 v1.7.17 分离 init 回报与终态载荷，保留 /complete 原处理。对不能确认无足迹的 probe 先解除；未安装应用的设备仍执行登记 disarm，结果最多为未验证。
- 清偿复用心跳现有晚绑定 concurrency_snapshot 所属的 OperationScheduler，不另建调度器。现有 start_host_control_plane 将该回调绑定到 host 全局实例；绑定未完成或类型不符时不执行。真实绑定测试验证同一实例、活跃集合与 LocalDB 恢复占用。
- Engine 在调用 init 检查时提供 StepResult 构造器，心跳接线层为清偿提供 PipelineEngine 构造器；清偿模块不反向导入 Engine，避免新增循环依赖与函数内导入。
- 既有 U1/U8 Engine 测试只补“已完成新 precheck”的证据前置，原断言保留；新 U4 测试显式请求 real_return_checks，运行真实注册包、脚本、Engine 与 pump。
- 新 schema 与客户端方法属于 Agent 私有协议，没有 Web 消费面或前端交互修改。没有新脚本版本、迁移、env、预算或阶段 B 开关。

## Verification

所有 pytest 经项目入口及内存保护 runner；Agent 使用 env -i，PG 使用 testcontainers。
冻结包由未修改族树重建并与 manifest 的 98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557 对拍，经真实包校验与缓存解析后执行其代码。
设备替换 adb 与 prefs 写入 I/O；进程用有限 StringIO 流接入真实 pump，信号边界由可信桩拒绝。时间测试使用有限虚拟时钟。

- v1.7.16 政策反例：原实现 5 failed、3 passed；修正与 U8 API 回归 69 passed；撤回判断再次 5 failed、3 passed，子 pytest 退出 1，驱动退出 0并恢复原字节。
- 新 init API 缺失时 12 项反例全部失败（退出 1），接线后通过。
- 关闭后的失败重放、不同租约诊断身份两项：修正前 2 failed，修正后通过。
- Agent 新功能 35 passed，退出 0：真实 heartbeat tick/重连钩子、冻结包 Engine、真实 runner 载荷隔离、先回报再建立足迹、旧控制面、失租、平台中断、422 降级、有界重试、失败前置不启动计划 teardown，以及清偿触发/permit/活跃/拒绝矩阵。
- 后端定向 91 passed，退出 0：新 API、三组 I5、原政策反例及 U8 四入口。包含冻结脚本 → Engine/runner → 真实 init API → PG → 真实 complete API；非开关机 job 的 returned/unverified 保持，开关机 job 自身 teardown 失败独立建账。
- I5：A 会话分别持 Job、Device、Lease；B 执行真实 HTTP 并进入 PG 锁等待；C 会话用 FOR UPDATE NOWAIT 探测已持锁前缀，释放 A 后请求完成。全程有界条件等待。
- 有限撤回工具：./scripts/project_python.sh -m backend.tests.return_withdrawals_3729 --run --output-dir <证据目录>。覆盖 30 个修复点，并运行旧 HEAD d4008e013 的真实 Engine 基线反例。基线 cancel、终止、pump 与 U8 guard 函数先核对 AST 同一性，不撤回信号安全或有界保护。每个子测试以真实行为失败证明反例，finally 恢复全部源码原字节，无主动 pytest.fail。

完整测试根与门禁命令（最终退出状态与完整 HEAD 在 PR 正文回告）：

```sh
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/ -q
env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py backend/tests/ -q
env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b6-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py tests/ -q
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_gates.py check:quick
```

上一轮 d4008e013 的三根与 check:quick 均已退出 0，作为旧提交证据保留。本轮最终代码单独重跑；六项 required CI 与 informational 只按最终远端 HEAD 的终态回告。运行中、跳过、未运行均不算通过。

## Revisit

交付后先由规划者核对，再交独立复核者。PR 全程 draft；Ready、auto-merge 与 merge 不由实施者操作。
没有生产迁移、初始化、部署、设备操作、publish/scan/热更新/重指。真机验证留在整批激活 L2，本地 I/O 替身不代签真机结果。
