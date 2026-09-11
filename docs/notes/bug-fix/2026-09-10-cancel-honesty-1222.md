# 助手动作取消的诚实化（#1222）

Status: implemented
Class: bug-fix

## Decision

#1222（R13-F10）：`POST /actions/{id}/cancel` 对 running 动作——service 型
（无 `console_run_id`）直接跳过取消仍返回 200；runconsole 型调用
`RunConsole.cancel()` 但忽略返回值——cancel False（本进程找不到 run：已结束被
finalize 竞速、或 run 由其他 worker 执行——RunConsole 是进程内单例）同样 200。
慢服务操作 / 跨 worker 场景出现「假取消反馈」。

修复（issue 建议「检查实际结果」落地；「路由到执行所属进程」留 Revisit）：

- **service 型动作**（无进程句柄）：409 `ACTION_NOT_CANCELLABLE`——如实声明
  不可取消，不假成功；
- **runconsole 型**：检查 `cancel()` 返回值——False 时复核 action 状态：
  - 仍 `running` → 409 `CANCEL_NOT_ROUTABLE`（本进程无法路由，稍后刷新看终态）；
  - 已被 finalize 竞速落终态（如 CANCELED）→ 200 如实返回终态；
- True → 200，响应体 `status` 为 refresh 时点的真实状态（cancelled 或仍在
  running 的收尾窗口），不再无条件宣称成功；
- 审计只在真正发起/确认取消后记录。

与 ADR-0033 的关系：无直接约束（平台自研助手域）。取消能力「声明」机制
（ToolSpec 增 `cancellable` 字段）留 Revisit——当前 service 工具一律不可取消，
409 文案已承载该语义。

## Alternatives

- 跨进程取消路由（action 记录 worker id + Redis pub/sub 转发）：ADR-0027 多
  worker 尚未部署，单 worker 现状下是提前建设——先诚实报错，路由实现随
  多实例落地另立单；
- service 型动作用协作式取消标志（线程 event）：`_run_service_tool` 内部是
  黑盒调用（通知/扫描等），逐工具改造 invasive，收益低；
- 取消后轮询等待终态再返回：把 API 拉长成等待，前端已有操作卡状态轮询。

## Verification

- `pytest backend/tests/api/test_ai_assistant_endpoints.py`：40 passed，新增
  4 例——service 型 409 `ACTION_NOT_CANCELLABLE` 且状态不变 / cancel False +
  仍 running → 409 `CANCEL_NOT_ROUTABLE` / cancel False 但 finalize 竞速落
  终态 → 200 返回 cancelled / cancel True → 200 状态如实；
- `ruff check backend/ tools/ scripts/` 全绿。

## Revisit

- 跨 worker 取消路由：ADR-0027 多实例落地时随会话亲和/进程注册表一并设计
  （action 记录 owner worker + Redis 转发），本单只保证单 worker 内诚实；
- ToolSpec 增加 `cancellable` 声明字段：当出现可取消的 service 工具时再加
  （schema 变更走 #787 同类收口）；
- 前端对 409 两个 code 的提示文案（UI 另开单）。
