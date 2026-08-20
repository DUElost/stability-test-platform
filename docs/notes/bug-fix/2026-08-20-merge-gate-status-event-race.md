# merge-gate 运行被 cancel-in-progress 取消：writer 无后继，gate 永久缺失

- **状态**：已实施
- **类别**：bug-fix
- **日期**：2026-08-20
- **关联**：#267 / #268（历史竞态设计）、#325/#326/#327/#328（本批实测受害 PR）

---

## 决定了什么

`enable-auto-merge.yml` 的并发块**关闭 `cancel-in-progress`**，保留按 head sha
分组的并发键与 final guard。

背景：PR head 的 `pull_request` 运行在启动 3~7 秒后被取消（job 日志
`The operation was canceled`），且运行列表里**没有同 head 的后继运行**——
`status` 事件只对默认分支 commit 触发（实测全部 status 运行都落在 main 的
sha 上），所以不存在「后继 status 事件补写 gate」的路径。writer 被
`cancel-in-progress` 取消且无后继，`code-rabbit-gate` 永久缺失，PR 全绿却
停在 BLOCKED。

修法：`cancel-in-progress` 本来就是 #268 的 belt-and-suspenders——final
guard（写入前重查「自己是否仍是该 head 最新 run」，被取代即退出不写）已经
保证不会竞写/旧覆盖。关闭取消后，任何事件的运行都会跑完，由 final guard
收敛为「最新 run 写状态」；writer 不再可能无后继。

## 放弃的备选

- **放行全部 status 事件跑 merge-gate**：最初方案，但实测 `status` 事件只对
  默认分支 commit 触发，PR head 根本收不到 status 事件，修不到点子上。
- **并发组按事件类型拆分**：要重新论证竞写窗口，改动面更大，且没有证据表明
  取消来自事件类型冲突。
- **每次人工触发 `@coderabbitai review` 或手工写 gate**：只治标，且依赖
  CodeRabbit 配额，不可控。

## 如何验证

1. #330 自身的 pull_request 运行不再被取消，merge-gate 跑完并写出
   `code-rabbit-gate`（Actions app 身份，满足 app-scoped required check）。
2. 确认存在并发运行（同 head 多事件）时仅最新 run 写状态（final guard 回归）。
3. 本批 #325/#326/#327/#328 更新分支后全部自动合入。

## 何时重议

- 若并发运行的真实执行成本不可接受（同 head 多事件并存、10 分钟轮询路径
  反复出现），再考虑按事件类型拆分并发组并补竞写论证。
