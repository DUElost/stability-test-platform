# 假性关闭事故：向已合并分支推送的提交静默丢失（#402）

Status: implemented（防线已按本 note 执行）
Class: process

## 事故

2026-08-24，PR #407 的 auto-merge 在第二个 commit（`51d75ae`，#402 在途守卫）
push 之前完成。该提交被推到一个**已被合并的分支**——GitHub 保留在分支引用上，
永不进入 main。我随后：

1. 看到 `state: MERGED` 即断定两笔提交都已合入；
2. 关闭 #402 并引用「已由 #407 合入 main（51d75ae）」——假性关闭；
3. #403 的文档锚点（CLAUDE.md 决策表、adr README 清单行）把「#402 已补」写成
   既成事实，随 PR #408 进入 main。

第三方审查用 `merge-base --is-ancestor` 抓出全部三处。根因：**用 PR 状态推断
逐笔提交的去向**。auto-merge 仓库里 PR 状态只说明「合并那一刻分支上有什么」，
之后到达的 push 与 main 无任何关系。

## 防线（本次执行）

1. **声称「合入」前必须验证 commit ancestry**：
   `git merge-base --is-ancestor <sha> origin/main && echo IN || echo NOT-IN`——
   不接受 PR 状态、不接受本地分支印象。
2. **issue 关闭评论里的 commit 引用必须先过第 1 条**；引用错误即 reopen 更正
   （本次已执行：#402 reopen + 撤评）。
3. **文档锚点不复述未核验的实现状态**——写「已合入 X」之前先跑第 1 条。
4. 流程偏好：一个分支一个 issue；在已有 open PR 的分支上追加提交前，先确认
   PR 尚未合并。

## 何时不重议

CI 若引入「merge 后分支残留新提交」的显式告警（GitHub 无原生能力），第 4 条可
降级为提示；其余三条与工具无关，长期有效。
