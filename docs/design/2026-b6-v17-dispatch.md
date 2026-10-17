# B6 v1.7 条件派单

状态：**仅规划，不可编码**。Owner 尚未裁决 [#3751](https://github.com/DUElost/stability-test-platform/issues/3751)。
本文件供裁决后的派单使用，不构成已派发、已领单或实施授权。
完整候选正文见 [v1.7](2026-b6-v17-planning-proposal.md)，模型提案见 [ADR 修订](2026-adr0061-v11-proposal.md)。

## 1. 规划收口的最后步骤

1. 读取 Owner 在 #3751 或当前会话对提案 §6 五项的原始裁决。
2. 如果存在退回、参数修改或尚未裁定项，停止派单并修订提案。
3. 按裁决同步 Accepted ADR-0061；通过文档 PR 处理，保持未裁定部分为 Proposed。
4. 把裁决链接写进 v1.7 正文 §2。
5. 把完整 v1.7 正文写入 #3723，检查标题、§3.1/§3.6/§5 版本与 U8 #3752 一致。
   预期：`gh issue view 3723 --json title,body` 显示获批 v1.7 与可访问的裁决引用，而非只在评论追加规则。
6. 复读正文与开放 PR，再派发下面对应提示词。

复制候选正文到 GitHub 时，规划者必须把本地相对文档链接转换为已合入的 GitHub 文档链接；
Owner 裁决前不得去掉“待裁决”而发布为正式实施规范。当前 U1/U2 历史通过不代替本轮裁决。

## 2. U3 原实施者提示词

```text
你是 B6-U3 原实施者，继续 #3728 / draft PR #3748 / 原 Execution。
先读 AGENTS.md、#3723 全文和全部评论、Owner 对 #3751 的裁决、batch-planning.md及当前实际 diff。
硬前置：#3723 已是获批 v1.7，含 §3.4.2 CLI 事务/重试规格。缺少时只读准备并报告，不编码。

刷新 origin/main、PR完整HEAD和评论、开放PR，运行：
./scripts/project_python.sh tools/dev/ai_work.py status --risk
核对原worktree/分支，不覆盖他人改动，不重复declare；若需更新scope，必须保留原完整范围（update会替换）。
只改 v1.7 §3.1 U3 范围，落实 §3.4.2 与 §4 U3/4.1/4.2。
必须测真实CLI两设备跨事务NOWAIT，不以service测试代替；按正文100个id分页、每台独立事务及最多3次冲突重试。
共享服务的类别政策和平台终止消费交U8，不改Agent/共享契约/包/模板。

按test-env-self-check隔离PG和内存保护runner，保留有限红绿与真实退出状态；禁止 #3739 危险变异。
更新原Agent Note四节、原PR与本批记录，等待最终HEAD六项required CI、Planner确认及独立复核。
保持draft；不得Ready/auto-merge/merge/部署/生产迁移/初始化/释放欠账/设备操作。
```

## 3. U5 原实施者提示词

```text
你是 B6-U5 原实施者，继续 #3730 / draft PR #3747 / 原 Execution。
先读 AGENTS.md、Agent scoped AGENTS、#3723全文与评论、Owner对 #3751 的裁决、实际diff与脚本版本SOP。
硬前置：#3723已是获批v1.7，明确 §3.0.2、§3.6.2–4、§4 U5。缺少时只读准备，不编码。
刷新origin/main/完整HEAD/开放PR/Registry --risk；保留原领单和全部scope，不重复declare。

只按 §3.1 U5 范围实施job实际版本角色helper和最小调用适配、等待上线轮询进度、脚本追加版本/pin与历史包恢复。
旧finish1.0.x仍解除，新finish1.1.0只收取；未知/混合/缺版本必须按正文处理。
目标disarm1.0.3，已占用或冻结后改字节时按正文顺延并同步 §3.1/§3.6/§5；finish1.1.0保持原包。
不改U8的reaper/guard/类别政策设计，不临时加入新状态/env/阶段B开关。

必须测真实Engine→runner载荷与旧计划manual_exit/abort/自然结束，15s有界轮询/60s以上离线后恢复和预算到期。
四个旧登记包按正文原提交恢复验SHA；不得删除冻结条目，不publish。
真实设备只用Owner指定独立MLD_LX2/LX3/NX2；缺设备时真机pending/空验证表/draft，不虚填。
按 §4.2检查，最终完整SHA六项required CI、Agent Note与实际pending回告。
保持draft；不得Ready/auto-merge/merge/publish/scan/热更新/重指/部署/生产设备操作；不得 #3739 危险变异。
```

## 4. U8 独立实施者提示词

```text
你是 B6-U8 实施者，负责 #3752。读 AGENTS.md、Agent scoped AGENTS、#3752、#3723全文和评论、
Owner对 #3751 的原始裁决、正式ADR以及U1/U2原独立复核。
硬前置：获批v1.7已发布，U3/U5均合入，当前main包含它们最终提交。缺任一只读准备，不编码/declare。
刷新开放PR、origin/main和Registry --risk，核对实际diff共享文件已结束编辑。

在独立worktree、分支 feat/3752-device-return-guard 开工，PR目标main，不stack、不复制父分支diff。
项目入口先status --risk，再用本单号declare（不是批次3723）：
./scripts/project_python.sh tools/dev/ai_work.py declare --requirement B6-U8 --harness '<当前harness>' \
  --worktree '<独立worktree绝对路径>' --issue 3752 \
  --scope backend/agent/contracts/device_return.py \
  --scope backend/agent/pipeline_engine.py --scope backend/agent/api_client.py \
  --scope backend/agent/pipeline_runner.py --scope backend/agent/coordinator.py \
  --scope backend/agent/lease_renewer.py \
  --scope backend/agent/registry/local_db.py --scope backend/models/job.py \
  --scope backend/alembic/versions --scope backend/api/schemas/run.py --scope backend/api/schemas/agent.py \
  --scope backend/api/routes/agent_api.py --scope backend/api/routes/agent_device_return.py \
  --scope backend/services/agent_lease_extend.py --scope backend/services/agent_coordinator_heartbeat.py \
  --scope backend/services/plan_run_abort.py --scope backend/core/job_timeout_config.py \
  --scope backend/scheduler/device_lease_reconciler.py --scope backend/scheduler/recycler.py \
  --scope backend/services/plan_run_devices.py --scope backend/services/agent_completion.py \
  --scope backend/services/disarm_derive.py --scope backend/services/device_return.py \
  --scope frontend/src/utils/api/types.ts --scope backend/agent/tests --scope backend/tests --scope tests \
  --scope docs/notes/feature/2026-10-17-b6-u8-device-return-guard-3752.md --test-impact direct

只实现 §3.0.5/6、§3.8 与 §3.1 U8范围；按 §4.1 U8有限清单和 §4.2验证。
Owner批准的预算、模型和拒绝政策不得自行改；U4负责清偿执行/init功能，本单只提供共用政策及相应控制面消费。
所有相关最终写入口必须复读，不能只测helper或豁免所有WAITING。真实失租/host失联/旧token仍拒绝，不复活终态。
必须在同时包含U3/U5/U8的唯一最终SHA运行集成、隔离迁移、三个测试根与required六项CI。
按test-env-self-check使用隔离PG/内存保护runner/信号桩，不运行 #3739 危险cancel变异。

新增draft PR，Refs #3723和#3752，Agent Note四节；报告实际完整SHA/命令/最终退出/红绿与pending。
使用ai_work update --pr登记自己的PR，完成编码按契约finish；不得修改他人Execution。
清单外路径/新模型/协议/失败不能据现有规范判定时立即退回Planner，不靠追加无穷返修扩面。
不得Ready/auto-merge/merge/部署/生产迁移/初始化/publish/scan/热更新/重指/设备操作。
```

## 5. 后续单元

- U4 #3729：U3/U5/U8合入后，按正式v1.7 §3.5/§3.8执行；与U8共享Agent文件串行。
- U6 #3731：U3稳定API后按 §3.7/§4推进；真实浏览器验收按testing.md §4。
- U7 #3732：U8后按最终协议同步；与本轮规划文档串行；维护窗口的具体命令由激活授权确定。
- 独立复核：由Owner指定不同工作面；按batch-review.md只签最终完整SHA；不得由本规划会话自审。
