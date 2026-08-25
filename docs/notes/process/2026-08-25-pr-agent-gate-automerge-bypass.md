# #421：pr-agent-gate failure 仍被 auto-merge 合入（PR #399）

Status: implemented
Class: process

## 事故

2026-08-24，PR #399（上线前兼容回退清理）在 head `bc1dbc0` 上
`pr-agent-gate` **failure**（06:13:05Z，原因「PR Agent review output not
found」——该 PR 全程 0 条 PR-Agent 评论）之后，仍于 06:17:23Z 被
`github-actions[bot]` auto-merge 合入 main。

同期对照：同日前后合入的 #396/#397/#398/#400 的 gate 均为 success；#399
每个有 gate 的 head（`835e47f` / `c3367eb` / `bc1dbc0`）gate 均为 failure。

## 排查结论

1. **`pr-agent-gate` 已在 main branch protection 的 required checks 列表中**
   （与 lint / CodeQL / pr-typecheck / pr-compileall / pr-agent-tests 并列；
   `enforce_admins=true`，`strict=true`）。issue 原文「可能不在 required
   checks」假设不成立。
2. **合入路径是 GitHub 原生 auto-merge**（`auto_merge_enabled` 于 05:07:15Z；
   `enable-auto-merge` 在最后一次 synchronize 上因已启用而 no-op），不是
   workflow 直接 `gh pr merge` 强合。
3. 在 required check 已 failure 的情况下仍合入，属于 **GitHub auto-merge 与
   required-check 执法之间的漏判/竞态**（平台侧无法从仓库配置再收紧同名
   check）。替换 CodeRabbit 时删掉了应用层 `merge-gate` job，把门禁完全交给
   branch protection——少了一层本地否决。

## 防线

`pr-agent.yml` 的 `pr-agent-gate` job 在 `failure()` 时显式
`gh pr merge --disable-auto`（#421）：

- branch protection 仍是主执法；
- 失败瞬间撤掉 auto-merge，避免「gate 已红、auto-merge 仍挂着」窗口被平台
  漏判合入。

复评路径不变：修复后 push → synchronize 重跑 gate；`enable-auto-merge.yml`
会再次挂上 auto-merge。

## 何时不重议

若 GitHub 修复「required check failure 仍 auto-merge」并有官方公告，可评估
去掉 `--disable-auto` 步骤；在那之前保留双保险。
