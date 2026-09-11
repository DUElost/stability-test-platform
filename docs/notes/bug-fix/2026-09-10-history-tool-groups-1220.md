# 持久化历史按完整工具交互组修复（#1220）

Status: implemented
Class: bug-fix

## Decision

#1220（R13-F08）：`_history_as_llm_messages` 按 `HISTORY_LIMIT=20` 行数截断，
可能把工具交互组拦腰切断——留下没有前置 assistant(tool_calls) 的孤儿 tool 消息
（严格供应商 400）；内联服务完成路径还会形成
assistant(tool_calls) → user(执行回执) → tool(response) 的错误顺序（严格校验
要求 tool 紧跟 assistant）。

修复：新增 `_sanitize_tool_history` 组装后置修复（规则 = 按完整交互组）：

- assistant(tool_calls) 的**全部响应都在窗口内** → 响应前移为紧随其后的组
  （中间插入的用户回执保留在组后）——严格校验要求 tool 消息紧跟 assistant；
- **任一响应缺失**（前驱或响应被截断）→ 该 assistant 丢弃 `tool_calls`
  （content 也为空则整条丢弃），已入库的响应转 user 回执——「组完整或整组
  丢弃」的落地形态；
- **无前驱的 tool 消息**（前驱被截断在窗口外）→ 转 user `[执行回执]`，
  内容不丢；
- 同 id 只消费一次（跨 assistant 的重复 call id 不产生重复输出）。

与 ADR-0033 的关系：无直接约束（平台自研助手域）。

## Alternatives

- 改 DB 写入顺序（内联回执等组齐再落库）：动写入路径影响面大（前端实时推送、
  状态机），且截断问题的根因在读取侧——修复读侧一处即可覆盖历史存量；
- 提高 HISTORY_LIMIT 让组不被切断：只是推迟，长会话必然再触；且窗口越大
  token 成本越高；
- 孤儿 tool 直接丢弃：丢内容——转 user 回执保留信息且协议合法。

## Verification

- `pytest backend/tests/api/test_ai_assistant_endpoints.py`：52 passed，新增
  4 例——孤儿 tool（前驱被 20 行截断）转 user 回执且内容保留 / 组间 user 回执
  被重排为 assistant→tool→user / 缺响应时 assistant 丢 tool_calls 保留
  content、已有响应转 user / 完整交互组正常路径回归；
- `ruff check backend/ tools/ scripts/` 全绿。

## Revisit

- 修复在读取侧（每次组装重算），历史存量无需迁移；
- 若供应商进一步要求「tool 响应内容必须匹配 schema」级别的校验，需在
  `_sanitize_tool_history` 扩展——当前只保证结构性协议合法；
- `HISTORY_LIMIT=20` 的窗口大小是 token 预算问题，与本单正交。
