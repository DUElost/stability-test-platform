# AI 助手续轮汇报可见性（pending 占位收口的第二轮修正）

- 日期：2026-08-29
- 类型：bug-fix
- 关联：[ADR-0031](../../adr/ADR-0031-platform-ai-assistant.md)、上一轮修复 PR #536（H1 pending 收口）

## 决定了什么

**pending 占位是「助手仍欠你一条回复」的唯一信号，它的生命周期必须覆盖整条续轮链，而不是单个 SAQ 轮次。**

上一轮为修 H1（占位永不收敛 → 前端无限轮询 + 气泡永挂）在轮次 `finally` 无条件收口，
但止轮等待动作执行时也一并删掉了占位。前端 `AssistantPage` 的 messages 查询
**只在有 pending/running 消息时**才 2s 轮询，而全局 `refetchOnWindowFocus: false`
（`QueryProvider.tsx`）——没有兜底刷新。于是"跑门禁 → 汇报结果"这条主链路的最后一步
（续轮写入的汇报）只落库、不上屏，要切会话或刷页面才看得到。

四处改动：

1. **占位跨续轮存活**（`orchestrator.ai_assistant_turn_task`）：新增 `awaiting_continuation`——
   因自动执行止轮时保留占位（`ensure_pending_placeholder`），由续轮自己收口；其余路径维持
   上一轮的收口语义（产出回复→删除、异常→failed 留痕）。
2. **续轮入队即落占位**（`_enqueue_continuation`）：入队前 `ensure_pending_placeholder`（幂等），
   入队失败立刻把占位标 `failed` 并写明原因，不留悬挂。
3. **内联终态不再谎报「已开始执行」**：`execute_action` 返回后 `db.refresh(action)`，若已是终态
   （服务型工具直接跑完 / `RunKeyBusyError` / spawn 失败），把真实结果作为 tool 结果喂回模型并
   **继续本轮**——因为此刻发起的续轮与本轮 SAQ job 同 key（`ai-turn:{sid}`），`saq.Queue.enqueue`
   对已存在的 key 返回 `None`（静默丢弃），指望续轮汇报等于汇报丢失。
4. **审批放行时落占位**（`routes/ai_assistant._decide_action`）：approve 的执行结果同样经续轮汇报，
   前端 approve 成功后 invalidate messages，据占位恢复轮询。
   前端 `ActionCard` 另加一次终态即时刷新（动作卡结果与助手汇报同时到位）。

顺带：`call_agent_control_sync` 补「只能从非主循环线程调用」的约束说明，并去掉构造期
`except RuntimeError` 死分支（sio 未初始化的异常由协程体抛出，在 `future.result()` 收口）。

## 放弃的备选

- **纯前端修**（ActionCard 终态 invalidate messages 作为唯一机制）：拒绝为主机制。它要求前端
  恰好挂着那张卡且轮询没被中断；后端占位是自驱动的，页面刷新/换设备后依然成立。前端那次
  invalidate 保留为锦上添花，不是正确性依赖。
- **给续轮换独立 job key 规避 SAQ 去重**：拒绝。key 的作用正是「同会话轮次串行」，换 key 等于
  允许两轮并发写同一会话消息流。改为在本轮内消化内联终态。
- **止轮时删占位、由 `_finalize_action` 重建**：拒绝。重建的占位落在停轮之后，前端根本不会去取，
  等于没建；且引入「谁先跑完」的竞态。

## 如何验证

- 新增 4 条回归（`backend/tests/api/test_ai_assistant_endpoints.py::TestContinuationVisibility`），
  **逐条构造反例证明会红**：把 `finally` 改回无条件收口 → 占位保留用例红；把内联终态判定短路
  → 谎报用例红；去掉入队失败收口 → 悬挂用例红；去掉 approve 落占位 → 审批用例红。
- AI 相关后端 63 用例全过（testcontainers PG）；`ruff` 绿；前端 `tsc` / `eslint --max-warnings 0` /
  assistant vitest 全过。
- 生产库只读核对（修复前基线）：`ai_chat_message` 曾有 5 条 `role=assistant, status=pending`
  滞留，PR #536 后归零——本轮解决的是它引出的第二个问题，不是回退它。

## 何时重议

- 若 v2 引入流式输出或全局抽屉，"占位驱动轮询"这套机制应整体让位给流式/推送，
  届时本文四处改动一并作废。
- 若 T2 白名单开始启用（`auto_approve_tools` 非空），服务型工具走内联终态路径的频率会上升，
  届时复看第 3 点的措辞是否还够（当前把结果原样喂回模型，未做结构化）。
