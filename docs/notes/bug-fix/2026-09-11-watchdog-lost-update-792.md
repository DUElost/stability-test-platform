# session_watchdog 转 UNKNOWN 加行锁复读（#792）

Status: implemented
Class: bug-fix

## Decision

`session_watchdog._check_host_heartbeat_timeouts` 是全仓唯一裸写终态路径：
无锁 `select` RUNNING job 后按陈旧 ORM 对象 `JobStateMachine.transition`
UNKNOWN，外层统一 commit。窗口内 `/complete` 提交 COMPLETED 时，watchdog
用旧视图 flush 把行覆写回 UNKNOWN（lost update）——grace 后转 FAILED，
已完成事实永久矛盾、设备结果丢失。

修复（对齐 reconciler/recycler/`/complete` 既有模式）：逐 job `FOR UPDATE`
复读 + `populate_existing=True` 刷新 + **仍 RUNNING 才 transition**；
非 RUNNING/行不存在即跳过。`/complete` 与 watchdog 由行锁串行，后到者
的状态复查使其退化为 no-op。

## Alternatives

- **CAS `UPDATE ... WHERE status='RUNNING' RETURNING`**——放弃：状态机
  transition（InvalidTransitionError 语义、execution_state 清理）在 ORM
  层，写法与同仓先例（#989/#993 同款「锁复读+复查」）不一致；
- **整批候选查询直接带 FOR UPDATE**——放弃：长持锁放大 `/complete` 等待；
  逐行短锁（单行复读距离）与 #987 裁决一致。

## Verification

- **反例实验未成立（如实记录）**：回退实现保留新用例 → 用例仍通过。
  原因：候选查询自带 `status == RUNNING` 过滤，测试构造的「查询前已提交
  COMPLETED」场景旧实现亦安全；真正的窗口在**查询后、commit 前**，黑盒
  无法确定性注入（修复后的行锁会让注入的并发提交阻塞，同一注入不可
  同时覆盖新旧两版）。本用例保留为「过滤语义未被移除」的回归。
- 修复语义依据：窗口分析（watchdog 快照对象 vs `/complete` 提交）+ 同源
  先例（`agent_api` complete、reconciler、recycler 均为锁/CAS）；本仓
  #987/#989/#993 已用同一「锁复读+条件复查」模式处理同族缺陷。
- `test_session_watchdog.py` 全套 3 passed（含 host 超时正常转 UNKNOWN 与
  lease 保持 ACTIVE 的既有回归）；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- 若未来引入「watchdog 与 /complete 真并发」的集成测试基建（双实例 +
  可控停顿），可把窗口场景纳入；当前以结构对齐与先例背书；
- host 判定（OFFLINE 翻转）未加行锁：心跳并发复活时下一 tick 自愈，
  暂无 lost-update 的终态后果（区别于 job 终态）。
