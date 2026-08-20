# merge-gate status 事件竞态：writer 被 Expected status 取消且无后继

- **状态**：已实施
- **类别**：bug-fix
- **日期**：2026-08-20
- **关联**：#267 / #268（历史竞态设计）、#325/#326/#328（本批实测受害 PR）

---

## 决定了什么

`enable-auto-merge.yml` 的 merge-gate 对 status 事件从「仅放行
`context == 'CodeRabbit'`」放宽为「放行除自家 `code-rabbit-gate` 外的全部
context」。

背景：push 时分支保护会为 required context `code-rabbit-gate` 创建一条
`Expected — Waiting for status to be reported` 的 commit status。该 status
事件按 #268 的并发规则（同 head 共享 concurrency 组 + cancel-in-progress）
取消正在运行的 synchronize run——而后者才是本会写 gate 的 writer；随后续
到达的 status 事件又因 context 条件被 job 级 `if` 跳过，writer 被取消且无
后继，`code-rabbit-gate` 永久缺失，PR 全绿却停在 BLOCKED。

修法沿用既有 final guard 收敛语义：任何 status 事件都可能成为 writer，但
写入前重查「自己是否仍是该 head 最新 run」，被取代即退出不写；自家
`code-rabbit-gate` context 被显式排除，防止写 gate 后自我触发死循环。

## 放弃的备选

- **并发组按事件类型拆分 / 非 writer 事件不取消 writer**：能省掉 status 事件
  的真实执行成本，但要重新论证竞写窗口，改动面更大。
- **每次人工触发 `@coderabbitai review` 或手工写 gate**：只治标，且依赖
  CodeRabbit 配额，不可控。

## 如何验证

1. 对任意全绿但缺 gate 的 PR head POST 一条无害 probe commit status，确认
   status 事件触发 merge-gate 并写出 `code-rabbit-gate`。
2. 确认 gate 写入后不产生新的 workflow 运行（无自循环）。
3. 本批 #325/#326/#327/#328 全部经该路径合入。

## 何时重议

- 若 status 事件触发频率导致 runner/API 成本不可接受（burst 排队、10 分钟
  轮询路径反复出现），改回按事件类型拆分并发组并补竞写论证。
