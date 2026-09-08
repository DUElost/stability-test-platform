# R07-F04 落地：终态上报三分语义，双故障保留恢复依据（#1005）

Status: implemented
Class: bug-fix

## Decision

`complete_job` 原实现把 `enqueue_terminal()` 失败只打 warning 吞掉；HTTP
`/complete` 失败时仅因 `local_db` 对象存在就按「已交给 outbox」返回——
即使 outbox 实际未写入。worker 正常清理（`_cleanup_after_job_exit` →
`delete_active_job`）随后删除 active 记录：远端无终态、本地无 outbox、
恢复入口也被删，终态静默丢失。

修复为显式三分，`TerminalReportLostError` 标记第三种：

1. **`api_client.complete_job`**：跟踪 `outbox_ok`（enqueue 成功与否）。
   HTTP 成功 → ack（仅 outbox_ok 时）；HTTP 失败 + outbox_ok → deferred
   返回（现状正确语义）；HTTP 失败 + !outbox_ok → error 日志 +
   raise `TerminalReportLostError`（不再伪称 deferred）；
2. **`job_runner.run_task_wrapper`**：`except TerminalReportLostError:
   raise`（位于通用 `except Exception` 之前）——不得把双故障包装成
   AGENT_ERROR 二次上报：会覆盖真实脚本结果，且同样故障下必然再次失败；
3. **`main._cleanup_after_job_exit`**：`delete_active_job` 加守卫——outbox
   有该 job 行（acked 或 pending，`local_db.has_terminal_fact(job_id)` 新
   增方法）才删除恢复依据；双故障无行 → 保留 active 记录 + error 日志。
   远端确认与 deferred 两路都写过 outbox 行，语义不受影响。

自愈路径：双故障保留 active 后，租约续期继续到后端超时/reaper 或下次
recovery 收敛——终态不会在三条链上同时消失。

## Alternatives

- **enqueue 失败后立即重试一次（瞬时 SQLite 锁场景）**——放弃：outbox
  drain 已有重试通道，且重试无法覆盖「SQLite 真不可写」；三分语义把
  失败显式化后由恢复链路兜底更清晰；
- **双故障时把 complete_payload 也持久化（扩 active 表 schema）**——放弃：
  Agent 本地 SQLite 无迁移通道，且该组合（本地写失败+远端不可达）极罕见；
  保留 active 让外层 recovery/后端超时收敛已满足「不静默丢失」；
- **delete_active_job 改为由 complete_job 显式触发**——放弃：删除点遍布
  recovery 动作与 release 链，语义重组面大；在既有删除点加
  has_terminal_fact 守卫是单点收口。

## Verification

- **反例实证**：回退四个实现文件保留测试 → 新测试文件收集即失败
  （`TerminalReportLostError` 不存在）；修复版全绿；
- 新增用例：
  - `test_terminal_durability.py` 7 例：双故障 raise 且不 ack / deferred
    不 raise / HTTP ok 的 ack 条件 / 无 local_db 原语义 / 守卫保留与删除
    两向 + has_terminal_fact 参数断言；
  - `test_fencing_token.py` 端到端 1 例：run_task_wrapper 双故障 →
    `TerminalReportLostError` 透传，HTTP 仅尝试一次（不二次 AGENT_ERROR）；
- `backend/agent/tests/` 全目录 **1446 passed**（2m15s）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 双故障后 active 记录靠后端租约超时/下次 recovery 收敛——同 boot 无
  主动补报通道（complete_payload 只在日志）；若该组合在实践中出现频率
  高，需补「保留 payload 重试上报」通道（扩 schema 或内存重试队列）；
- #1004（cursor 在窗）修重启恢复「有终态不得再执行」——本单保留的
  active 记录届时如何表达「已有未上报终态」需与 #1004 的 outbox 优先
  协议对证；
- `has_terminal_fact` 覆盖 acked 与 pending 两态：acked 行保留 100 条
  （prune 阈值），prune 后远端确认过的旧 job 不再有行——但远端已确认
  意味着删除本就安全，无回归风险。
