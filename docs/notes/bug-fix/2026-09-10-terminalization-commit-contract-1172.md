# Main CI backstop 修复：终态化调用方适配 on_job_terminal 自管理提交（#1172）

Status: implemented
Class: bug-fix

## Decision

main 全量 CI（backend-test）红 6 个测试，模式统一：UNKNOWN grace 到期
未 FAILED（`failed=0`）、pending timeout 未聚合、lease 未释放。根因：
**#986 起 `on_job_terminal`(_sync) 在聚合后内部 `db.commit()`**（父终态
先提交再触发链式派发，防子 Plan prepare 失败回滚父完成事实）——而
`device_lease_reconciler` 与 `recycler` 在 `begin_nested`（SAVEPOINT）内
调用它：内部 commit 终结外层事务，返回后继续使用 db 抛
`InvalidRequestError: Can't operate on closed transaction inside context
manager`，异常被外层 except 吞 → 计数 0、终态化静默缺失。

修复（**调用方适配 #986 契约**，不改 #986 语义）：

1. `device_lease_reconciler` Check1 Phase2 与 Check2：终态 transition +
   lease release 留在 SAVEPOINT 内（退出即提交），`on_job_terminal` 移到
   嵌套事务之外、由函数尾部统一执行一次后返回（本 tick 收尾；其余候选
   下轮 tick 处理——reconciler 为周期任务，可接受）；
2. `recycler._mark_pending_timeout` 增 `defer_aggregation`：置位时只完成
   终态落库/租约释放/审计/metrics；调用方在 SAVEPOINT 提交后聚合（聚合
   commit 终结事务，break 余批——外层 while 会续轮处理）；
3. `agent_api` 两处调用无嵌套事务（其后 commit 为空提交），不动。

bisect 定位回归为 #986 合入（`job_terminalization` 重构）；本修复验证了
#986 变更破坏的调用面仅 reconciler/recycler 两处。

## Alternatives

- **回退 #986 的 commit 契约（on_job_terminal 不内部提交）**——放弃：
  #986 的语义（父终态先提交、chain 失败只影响子 Run）依赖该提交点；
  逐调用方适配比回退契约安全（/complete 路径已按新契约工作）；
- **reconciler/recycler 改用每候选独立顶层事务（弃 begin_nested）**——
  放弃：改动面更大且丢失单候选失败隔离；「终态化延后到 savepoint 外 +
  tick 收尾」保留隔离且改动局部。

## Verification

- CI 红 6 测试本地复现（reconciler 4 + recycler 1 + socketio 1）→ 修复后
  相关全套 **32 passed**；`test_job_terminalization`/`test_plan_chain_
  trigger`（#986 语义回归）绿；
- 基线对比（回退修复）：5 failed 复现，确认修复消除；
- scheduler 目录独跑存在预存 TRUNCATE 顺序 error（基线即有、CI 全量
  顺序未现、单文件绿）——非本次回归（Revisit）；
- `check:quick` 与 PR 门禁：见 PR 描述；main 全量 CI 由 backstop 工作流
  复跑确认（绿后自动关 #1172）。

## Revisit

- `on_job_terminal`(_sync) 的「自管理提交」契约已成为其公共 API 语义：
  未来任何在事务上下文（begin_nested/未提交事务）内调用它的新代码都会
  重蹈 #1172——应在 job_terminalization.py 的 docstring 顶部显式声明
  「调用方不得在嵌套/打开事务内调用」（补文档义务随本单）；
- scheduler 目录独跑的 TRUNCATE 顺序 error 未定位留锁测试——若 CI 全量
  顺序变更使其浮出，按「TRUNCATE 前 session 泄漏」方向排查。
