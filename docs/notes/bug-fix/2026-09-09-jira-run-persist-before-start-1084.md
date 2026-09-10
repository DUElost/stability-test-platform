# JIRA run 先落库后启动：快速回调不再产生永久 RUNNING（#1084）

Status: implemented
Class: bug-fix

## Decision

#1084（R10-F16，设计风险）：`start_jira_run` 先 `RunConsole.start()` 起子进程、
后 INSERT `jira_run` 行（RUNNING）。子进程秒级结束时，`_on_jira_run_complete`
回调（reader 线程）早于 INSERT 执行 → 找不到行记 warning 跳过 → 行随后仍以
RUNNING 落库且**再无人更新**：持久记录永久 RUNNING、缺 issue_keys。代码原注释
「可接受」被本轮否决。

修复（issue 首选方案：**启动前先落库**）：

- 路由预生成 `console_run_id`（`con-<uuid12>`）→ **先 INSERT**（RUNNING）→
  再 `RunConsole.start(..., run_id=console_run_id)`；
- `RunConsole.start` 增加可选 `run_id` 参数（缺省仍自生成，向后兼容）；提供值
  撞已有 run 时抛 `RunConsoleError`——幂等键冲突在 spawn 前就失败，不留半态；
- start 失败（RunKeyBusy 409 / RunConsoleError 500）→ 新增
  `_mark_jira_run_not_started` 把已落库的行回写 **FAILED**（带 error +
  ended_at），不留悬挂 RUNNING；
- INSERT 自身失败（DB 不可用）保持旧行为：记日志、照常启动（历史记录缺失，
  回调同样找不到行、按既有 warning 跳过）。

## Alternatives

- 回调里找不到行时重试/轮询等待 INSERT：治标——重试窗口仍是猜的，DB 真不可用
  时白等；先写后启让竞态在顺序上不存在；
- 回调找不到行时**自建**行：回调只有 run 上下文，缺 vendor/stage/user 等字段，
  会写出残缺历史；
- 维持现状 + 文档「接受」：永久 RUNNING 会在列表页误导运营（看起来还在跑），
  且丢 issue_keys 影响下游关联。

## Verification

- `pytest backend/tests/api/test_dedup_jira_endpoints.py`：31 passed。新增 2 例
  ——`test_fast_completion_finds_row`：fake start 在 spawn 前断言行已 RUNNING、
  同步触发 `_on_jira_run_complete` 后行落 SUCCESS（旧顺序下该用例找不到行，
  行停留 RUNNING）；`test_start_failure_marks_row_failed`：RunKeyBusyError →
  409 且行回写 FAILED 带 error；
- `RunConsole.start` 契约扩展对既有用例无感（run_id 缺省自生成）；
  `test_run_console.py` + dedup scan 端点 32 passed；
- ruff 干净。

## Revisit

- `RunConsole.start(run_id=...)` 是通用能力：其他「spawn 后落库」的消费者
  （后续同类运维工具）应直接复用该模式，而不是复制竞态；
- 历史上已悬挂的 RUNNING 行不在本单修复范围——如需对账，可按
  `console_run_id` 不存在于 console 日志目录或 created_at 超期批量关账，另立单；
- INSERT 与 start 之间进程崩溃会留下 RUNNING 行（无子进程在跑）：与崩溃遗留
  的孤儿 run 同类，暂按既有低概率接受；若要闭环需启动时对账，另立单。
