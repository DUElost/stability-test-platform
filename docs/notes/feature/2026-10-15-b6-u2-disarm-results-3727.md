# B6 U2：控制面解除结果落库、宽限续期与墙钟校验

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) **v1.5** §3.3 / §3.3.1 / §3.3.2
与 ADR-0061 D3.6/D3.8、D4、D7.1 实施 [#3727](https://github.com/DUElost/stability-test-platform/issues/3727)。
Draft PR 待规划确认与独立复核；合入不等于生效（控制面部署按 §5 第 3 步，在 Agent 热更新之后）。

## Decision

1. 迁移 `c3d4e5f6a7b8` 仅新增可空 JSONB 列 `job_instance.disarm_results`。
2. `/complete`（`_RunCompleteIn`）接受 `disarm_results`；缺失时按 §3.0 第 6 条从
   teardown step_trace + `pipeline_def` 推导；持久化到列，**不**写 `status_reason`。
   **v1.5 R2**：按 `original_ts`（并列用稳定键）选取解除终态；迟到 STARTED/RUNNING
   不得覆盖其后 COMPLETED/FAILED；后发生 FAILED 胜过旧成功；无终态证据 → `not_executed`。
3. `_VALID_EXECUTION_STATES` 增加 `EXECUTING_DISARM`。**v1.5 R1**：abort 主体时钟与解除
   宽限纯判断收敛到 `job_timeout_config`，abort reaper 与通用 `recycler` 共用；
   `EXECUTING_DISARM` 进入已知执行态（`last_execution_heartbeat_at`）；受有效 abort 主体
   覆盖且进度在两协调心跳周期内、未到 `abort_at+墙钟` 时两回收器保持 RUNNING；
   候选 SQL / Python 判定 / 行锁复读 / CAS、patrol-stall 与 step-trace-stall、
   `plan_run_devices.running_heartbeat_deadline` 同步承接保护。真实 host/租约失效不永久豁免。
   **规划核验补齐**：running-timeout 与 step-trace-stall 的最终写入口共用
   `_final_disarm_grace_blocks_write`，以列投影读取最新已提交 `PlanRun.run_context`
   （避开 Session identity map），候选预取仅作预筛；陈旧候选在预取后、写入前转为
   受保护解除时必须放弃写入（不追加反向 PlanRun 行锁）。
4. Plan 保存时对 ONBOARDED 登记了 `min_wall_clock_seconds` 的启用步骤要求**显式**
   正整数 `timeout_seconds` 且 ≥ 有效参数算出的登记预算（#3723 v1.3/v1.5 §3.3.1）；
   省略/null/0/负数/低于预算 → 422 `WALL_CLOCK_BELOW_ONBOARDED_BUDGET`（省略记 null）；
   **不**读取控制面 `STP_STEP_WALL_CLOCK_SECONDS`。**v1.5 R3**：创建入口在通用 lifecycle
   抢先拦截 0/负数之前执行登记预算校验，三入口统一 code/message/violations。
5. 指标 `stp_disarm_result_total{family,result}` 与两条 warning 告警（1h
   not_executed|incomplete 增量；单专项 24h failed 占比 > 5%）。
6. job 详情 schema / `types.ts` 同步暴露 `disarm_results`。

U1 拥有 `backend/agent/contracts/device_return.py`：本单元**未**创建或修改该文件。
控制面用与方案一致的字面量（FAMILY_STEPS / EXECUTING_DISARM / 六值），并在
`agent_completion` / `plans` 标明「U1 合入后改为从 device_return 导入」。
`plan_run_catalog._job_out` 增补一行映射（§3.3.6 API 面必需）。

## Alternatives

- 把推导逻辑放进新服务模块：超出 declare scope，故内联于 `agent_completion`。
- 指标名用 `stability_` 前缀：方案 §3.0 第 7 条写明 `stp_disarm_result_total`，从方案。
- U1 未合入前复制 `device_return.py` 进本树：违反「U1 拥有该文件」约束，未采用。
- 在两个回收器各写一套宽限规则：违反 §3.3.2 A「不得两套规则」，未采用。
- 控制面 env 代签省略墙钟：v1.3 已否决；异宿主 Agent 默认 300 会低于登记预算。

## Verification

见本 PR 正文「门禁」与 #3723 回报：`check:quick`；根 `tests/`；受影响 scheduler /
completion / wall / 迁移 / metric 契约；Agent 套件；真实 PG `recycle_once` 正反对照
（新鲜解除存活、进度陈旧可回收、执行心跳旧但进度新仍保护）；三入口墙钟矩阵（含
create 的 0/−1）；乱序 helper + `/complete` late_started；`promtool`；前端 `tsc`。
**R1 最终保护复验**：`test_planner_u2_context_race`（running + trace）修前红→修后绿；
与既有 recycler 反例同批验证。未跑 #3739 危险 cancel 变异。迁移
`down_revision=9b7e24a6d081`（本轮未改迁移链）。

## Revisit

- #3723 v1.5：R1 最终保护复验已按规划核验补齐；R2 乱序、R3 创建入口错误契约保留；
  待规划再确认与独立复核。
- U1 合入后 rebase，将字面量改为 `from backend.agent.contracts.device_return import …`。
- U5 写入 ONBOARDED 后，墙钟校验对开关机步骤生效；用真实 `min_wall_clock_seconds`
  公式回归一次。
- U3 接管 §1.2 租约释放点；本单元未改那些调用点。
