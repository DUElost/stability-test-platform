# Dependabot：每日 cron、cooldown 与 github-actions 生态
Status: implemented
Class: process

## Decision

pip / npm 从 weekly 改为每日 04:00（Asia/Shanghai）cron（夜间批量，白天
零打扰）；两组均加 `cooldown.default-days: 30`（同一依赖 30 天内最多一个
版本更新 PR，压低噪音）；新增 `github-actions` 生态（同样 cron + cooldown）。

actions 仍按 SHA 固定，由 Dependabot 自动跟进安全/版本更新；更新 PR 必须
过现有轻量门禁才会被 auto-merge，夜间全量 CI 再复验。cooldown 只作用于
版本更新，安全更新不受影响。

## Alternatives

- 保持 weekly 且无 cooldown：拒绝。PR 噪音与「白天零干扰」哲学冲突。
- actions 继续人肉追踪：拒绝。按 SHA 固定 + 人工升级留下安全更新盲区；
  自动 PR 叠加 SHA 固定保留可追溯性，两者兼得。

## Verification

下一个凌晨 cron 产生的 PR：观察分组、cooldown 行为与 auto-merge 门禁
（lint / pr-typecheck / pr-compileall / pr-agent-tests 全绿才合入）。

## Revisit

若 cooldown 实际造成关键版本滞后，评估为安全相关依赖缩短窗口。
