# 通知/后台池 env 参数登记（#1167 收尾 follow-up）

Status: implemented
Class: process

## Decision

PR #1469（#1167 P5）复查 F1：P4/P5 引入的参数未登记权威清单
`docs/development/environment-variables.md`——同族的 `BACKGROUND_POOL_MAX_QUEUE`
（#1122）与 `STP_SMTP_TIMEOUT_SECONDS`（#1122）已在清单内，口径不一致。本次补 5 行：

- `BACKGROUND_POOL_SIZE`（#1122 遗漏，默认 `8`）；
- `STP_NOTIFY_WEBHOOK_TIMEOUT_S` / `STP_NOTIFY_DINGTALK_TIMEOUT_S`（#1167 P4，
  默认 `10`，通道级 deadline）；
- `STP_NOTIFY_SAQ_RETRIES` / `STP_NOTIFY_SAQ_TIMEOUT_S`（#1167 P5；前者默认
  派生自 D5 策略上限 `DEFAULT_RETRY_POLICY.max_attempts`、非法值回落、下限 1；
  后者默认 `120`）。

来源逐一对照 main / P5 分支的常量默认值，**无新语义**。本 PR 堆叠于 #1469
（其合入后本 PR diff 收敛为纯文档）。

## Alternatives

- **等 P5 作者顺手补**：跨会话往返慢，且 F1 已由其 PR 评审记录在案，独立收口更干净；
- **参数只写各自 Note、不进 env 清单**：env 清单是「按需入口」权威面，Note 是过程
  记录，二者分工不同——参数化后没有清单入口即回到漂移前提。

## Verification

- `python tools/dev/check_governance_surface.py --check` 全绿；
- 5 行逐一对照常量默认值核对（WEBHOOK/DINGTALK `10.0`；SAQ retries 策略派生、
  timeout `120`；POOL_SIZE `8`）；
- docs-only（无代码/测试面），`check:quick` 通过。

## Revisit

- 若 ADR-0011 可观测性批次为通知投递引入指标/阈值参数（#1469 复查 F2 的口径），
  其阈值同样登记本清单，不再散落 Note。
