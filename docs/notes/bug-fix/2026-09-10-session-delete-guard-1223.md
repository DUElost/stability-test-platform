# 助手会话删除守卫：有未完成动作/进行中轮次拒绝硬删除（#1223）

Status: implemented
Class: bug-fix

## Decision

#1223（R13-F11）：`DELETE /ai-assistant/sessions/{id}` 不检查活动轮次或动作、
不停止后台执行，直接删消息 + action + session——仍在跑的子进程结束后
`_finalize_action` 找不到 action 只能打 error 日志（"ai_action_finalize_missing"），
执行结果、操作卡回执与续轮全部丢失。

修复（issue 两案取「拒绝硬删除」——软删除需要全链路查询过滤，改动面大且
本单无此需求）：

- 删除前两道守卫，任一命中 → 409：
  1. `SESSION_HAS_ACTIVE_ACTIONS`：会话下存在 `proposed / approved / running`
     状态的动作——background 执行稍后要回写，删了就孤儿化；
  2. `SESSION_TURN_IN_FLIGHT`：会话下存在 `pending / running` 的 assistant
     消息——轮次正在 LLM 编排中，删除会撕掉正在写回的消息；
- 终态动作（succeeded / failed / cancelled / rejected / expired）不阻塞清理，
  会话历史照常可删。

与 ADR-0033 的关系：无直接约束（平台自研助手域）。

## Alternatives

- 软删除（deleted_at 标记 + 全查询过滤）：能保留执行记录，但所有会话/消息/
  动作查询都要加过滤条件，且「已删会话的孤儿子进程」仍然无人收口——拒绝式
  把问题挡在删除入口，剩余场景（用户执意要删）可先审批/拒绝动作后自然可删；
- 删除时顺带 cancel running 动作：取消语义依赖 console_run_id 的进程内路由
  （#1222 的坑——跨 worker 取消不可靠），在删除路径叠一个不可靠取消只会把
  两个问题缠在一起；
- 只守 action 不守消息：进行中轮次的 assistant 占位消息同样会被删，轮次
  收尾时照样找不到会话。

## Verification

- `pytest backend/tests/api/test_ai_assistant_endpoints.py`：40 passed，新增
  4 例——running 动作阻塞（409 + 会话仍在）/ proposed 阻塞 / pending 轮次
  阻塞（独立 code）/ 五种终态动作不阻塞、删除后动作与会话全部清除；
- `ruff check backend/ tools/ scripts/` 全绿。

## Revisit

- 校验与删除之间仍存在窗口（worker 恰在此间隙创建动作/写 pending 消息）——
  完全闭合需要会话行锁与轮次启动互斥（`_touch_session` 与 turn 启动都在
  动 session），若线上出现竞态复现，下一步是 `SELECT ... FOR UPDATE` 会话行；
- #1222（取消接口假成功）与本单同域相邻：先有可靠取消，才能讨论「删除时
  顺带取消」的合并语义；
- 前端收到 409 时应引导用户先处理操作卡——UI 侧提示不在本单。
