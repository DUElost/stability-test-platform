# Dependabot：每日 cron、cooldown 与 github-actions 生态

Status: implemented
Class: process

## Decision

pip / npm 从 weekly 改为每日 04:00（Asia/Shanghai）cron（夜间批量，白天
零打扰）；两组均加 `cooldown.default-days: 30`（官方语义：同一依赖的 PR
合并/关闭后 30 天内不再开新的**版本更新** PR，压低噪音；安全更新不受
cooldown 约束）；新增 `github-actions` 生态（同样 cron + cooldown）。

actions 仍按 SHA 固定，由 Dependabot 自动跟进安全/版本更新。两类 PR 的
合入路径不同：

- **github_actions 更新**：**排除 auto-merge、人工评审**。轻量门禁对
  action 内容零检测力，而 enable-auto-merge 的 job 持有 contents /
  pull-requests 写权限、backstop 的 verify-and-cleanup job 持有 actions /
  contents 写权限与 pull-requests 读权限、notify job 持有 issues 写权限
  ——投毒 release 经自动合入即可在对应 job 运行时拿到这些权限；人工看一行
  SHA diff 成本近零（#267 CodeRabbit 评审意见）。
- pip / npm 更新：过现有轻量门禁后 auto-merge，夜间全量 CI 再复验。

## Alternatives

- 保持 weekly 且无 cooldown：拒绝。PR 噪音与「白天零干扰」哲学冲突。
- actions 继续人肉追踪：拒绝。按 SHA 固定 + 人工升级留下安全更新盲区；
  自动 PR 叠加 SHA 固定保留可追溯性。
- actions 更新保持全自动（曾考虑）：拒绝。见 Decision，供应链风险无法
  被轻量门禁抵消，人审成本近零。

## Verification

下一个凌晨 cron 产生的 PR：观察分组与 cooldown 行为；github_actions
更新 PR 应**无** auto-merge 挂载（人工评审），pip/npm PR 在轻量门禁
全绿后自动合入。

## Revisit

若 cooldown 实际造成关键版本滞后，评估为安全相关依赖缩短窗口。
