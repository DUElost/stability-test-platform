# auto-merge 合入不触发关联 issue 原生自动关闭，backstop 每日补关

Status: implemented
Class: bug-fix

## Decision

`enable-auto-merge.yml` 用 `GH_TOKEN: secrets.GITHUB_TOKEN` +
`gh pr merge --auto --merge` 合入，合入者记为 `github-actions[bot]`。实测
GitHub 原生「合并关联 PR 自动关闭 issue」对这类 bot 合入不生效，且
`pull_request.closed` 事件同样被 GITHUB_TOKEN 级联限制抑制——两条自动路径都
接不住，关联 issue 只能靠人工补关（#221–#226、#324–#336 共 16 个 PR 全部
遗留 OPEN；#320 合入 76 分钟后才被人工关闭）。

在 `main-ci-backstop.yml`（每天 UTC 18:00 = 本地凌晨 02:00）追加
`Close linked issues after PASS` 步骤，与既有远端分支清理同批：

- 仅在 main 全量 CI `success` 后执行，并复用同一 main 快照守卫（main 已前进
  则跳过本次），与分支清理条件一致；
- 用 GraphQL `closingIssuesReferences` 取 GitHub 自己认定的关联 issue，而非
  正则解析 PR body——这正是原生自动关闭所依赖的连接，最权威；
- 窗口限定近 30 天合入的 PR，避免每晚全量扫描（按 UPDATED_AT 翻页时
  mergedAt 不保证单调，必须翻完所有页再按 mergedAt 过滤，不能提前 break）；
- 关闭前逐条复查 issue 仍 OPEN，避免查询与执行之间被人抢先关闭时重复关闭
  报错；关闭时附「auto-merge 未触发原生自动关闭，由 backstop 补关」评论。

## Alternatives

- **改用人类 PAT 合入**：可恢复原生自动关闭与 `closed` 事件级联，但需要新增
  PAT secret、权限面大于 GITHUB_TOKEN，且合入后会重新触发全部 post-merge
  workflow，CI 行为面改动大，未采用。
- **在 `enable-auto-merge.yml` 的 `closed` 事件补关**：实测 bot 合入不触发
  `closed` 事件（GITHUB_TOKEN 级联限制），方案不可行。
- **正则解析 PR body 提取 `Closes #N`**：PR body 与 GitHub 实际建立的
  closing reference 可能不一致（大小写、格式、编辑历史），不如
  `closingIssuesReferences` 权威，未采用。

## Verification

- 本地 YAML 语法解析通过；
- 查询/关闭逻辑用当前仓库实况试跑：近 30 天合入 PR 中无仍 OPEN 的关联
  issue（12 个遗留 issue 已人工补关），预期 closed=0、无失败；
- 合入 main 后次日凌晨 2 点 backstop 实际执行生效；日志出现
  `linked-issue close done: closed=N skipped=N`。

## Revisit

若 GitHub 修复 bot 合入的原生自动关闭，或仓库改回人类身份（PAT）合入，
可移除该兜底步骤，仅保留分支清理。
