# B6 U2：控制面解除结果落库、宽限续期与墙钟校验

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) v1.2 §3.3
与 ADR-0061 D3.6/D3.8、D4、D7.1 实施 [#3727](https://github.com/DUElost/stability-test-platform/issues/3727)。
Draft PR 待独立复核；合入不等于生效（控制面部署按 §5 第 3 步，在 Agent 热更新之后）。

## Decision

1. 迁移 `c3d4e5f6a7b8` 仅新增可空 JSONB 列 `job_instance.disarm_results`。
2. `/complete`（`_RunCompleteIn`）接受 `disarm_results`；缺失时按 §3.0 第 6 条从
   teardown step_trace + `pipeline_def` 推导；持久化到列，**不**写 `status_reason`。
3. `_VALID_EXECUTION_STATES` 增加 `EXECUTING_DISARM`；abort reaper 对
   `EXECUTING_DISARM` 且 `last_progress_at` 在两个协调心跳周期内的 job 续期至
   `abort_at + 解除步骤 timeout_seconds`，超上限或进度中断则按现行规则回收。
4. Plan 保存时对 ONBOARDED 登记了 `min_wall_clock_seconds` 的步骤做结构化 422；
   U1 未合入 / U5 未登记时 ONBOARDED 为空，现有步骤不拒（预期）。
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

## Verification

见本 PR 正文「门禁」：`check:quick`（schema-at-head 对本机生产库 behind 属预期中间态，
迁移不得在生产库试跑）、根 `tests/`、受影响后端测试、`promtool test rules`、
前端 `tsc`；反例测试覆盖迁移链、带/不带 `disarm_results` 的推导表、reaper 续期/
回收、墙钟 422、指标计数、告警场景。

## Revisit

- U1 合入后 rebase，将字面量改为 `from backend.agent.contracts.device_return import …`。
- U5 写入 ONBOARDED 后，墙钟校验对开关机步骤生效；用真实 `min_wall_clock_seconds`
  公式回归一次。
- U3 接管 §1.2 租约释放点；本单元未改那些调用点。
